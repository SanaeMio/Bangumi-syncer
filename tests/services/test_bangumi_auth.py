"""Bangumi OAuth 认证服务单元测试。

使用内存版 config 替身隔离配置读写，并 mock httpx.post 模拟 Bangumi 令牌端点。
"""

from unittest.mock import MagicMock, patch

import pytest

from app.core.database import database_manager as _dbm
from app.services.bangumi.auth import BangumiAuthService


class _FakeConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, section, option, *, raw=False, fallback=None):
        return self.data.get(section, {}).get(option, fallback)

    def set(self, section, option, value):
        self.data.setdefault(section, {})[option] = str(value)

    def get_user_mappings(self):
        return {}


@pytest.fixture
def svc(monkeypatch):
    fake = _FakeConfig(
        {
            "bangumi-oauth": {
                "client_id": "app-id",
                "client_secret": "app-secret",
                "redirect_uri": "http://localhost:8000/api/bangumi/oauth/callback",
            },
            "bangumi": {
                "auth_method": "manual",
                "access_token": "",
                "refresh_token": "",
                "expires_at": "0",
            },
            "sync": {"mode": "single"},
        }
    )
    svc = BangumiAuthService()
    monkeypatch.setattr("app.services.bangumi.auth.config_manager.get", fake.get)
    monkeypatch.setattr("app.services.bangumi.auth.config_manager.set", fake.set)
    monkeypatch.setattr(
        "app.services.bangumi.auth.config_manager.get_user_mappings",
        fake.get_user_mappings,
    )
    # OAuth state 统一落库；测试中以内存替身隔离真实数据库
    store: dict = {}

    def _save(state, section_name, expires_at, provider=""):
        store[state] = {
            "provider": provider,
            "account_key": section_name,
            "expires_at": expires_at,
        }

    def _get(state):
        return store.get(state)

    def _del(state):
        return store.pop(state, None) is not None

    monkeypatch.setattr(_dbm, "save_oauth_state", _save)
    monkeypatch.setattr(_dbm, "get_oauth_state", _get)
    monkeypatch.setattr(_dbm, "delete_oauth_state", _del)
    return svc, fake


def _mock_token_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def test_get_auth_url_contains_client_and_state(svc):
    svc, fake = svc
    url, state = svc.get_auth_url()
    assert "client_id=app-id" in url
    assert "state=" in url and "redirect_uri=" in url
    assert svc.verify_state(state) is True
    assert svc.verify_state("not-a-real-state") is False


def test_exchange_code_persists_token(svc):
    svc, fake = svc
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
    assert fake.data["bangumi"]["access_token"] == "AT"
    assert fake.data["bangumi"]["refresh_token"] == "RT"
    assert fake.data["bangumi"]["auth_method"] == "oauth"
    assert fake.data["bangumi"]["username"] == "myname"
    assert int(fake.data["bangumi"]["expires_at"]) > 0
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
    svc, fake = svc
    fake.data["bangumi"]["auth_method"] = "oauth"
    fake.data["bangumi"]["refresh_token"] = "OLD_RT"
    fake.data["bangumi"]["access_token"] = "OLD_AT"
    new_token = {"access_token": "NEW_AT", "expires_in": 7200, "token_type": "Bearer"}
    with patch("httpx.post", return_value=_mock_token_response(new_token)) as m:
        ok = svc.refresh_active_token()
    assert ok is True
    assert fake.data["bangumi"]["access_token"] == "NEW_AT"
    m.assert_called_once()
    # 刷新时携带 refresh_token
    sent = m.call_args.kwargs["data"]
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "OLD_RT"


def test_refresh_active_token_missing_refresh(svc):
    svc, fake = svc
    fake.data["bangumi"]["refresh_token"] = ""
    with patch("httpx.post") as m:
        assert svc.refresh_active_token() is False
    m.assert_not_called()


def test_refresh_active_token_if_needed_only_for_oauth(svc):
    svc, fake = svc
    # 默认 manual：不应触发网络请求
    with patch("httpx.post") as m:
        assert svc.refresh_active_token_if_needed() is False
    m.assert_not_called()

    # 设为 oauth 且已过期：应触发刷新
    fake.data["bangumi"]["auth_method"] = "oauth"
    fake.data["bangumi"]["refresh_token"] = "RT"
    fake.data["bangumi"]["access_token"] = "AT"
    fake.data["bangumi"]["expires_at"] = "1"  # 过去的时间戳，视为已过期
    with patch("httpx.post", return_value=_mock_token_response({"access_token": "X"})):
        assert svc.refresh_active_token_if_needed() is True


def test_connection_status(svc):
    svc, fake = svc
    assert svc.get_connection_status()["connected"] is False

    fake.data["bangumi"]["auth_method"] = "oauth"
    fake.data["bangumi"]["access_token"] = "AT"
    fake.data["bangumi"]["username"] = "u"
    fake.data["bangumi"]["expires_at"] = "9999999999"
    status = svc.get_connection_status()
    assert status["connected"] is True
    assert status["username"] == "u"
    assert status["expired"] is False


def test_disconnect_keeps_access_token_for_manual_fallback(svc):
    svc, fake = svc
    fake.data["bangumi"]["auth_method"] = "oauth"
    fake.data["bangumi"]["access_token"] = "AT"
    fake.data["bangumi"]["refresh_token"] = "RT"
    fake.data["bangumi"]["expires_at"] = "123"
    fake.data["bangumi"]["token_type"] = "Bearer"

    svc.disconnect()

    assert fake.data["bangumi"]["auth_method"] == "manual"
    assert fake.data["bangumi"]["refresh_token"] == ""
    assert fake.data["bangumi"]["expires_at"] == ""
    # 降级手动：保留 access_token
    assert fake.data["bangumi"]["access_token"] == "AT"


def test_redirect_uri_from_config(svc):
    svc, fake = svc
    assert svc.get_redirect_uri() == "http://localhost:8000/api/bangumi/oauth/callback"


def test_redirect_uri_override(svc):
    svc, fake = svc
    fake.data["bangumi-oauth"]["redirect_uri"] = "https://example.com/cb"
    assert svc.get_redirect_uri() == "https://example.com/cb"
