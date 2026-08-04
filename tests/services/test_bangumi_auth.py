"""Bangumi OAuth 认证服务单元测试。

BangumiAuthService 已切换到 DB accounts 为唯一真相源，本测试用内存 dict
模拟 ``app.core.accounts`` 的 DB 访问层，并 mock httpx.post 模拟令牌端点。
"""

from unittest.mock import MagicMock, patch

import pytest

from app.core.database import database_manager as _dbm
from app.services.bangumi.auth import BangumiAuthService


class _FakeAccountStore:
    """内存版 DB 账号存储，模拟 app.core.accounts 的访问层。"""

    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}

    def get_active(self):
        for acc in self.accounts.values():
            if acc.get("is_active"):
                return dict(acc)
        # 无激活时取首个
        return dict(next(iter(self.accounts.values()))) if self.accounts else None

    def get(self, section_name):
        acc = self.accounts.get(section_name)
        return dict(acc) if acc else None

    def save(self, account):
        section = account.get("section_name")
        if not section:
            return False
        self.accounts[section] = dict(account)
        return True

    def set_active(self, section_name):
        if section_name not in self.accounts:
            return False
        for s in self.accounts:
            self.accounts[s]["is_active"] = s == section_name
        return True


@pytest.fixture
def svc(monkeypatch):
    """构造 BangumiAuthService，DB 访问层用内存 store 替身。"""
    fake_oauth_cfg = {
        "bangumi-oauth": {
            "client_id": "app-id",
            "client_secret": "app-secret",
            "redirect_uri": "http://localhost:8000/api/bangumi/oauth/callback",
        }
    }

    def _fake_get(section, option, *, raw=False, fallback=None):
        return fake_oauth_cfg.get(section, {}).get(option, fallback)

    store = _FakeAccountStore()

    # mock config_manager.get（仅用于读取 [bangumi-oauth] 应用凭证）
    monkeypatch.setattr("app.services.bangumi.auth.config_manager.get", _fake_get)
    # mock app.core.accounts 的 DB 访问函数
    monkeypatch.setattr(
        "app.services.bangumi.auth.get_active_bangumi_account", store.get_active
    )
    monkeypatch.setattr("app.services.bangumi.auth.get_bangumi_account", store.get)
    monkeypatch.setattr("app.services.bangumi.auth.save_bangumi_account", store.save)
    monkeypatch.setattr(
        "app.services.bangumi.auth.set_active_bangumi_account", store.set_active
    )

    # OAuth state 统一落库；测试中以内存替身隔离真实数据库
    state_store: dict = {}

    def _save_state(state, section_name, expires_at, provider="", redirect_uri=""):
        state_store[state] = {
            "provider": provider,
            "section_name": section_name,
            "expires_at": expires_at,
            "redirect_uri": redirect_uri,
        }

    def _get_state(state):
        return state_store.get(state)

    def _del_state(state):
        return state_store.pop(state, None) is not None

    monkeypatch.setattr(_dbm, "save_oauth_state", _save_state)
    monkeypatch.setattr(_dbm, "get_oauth_state", _get_state)
    monkeypatch.setattr(_dbm, "delete_oauth_state", _del_state)

    service = BangumiAuthService()
    return service, store


def _mock_token_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def test_get_auth_url_contains_client_and_state(svc):
    svc, store = svc
    url, state = svc.get_auth_url()
    assert "client_id=app-id" in url
    assert "state=" in url and "redirect_uri=" in url
    assert svc.verify_state(state) is True
    assert svc.verify_state("not-a-real-state") is False


def test_get_auth_url_accepts_dynamic_redirect_uri(svc):
    """前端动态传入 redirect_uri 时应透传到授权 URL 并绑定到 state。"""
    from urllib.parse import parse_qs, urlparse

    svc, store = svc
    custom = "http://192.168.1.10:8000/api/oauth/bangumi/callback"
    url, state = svc.get_auth_url(redirect_uri=custom)
    # 授权 URL 中应包含动态 redirect_uri
    assert "redirect_uri=" in url
    qs = parse_qs(urlparse(url).query)
    assert qs["redirect_uri"] == [custom]


def test_exchange_code_persists_token_to_db(svc):
    svc, store = svc
    _, state = svc.get_auth_url()
    token = {
        "access_token": "AT",
        "refresh_token": "RT",
        "expires_in": 3600,
        "token_type": "Bearer",
        "user_id": "myname",
    }
    with patch("httpx.post", return_value=_mock_token_response(token)) as m:
        result = svc.exchange_code_for_token("the-code", state)

    assert result["access_token"] == "AT"
    # token 应写入 DB（store），而非 INI；section_name 按 user_id 生成
    acc = store.get("bangumi-myname")
    assert acc is not None
    assert acc["access_token"] == "AT"
    assert acc["refresh_token"] == "RT"
    assert acc["auth_method"] == "oauth"
    assert acc["bangumi_user_id"] == "myname"
    assert int(acc["expires_at"]) > 0
    # 新建账号授权成功后自动激活
    assert acc["is_active"] is True
    m.assert_called_once()
    # state 已被消费
    assert svc.verify_state(state) is False


def test_exchange_code_rejects_bad_state(svc):
    svc, _ = svc
    with patch("httpx.post") as m:
        with pytest.raises(ValueError):
            svc.exchange_code_for_token("code", "bogus-state")
    m.assert_not_called()


