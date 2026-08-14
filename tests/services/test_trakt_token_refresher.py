"""Trakt Bearer 凭证续期服务测试。"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.trakt.token_refresher import (
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    heartbeat_all_users,
    refresh_user_bearer,
    validate_and_save_bearer,
)


def _make_config_dict(**over):
    d = {
        "user_id": "u1",
        "access_token": "tok1",
        "refresh_token": "ref1",
        "expires_at": None,
        "enabled": 1,
        "sync_interval": "0 */6 * * *",
        "sync_filter_enabled": 1,
        "last_sync_time": None,
        "auth_type": "bearer",
        "created_at": 1,
        "updated_at": 1,
    }
    d.update(over)
    return d


def _token_response(access="newaccess", refresh="newrefresh", expires_in=604800):
    return {
        "access_token": access,
        "expires_in": expires_in,
        "expires_at": int(time.time()) + expires_in,
        "token_type": "bearer",
        "refresh_token": refresh,
        "scope": "public openid profile email offline_access",
        "id_token": "jwt",
    }


class TestValidateAndSaveBearer:
    @pytest.mark.asyncio
    async def test_success_rotates_and_saves(self):
        """有效凭证：旋转式换新并落库（auth_type=bearer）"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_db.get_trakt_config.return_value = _make_config_dict()
            mock_call.return_value = ("ok", _token_response(), None)

            result = await validate_and_save_bearer("u1", "ref1")

            assert result["success"] is True
            saved = mock_db.save_trakt_config.call_args[0][0]
            assert saved["auth_type"] == "bearer"
            assert saved["access_token"] == "newaccess"  # 旋转换新
            assert saved["refresh_token"] == "newrefresh"
            assert saved["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_invalid_grant_not_saved(self):
        """凭证无效：不落库，返回失败"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_call.return_value = ("invalid_grant", None, "invalid refresh token")

            result = await validate_and_save_bearer("u1", "bad")

            assert result["success"] is False
            assert "invalid_grant" in result["message"]
            mock_db.save_trakt_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_network_error_not_saved(self):
        """网络错误：不落库，返回失败"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_call.return_value = ("error", None, "timeout")

            result = await validate_and_save_bearer("u1", "ref1")

            assert result["success"] is False
            mock_db.save_trakt_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_new_config_when_none(self):
        """无既有配置：验证成功即新建配置"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_db.get_trakt_config.return_value = None
            mock_call.return_value = ("ok", _token_response(), None)

            result = await validate_and_save_bearer("u1", "ref1")

            assert result["success"] is True
            saved = mock_db.save_trakt_config.call_args[0][0]
            assert saved["user_id"] == "u1"
            assert saved["auth_type"] == "bearer"


class TestRefreshUserBearer:
    @pytest.mark.asyncio
    async def test_skipped_when_not_due(self):
        """剩余 >1 天：跳过刷新"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_db.get_trakt_config.return_value = _make_config_dict(
                expires_at=int(time.time()) + 3 * 86400
            )
            status = await refresh_user_bearer("u1")
            assert status == STATUS_SKIPPED
            mock_call.assert_not_called()
            mock_db.save_trakt_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_ok_when_due(self):
        """剩余 ≤1 天：刷新并旋转落库"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_db.get_trakt_config.return_value = _make_config_dict(
                expires_at=int(time.time()) + 3600
            )
            mock_call.return_value = ("ok", _token_response(), None)

            status = await refresh_user_bearer("u1")

            assert status == STATUS_OK
            saved = mock_db.save_trakt_config.call_args[0][0]
            assert saved["access_token"] == "newaccess"
            assert saved["refresh_token"] == "newrefresh"

    @pytest.mark.asyncio
    async def test_invalid_grant_marks_expired_and_notifies(self):
        """invalid_grant：标失效（expires_at=0）+ 通知，保留配置"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
            patch(
                "app.services.trakt.token_refresher.notify_scheduler_failure"
            ) as mock_notify,
        ):
            mock_db.get_trakt_config.return_value = _make_config_dict(expires_at=0)
            mock_call.return_value = ("invalid_grant", None, "invalid refresh token")

            status = await refresh_user_bearer("u1")

            assert status == STATUS_EXPIRED
            saved = mock_db.save_trakt_config.call_args[0][0]
            assert saved["expires_at"] == 0
            mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_server_error_keeps_state(self):
        """服务端错误：非失效，保留原状态"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_db.get_trakt_config.return_value = _make_config_dict(expires_at=0)
            mock_call.return_value = ("error", None, "HTTP 500")

            status = await refresh_user_bearer("u1")

            assert status == STATUS_FAILED
            mock_db.save_trakt_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_skipped_wrong_mode_or_no_refresh(self):
        """非 bearer 模式 / 无 refresh_token：跳过"""
        with patch("app.services.trakt.token_refresher.database_manager") as mock_db:
            mock_db.get_trakt_config.return_value = _make_config_dict(
                auth_type="oauth", refresh_token=None
            )
            assert (await refresh_user_bearer("u1")) == STATUS_SKIPPED

    @pytest.mark.asyncio
    async def test_force_skips_slack_check(self):
        """force=True 时忽略 expires_at slack，剩余 >1 天也强制刷新（401 路径）"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_db.get_trakt_config.return_value = _make_config_dict(
                expires_at=int(time.time()) + 6 * 86400  # 剩余 6 天
            )
            mock_call.return_value = ("ok", _token_response(), None)

            status = await refresh_user_bearer("u1", force=True)

            assert status == STATUS_OK
            mock_call.assert_awaited_once()
            saved = mock_db.save_trakt_config.call_args[0][0]
            assert saved["access_token"] == "newaccess"

    @pytest.mark.asyncio
    async def test_ok_response_missing_tokens_keeps_state(self):
        """ok 响应缺 token 字段：STATUS_FAILED 且不落库"""
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            mock_db.get_trakt_config.return_value = _make_config_dict(expires_at=0)
            mock_call.return_value = ("ok", {"expires_in": 604800}, None)

            status = await refresh_user_bearer("u1", force=True)

            assert status == STATUS_FAILED
            mock_db.save_trakt_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_refresh_serialized_by_lock(self):
        """并发刷新被 per-user 锁串行化：第二个刷新读到第一次落库后的新 refresh。

        无锁时两个协程会同时读到旧 refresh（r1），各自旋转后互相覆盖；
        有锁 + 持锁重读时，第二次调用应以第一次的新 refresh（newrefresh）
        发起 /oauth/token。
        """
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher._call_oauth_token",
                new_callable=AsyncMock,
            ) as mock_call,
        ):
            # 模拟真实存储：save 后 get 返回新值（否则并发重读永远拿到旧 config）
            store = _make_config_dict(expires_at=0)

            def _fake_get(uid):
                return dict(store)

            def _fake_save(cfg):
                store.clear()
                store.update(cfg)

            mock_db.get_trakt_config.side_effect = _fake_get
            mock_db.save_trakt_config.side_effect = _fake_save
            # 每次刷新都返回同一组新 token（旋转式）；记录每次调用使用的 refresh
            mock_call.side_effect = [
                ("ok", _token_response(), None),
                ("ok", _token_response(), None),
            ]

            r1, r2 = await asyncio.gather(
                refresh_user_bearer("u1", force=True),
                refresh_user_bearer("u1", force=True),
            )

            assert r1 == STATUS_OK
            assert r2 == STATUS_OK
            # 两次都真的调了 /oauth/token
            assert mock_call.await_count == 2
            # 第二次调用用的是第一次落库后的新 refresh（持锁重读）
            assert mock_call.await_args_list[0][0][0] == "ref1"
            assert mock_call.await_args_list[1][0][0] == "newrefresh"
            # 落库两次，最终是旋转后的新 token
            assert mock_db.save_trakt_config.call_count == 2
            final_saved = mock_db.save_trakt_config.call_args[0][0]
            assert final_saved["refresh_token"] == "newrefresh"


