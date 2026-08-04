"""TraktAuthService：配置校验、OAuth 状态、令牌交换/刷新（httpx mock）。"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.database import database_manager as _dbm
from app.models.trakt import TraktCallbackRequest, TraktConfig
from app.services.trakt.auth import TraktAuthService


@pytest.fixture(autouse=True)
def _fake_oauth_state(monkeypatch):
    """用内存替身隔离真实 oauth_states 表，使 CSRF state 测试可复现。"""
    store: dict = {}

    def _save(state, section_name, expires_at, provider=""):
        store[state] = {
            "provider": provider,
            "section_name": section_name,
            "expires_at": expires_at,
        }

    def _get(state):
        return store.get(state)

    def _del(state):
        return store.pop(state, None) is not None

    def _cleanup():
        now = int(time.time())
        expired = [
            k for k, v in store.items() if v["expires_at"] and v["expires_at"] <= now
        ]
        for k in expired:
            store.pop(k)
        return len(expired)

    monkeypatch.setattr(_dbm, "save_oauth_state", _save)
    monkeypatch.setattr(_dbm, "get_oauth_state", _get)
    monkeypatch.setattr(_dbm, "delete_oauth_state", _del)
    monkeypatch.setattr(_dbm, "cleanup_oauth_states_expired", _cleanup)


@pytest.fixture
def svc():
    return TraktAuthService()


def _valid_trakt_cfg():
    return {
        "client_id": "cid",
        "client_secret": "sec",
        "redirect_uri": "http://localhost/cb",
        "default_sync_interval": "0 */6 * * *",
    }


class TestTraktAuthValidateAndOAuth:
    def test_validate_config_empty(self, svc):
        with patch.object(svc, "_get_config", return_value={}):
            assert svc._validate_config() is False

    def test_validate_config_missing_client_id(self, svc):
        with patch.object(
            svc,
            "_get_config",
            return_value={**_valid_trakt_cfg(), "client_id": "  "},
        ):
            assert svc._validate_config() is False

    def test_validate_config_missing_secret(self, svc):
        with patch.object(
            svc,
            "_get_config",
            return_value={**_valid_trakt_cfg(), "client_secret": ""},
        ):
            assert svc._validate_config() is False

    def test_validate_config_missing_redirect(self, svc):
        with patch.object(
            svc,
            "_get_config",
            return_value={**_valid_trakt_cfg(), "redirect_uri": ""},
        ):
            assert svc._validate_config() is False

    @pytest.mark.asyncio
    async def test_init_oauth_empty_user(self, svc):
        assert await svc.init_oauth("  ") is None

    @pytest.mark.asyncio
    async def test_init_oauth_invalid_config(self, svc):
        with patch.object(svc, "_validate_config", return_value=False):
            assert await svc.init_oauth("u1") is None

    @pytest.mark.asyncio
    async def test_init_oauth_success(self, svc):
        with patch.object(svc, "_validate_config", return_value=True):
            with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
                r = await svc.init_oauth("alice")
        assert r is not None
        assert "trakt.tv/oauth/authorize" in r.auth_url
        assert r.state

    def test_calculate_expires_at_none_and_value(self, svc):
        assert svc._calculate_expires_at(None) is None
        t0 = int(time.time())
        exp = svc._calculate_expires_at(120)
        assert exp is not None
        assert exp >= t0 + 50

    def test_extract_user_id_from_state(self, svc):
        assert svc.extract_user_id_from_state("nope") is None
        svc._save_oauth_state("bob", "st1")
        assert svc.extract_user_id_from_state("st1") == "bob"

    def test_verify_oauth_state_branches(self, svc):
        assert svc._verify_oauth_state("u", "s") is False
        svc._save_oauth_state("u", "s")
        assert svc._verify_oauth_state("u", "s") is True
        # 校验即消费：再次校验同一 state 失败
        assert svc._verify_oauth_state("u", "s") is False
        # 不同 user_id 不匹配（已消费，校验失败）
        svc._save_oauth_state("u2", "s2")
        assert svc._verify_oauth_state("other", "s2") is False

    def test_cleanup_expired_states(self, svc):
        svc._save_oauth_state("a", "a:s")
        # 注入一个已过期的 state
        _dbm.save_oauth_state("old", "x", 1, provider="trakt")
        deleted = svc._cleanup_expired_states()
        assert deleted >= 1
        assert svc.extract_user_id_from_state("a:s") == "a"  # 未过期保留
        assert svc.extract_user_id_from_state("old") is None  # 已过期清理

    def test_get_user_trakt_config(self, svc):
        with patch("app.services.trakt.auth.database_manager") as db:
            db.get_trakt_config.return_value = None
            assert svc.get_user_trakt_config("x") is None
            db.get_trakt_config.return_value = {
                "user_id": "x",
                "access_token": "t",
                "refresh_token": None,
                "expires_at": None,
                "enabled": 1,
                "sync_interval": "0 */6 * * *",
                "last_sync_time": None,
                "created_at": 1,
                "updated_at": 1,
            }
            cfg = svc.get_user_trakt_config("x")
            assert isinstance(cfg, TraktConfig)

    def test_disconnect_trakt(self, svc):
        with patch("app.services.trakt.auth.database_manager") as db:
            db.delete_trakt_config.return_value = True
            assert svc.disconnect_trakt("u") is True
            db.delete_trakt_config.return_value = False
            assert svc.disconnect_trakt("u") is False


@pytest.mark.asyncio
async def test_exchange_code_for_token_200_and_errors(svc):
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"access_token": "a"}
    ok.raise_for_status = MagicMock()

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch("httpx.post", return_value=ok):
                assert await svc._exchange_code_for_token("code") == {
                    "access_token": "a"
                }

    bad = MagicMock(status_code=400, text="err")
    bad.raise_for_status = MagicMock(side_effect=Exception("bad"))

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch("httpx.post", return_value=bad):
                assert await svc._exchange_code_for_token("c") is None

    with patch.object(svc, "_validate_config", return_value=False):
        assert await svc._exchange_code_for_token("c") is None

    async def boom():
        raise httpx.RequestError("x", request=MagicMock())

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch("httpx.post", side_effect=boom):
                assert await svc._exchange_code_for_token("c") is None


@pytest.mark.asyncio
async def test_refresh_access_token_paths(svc):
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"access_token": "n"}
    ok.raise_for_status = MagicMock()

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch("httpx.post", return_value=ok):
                assert await svc._refresh_access_token("rt") == {"access_token": "n"}

    async def boom():
        raise RuntimeError("inner")

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch("httpx.post", side_effect=boom):
                assert await svc._refresh_access_token("rt") is None


@pytest.mark.asyncio
async def test_handle_callback_branches(svc):
    cb = TraktCallbackRequest(code="c", state="st")
    with patch.object(svc, "_validate_config", return_value=False):
        r = await svc.handle_callback(cb, "u")
        assert r.success is False

    # state 校验已由 API 回调入口（extract_user_id_from_state）完成，
    # handle_callback 不再二次消费 state，故无 "State 验证失败" 分支

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(
            svc,
            "_exchange_code_for_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            r = await svc.handle_callback(cb, "u")
            assert r.success is False

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(
            svc,
            "_exchange_code_for_token",
            new_callable=AsyncMock,
            return_value={
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 3600,
            },
        ):
            with patch("app.services.trakt.auth.database_manager") as db:
                db.save_trakt_config.return_value = False
                r = await svc.handle_callback(cb, "u")
                assert r.success is False
                db.save_trakt_config.return_value = True
                r = await svc.handle_callback(cb, "u")
                assert r.success is True


@pytest.mark.asyncio
async def test_refresh_token_branches(svc):
    with patch("app.services.trakt.auth.database_manager") as db:
        db.get_trakt_config.return_value = None
        assert await svc.refresh_token("u") is False

    with patch("app.services.trakt.auth.database_manager") as db:
        db.get_trakt_config.return_value = {"user_id": "u"}
        with patch.object(TraktConfig, "from_dict", return_value=None):
            assert await svc.refresh_token("u") is False

    cfg = MagicMock()
    cfg.refresh_if_needed.return_value = False
    with patch("app.services.trakt.auth.database_manager") as db:
        db.get_trakt_config.return_value = {"user_id": "u"}
        with patch.object(TraktConfig, "from_dict", return_value=cfg):
            assert await svc.refresh_token("u") is True

    cfg2 = MagicMock()
    cfg2.refresh_if_needed.return_value = True
    cfg2.refresh_token = None
    with patch("app.services.trakt.auth.database_manager") as db:
        db.get_trakt_config.return_value = {"user_id": "u"}
        with patch.object(TraktConfig, "from_dict", return_value=cfg2):
            assert await svc.refresh_token("u") is False

    cfg3 = MagicMock()
    cfg3.refresh_if_needed.return_value = True
    cfg3.refresh_token = "rt"
    cfg3.to_dict.return_value = {}
    with patch("app.services.trakt.auth.database_manager") as db:
        db.get_trakt_config.return_value = {"user_id": "u"}
        with patch.object(TraktConfig, "from_dict", return_value=cfg3):
            with patch.object(
                svc, "_refresh_access_token", new_callable=AsyncMock, return_value=None
            ):
                assert await svc.refresh_token("u") is False

    cfg4 = MagicMock()
    cfg4.refresh_if_needed.return_value = True
    cfg4.refresh_token = "rt"
    cfg4.to_dict.return_value = {}
    with patch("app.services.trakt.auth.database_manager") as db:
        db.get_trakt_config.return_value = {"user_id": "u"}
        with patch.object(TraktConfig, "from_dict", return_value=cfg4):
            with patch.object(
                svc,
                "_refresh_access_token",
                new_callable=AsyncMock,
                return_value={"access_token": "n", "expires_in": 60},
            ):
                with patch.object(svc, "_calculate_expires_at", return_value=1):
                    db.save_trakt_config.return_value = True
                    assert await svc.refresh_token("u") is True


@pytest.mark.asyncio
async def test_handle_callback_exception_returns_message(svc):
    cb = TraktCallbackRequest(code="c", state="st")
    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(
            svc,
            "_exchange_code_for_token",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            r = await svc.handle_callback(cb, "u")
            assert r.success is False
            assert "boom" in r.message


@pytest.mark.asyncio
async def test_refresh_token_concurrent_no_duplicate_refresh(svc):
    """并发刷新同一用户只应调用一次 OAuth refresh 接口（per-user 锁 + double-check）。

    第一个协程持锁刷新后更新配置（refresh_if_needed 返回 False），第二个协程
    持锁后 double-check 发现无需刷新，跳过。
    """
    import asyncio

    call_count = 0

    cfg_first = MagicMock()
    # 首次检查：需要刷新
    cfg_first.refresh_if_needed.return_value = True
    cfg_first.refresh_token = "rt"
    cfg_first.to_dict.return_value = {}

    cfg_after = MagicMock()
    # 刷新后检查：不再需要刷新（double-check 命中）
    cfg_after.refresh_if_needed.return_value = False

    configs = [cfg_first, cfg_after]

    def _from_dict(data):
        return configs.pop(0) if configs else cfg_after

    async def _counting_refresh(refresh_token):
        nonlocal call_count
        call_count += 1
        # 模拟网络延迟，让两个协程有机会并发
        await asyncio.sleep(0.01)
        return {"access_token": "new", "expires_in": 3600}

    with patch("app.services.trakt.auth.database_manager") as db:
        db.get_trakt_config.return_value = {"user_id": "u"}
        db.save_trakt_config.return_value = True
        with patch.object(TraktConfig, "from_dict", side_effect=_from_dict):
            with patch.object(
                svc,
                "_refresh_access_token",
                new_callable=AsyncMock,
                side_effect=_counting_refresh,
            ):
                with patch.object(svc, "_calculate_expires_at", return_value=1):
                    results = await asyncio.gather(
                        svc.refresh_token("u"),
                        svc.refresh_token("u"),
                    )

    # 并发下只应调用一次 OAuth refresh 接口
    assert call_count == 1
    # 两个协程都应成功返回
    assert all(results)