def test_refresh_active_token(svc):
    svc, store = svc
    # 预置一个 oauth 账号并激活
    store.save(
        {
            "section_name": "bangumi",
            "username": "u",
            "auth_method": "oauth",
            "refresh_token": "OLD_RT",
            "access_token": "OLD_AT",
            "media_server_usernames": [],
            "private": False,
            "is_active": True,
        }
    )
    new_token = {"access_token": "NEW_AT", "expires_in": 7200, "token_type": "Bearer"}
    with patch("httpx.post", return_value=_mock_token_response(new_token)) as m:
        ok = svc.refresh_active_token()
    assert ok is True
    assert store.get("bangumi")["access_token"] == "NEW_AT"
    m.assert_called_once()
    sent = m.call_args.kwargs["data"]
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "OLD_RT"


def test_refresh_active_token_missing_refresh(svc):
    svc, store = svc
    store.save(
        {
            "section_name": "bangumi",
            "username": "u",
            "auth_method": "oauth",
            "refresh_token": "",
            "access_token": "AT",
            "media_server_usernames": [],
            "private": False,
            "is_active": True,
        }
    )
    with patch("httpx.post") as m:
        assert svc.refresh_active_token() is False
    m.assert_not_called()


def test_refresh_active_token_if_needed_only_for_oauth(svc):
    svc, store = svc
    # 默认 manual：不应触发网络请求
    store.save(
        {
            "section_name": "bangumi",
            "username": "u",
            "auth_method": "manual",
            "access_token": "AT",
            "refresh_token": "RT",
            "media_server_usernames": [],
            "private": False,
            "is_active": True,
        }
    )
    with patch("httpx.post") as m:
        assert svc.refresh_active_token_if_needed() is False
    m.assert_not_called()

    # 设为 oauth 且已过期：应触发刷新
    store.accounts["bangumi"]["auth_method"] = "oauth"
    store.accounts["bangumi"]["expires_at"] = 1  # 过去的时间戳，视为已过期
    with patch("httpx.post", return_value=_mock_token_response({"access_token": "X"})):
        assert svc.refresh_active_token_if_needed() is True


def test_connection_status(svc):
    svc, store = svc
    # 无账号：未连接
    assert svc.get_connection_status()["connected"] is False

    # 有 oauth 账号：已连接
    store.save(
        {
            "section_name": "bangumi",
            "username": "u",
            "auth_method": "oauth",
            "access_token": "AT",
            "bangumi_user_id": "123",
            "expires_at": 9999999999,
            "media_server_usernames": [],
            "private": False,
            "is_active": True,
        }
    )
    status = svc.get_connection_status()
    assert status["connected"] is True
    assert status["username"] == "u"
    assert status["user_id"] == "123"
    assert status["expired"] is False


def test_refresh_if_needed_concurrent_no_duplicate_refresh(svc):
    """并发场景下 refresh_active_token_if_needed 只应刷新一次。

    模拟多线程并发：第一个线程持锁刷新后更新 expires_at，第二个线程
    持锁后 double-check 发现不再临近过期，跳过刷新。
    """
    import threading

    svc, store = svc
    store.save(
        {
            "section_name": "bangumi",
            "username": "u",
            "auth_method": "oauth",
            "access_token": "OLD_AT",
            "refresh_token": "RT",
            "expires_at": 1,  # 已过期
            "media_server_usernames": [],
            "private": False,
            "is_active": True,
        }
    )

    call_count = 0
    original_post_response = _mock_token_response(
        {"access_token": "NEW_AT", "expires_in": 7200, "token_type": "Bearer"}
    )

    def _counting_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_post_response

    barrier = threading.Barrier(2)

    def _worker():
        barrier.wait()
        svc.refresh_active_token_if_needed()

    with patch("httpx.post", side_effect=_counting_post):
        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    # 并发下只应调用一次 OAuth refresh 接口（锁 + double-check 生效）
    assert call_count == 1
    assert store.get("bangumi")["access_token"] == "NEW_AT"


def test_disconnect_keeps_access_token_for_manual_fallback(svc):
    svc, store = svc
    store.save(
        {
            "section_name": "bangumi",
            "username": "u",
            "auth_method": "oauth",
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_at": 123,
            "token_type": "Bearer",
            "media_server_usernames": [],
            "private": False,
            "is_active": True,
        }
    )

    svc.disconnect()

    acc = store.get("bangumi")
    assert acc["auth_method"] == "manual"
    assert acc["refresh_token"] == ""
    assert acc["expires_at"] is None
    # 降级手动：保留 access_token
    assert acc["access_token"] == "AT"


def test_redirect_uri_from_config(svc):
    svc, _ = svc
    assert svc.get_redirect_uri() == "http://localhost:8000/api/bangumi/oauth/callback"


def test_redirect_uri_override(svc):
    svc, _ = svc
    # 通过修改 fake_oauth_cfg 来覆盖 redirect_uri
    # 由于 _fake_get 闭包捕获 fake_oauth_cfg，直接修改其内容即可
    # 但 fixture 中 fake_oauth_cfg 是局部变量，这里改用直接 patch
    with patch(
        "app.services.bangumi.auth.config_manager.get",
        return_value="https://example.com/cb",
    ):
        assert svc.get_redirect_uri() == "https://example.com/cb"
