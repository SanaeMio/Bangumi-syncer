"""TraktAuthService：配置校验、OAuth 状态、令牌交换/刷新（httpx mock）。"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.models.trakt import TraktCallbackRequest, TraktConfig
from app.services.trakt.auth import TraktAuthService


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
        svc._oauth_states["u:s"]["created_at"] = "bad"
        assert svc._verify_oauth_state("u", "s") is False
        svc._save_oauth_state("u2", "s2")
        svc._oauth_states["u2:s2"]["created_at"] = time.time() - 400
        assert svc._verify_oauth_state("u2", "s2") is False
        svc._save_oauth_state("u3", "s3")
        assert svc._verify_oauth_state("u3", "s3") is True
        assert "u3:s3" not in svc._oauth_states

    def test_cleanup_expired_states(self, svc):
        svc._oauth_states = {
            "a:b": {"user_id": "a", "state": "b", "created_at": time.time() - 999},
            "c:d": {"user_id": "c", "state": "d", "created_at": time.time()},
        }
        svc._cleanup_expired_states(max_age=300)
        assert "a:b" not in svc._oauth_states
        assert "c:d" in svc._oauth_states

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


class _FakeAsyncClientCtx:
    def __init__(self, post_coro):
        self._post = post_coro

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def post(self, *a, **kw):
        return await self._post()


@pytest.mark.asyncio
async def test_exchange_code_for_token_200_and_errors(svc):
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"access_token": "a"}

    async def ret_ok():
        return ok

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch(
                "app.services.trakt.auth.httpx.AsyncClient",
                return_value=_FakeAsyncClientCtx(ret_ok),
            ):
                assert await svc._exchange_code_for_token("code") == {"access_token": "a"}

    bad = MagicMock(status_code=400, text="err")
    async def ret_bad():
        return bad

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch(
                "app.services.trakt.auth.httpx.AsyncClient",
                return_value=_FakeAsyncClientCtx(ret_bad),
            ):
                assert await svc._exchange_code_for_token("c") is None

    with patch.object(svc, "_validate_config", return_value=False):
        assert await svc._exchange_code_for_token("c") is None

    async def boom():
        raise httpx.RequestError("x", request=MagicMock())

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch(
                "app.services.trakt.auth.httpx.AsyncClient",
                return_value=_FakeAsyncClientCtx(boom),
            ):
                assert await svc._exchange_code_for_token("c") is None


@pytest.mark.asyncio
async def test_refresh_access_token_paths(svc):
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"access_token": "n"}
    async def ret_ok():
        return ok

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch(
                "app.services.trakt.auth.httpx.AsyncClient",
                return_value=_FakeAsyncClientCtx(ret_ok),
            ):
                assert await svc._refresh_access_token("rt") == {"access_token": "n"}

    async def boom():
        raise RuntimeError("inner")

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_get_config", return_value=_valid_trakt_cfg()):
            with patch(
                "app.services.trakt.auth.httpx.AsyncClient",
                return_value=_FakeAsyncClientCtx(boom),
            ):
                assert await svc._refresh_access_token("rt") is None


@pytest.mark.asyncio
async def test_handle_callback_branches(svc):
    cb = TraktCallbackRequest(code="c", state="st")
    with patch.object(svc, "_validate_config", return_value=False):
        r = await svc.handle_callback(cb, "u")
        assert r.success is False

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_verify_oauth_state", return_value=False):
            r = await svc.handle_callback(cb, "u")
            assert "State" in r.message

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_verify_oauth_state", return_value=True):
            with patch.object(
                svc,
                "_exchange_code_for_token",
                new_callable=AsyncMock,
                return_value=None,
            ):
                r = await svc.handle_callback(cb, "u")
                assert r.success is False

    with patch.object(svc, "_validate_config", return_value=True):
        with patch.object(svc, "_verify_oauth_state", return_value=True):
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
            svc, "_verify_oauth_state", side_effect=RuntimeError("boom")
        ):
            r = await svc.handle_callback(cb, "u")
            assert r.success is False
            assert "boom" in r.message