class TestHeartbeatAllUsers:
    @pytest.mark.asyncio
    async def test_only_bearer_mode_with_refresh(self):
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher.refresh_user_bearer",
                new_callable=AsyncMock,
            ) as mock_refresh,
        ):
            mock_db.get_trakt_configs_with_sync_enabled.return_value = [
                _make_config_dict(user_id="u1", auth_type="bearer", refresh_token="r1"),
                _make_config_dict(user_id="u2", auth_type="oauth", refresh_token=None),
                _make_config_dict(user_id="u3", auth_type="bearer", refresh_token=None),
            ]
            mock_refresh.return_value = STATUS_OK

            results = await heartbeat_all_users()

            # 仅 u1 是 bearer 且有 refresh_token
            assert results["checked"] == 1
            assert results["ok"] == 1
            mock_refresh.assert_awaited_once_with("u1")

    @pytest.mark.asyncio
    async def test_counts_expired_and_failed(self):
        with (
            patch("app.services.trakt.token_refresher.database_manager") as mock_db,
            patch(
                "app.services.trakt.token_refresher.refresh_user_bearer",
                new_callable=AsyncMock,
            ) as mock_refresh,
        ):
            mock_db.get_trakt_configs_with_sync_enabled.return_value = [
                _make_config_dict(user_id="u1", refresh_token="r1"),
                _make_config_dict(user_id="u2", refresh_token="r2"),
            ]
            mock_refresh.side_effect = [STATUS_EXPIRED, STATUS_SKIPPED]

            results = await heartbeat_all_users()

            assert results["checked"] == 2
            assert results["expired"] == 1
            assert results["skipped"] == 1
