"""
Trakt API测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, trakt


@pytest.fixture
def app_with_auth():
    """创建带有认证的测试应用"""
    app = FastAPI()
    app.include_router(trakt.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def mock_trakt_auth_service():
    """模拟 Trakt 认证服务"""
    with patch("app.api.trakt.trakt_auth_service") as mock_auth:
        mock_auth.init_oauth = AsyncMock(
            return_value={
                "auth_url": "https://trakt.tv/oauth/authorize",
                "state": "test_state",
            }
        )
        mock_auth.extract_user_id_from_state.return_value = "test_user"
        mock_auth.handle_callback = AsyncMock(
            return_value=MagicMock(success=True, message="Authorized successfully")
        )
        mock_auth.get_user_trakt_config.return_value = MagicMock(
            user_id="test_user",
            enabled=True,
            sync_interval="0 */6 * * *",
            last_sync_time=None,
            access_token="test_token",
            is_token_expired=lambda: False,
            expires_at=None,
            to_dict=lambda: {},
        )
        mock_auth.disconnect_trakt.return_value = True

        yield mock_auth


@pytest.fixture
def mock_trakt_scheduler():
    """模拟 Trakt 调度器"""
    with patch("app.api.trakt.trakt_scheduler") as mock_scheduler:
        mock_scheduler.get_user_job_status.return_value = {"next_run_time": 9999999999}
        mock_scheduler.remove_user_job.return_value = None
        mock_scheduler.add_user_job.return_value = None

        yield mock_scheduler


@pytest.fixture
def mock_trakt_sync_service():
    """模拟 Trakt 同步服务"""
    with patch("app.api.trakt.trakt_sync_service") as mock_sync:
        mock_sync.start_user_sync_task = AsyncMock(return_value="test_job_123")

        yield mock_sync


@pytest.fixture
def mock_config_manager():
    """模拟配置管理器"""
    with patch("app.api.trakt.config_manager") as mock_cm:
        mock_cm.get_trakt_config.return_value = {
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "redirect_uri": "http://localhost:8000/api/trakt/auth/callback",
        }

        yield mock_cm


@pytest.fixture
def mock_database_manager():
    """模拟数据库管理器"""
    with patch("app.api.trakt.database_manager") as mock_db:
        mock_db.save_trakt_config.return_value = True
        mock_db.get_sync_records.return_value = {
            "records": [
                {"status": "success"},
                {"status": "success"},
                {"status": "error"},
            ]
        }

        yield mock_db


@pytest.mark.asyncio
async def test_init_trakt_auth(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_trakt_sync_service,
    mock_config_manager,
):
    """测试初始化 Trakt 授权"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/trakt/auth/init",
            json={"user_id": "test_user"},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_init_trakt_auth_failure(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_trakt_sync_service,
    mock_config_manager,
):
    """测试初始化 Trakt 授权失败"""
    mock_trakt_auth_service.init_oauth.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/trakt/auth/init",
            json={"user_id": "test_user"},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_init_trakt_auth_exception(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_trakt_sync_service,
    mock_config_manager,
):
    """测试初始化 Trakt 授权抛出异常"""
    mock_trakt_auth_service.init_oauth = AsyncMock(side_effect=RuntimeError("boom"))

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/trakt/auth/init",
            json={"user_id": "test_user"},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_trakt_config(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试获取 Trakt 配置"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/trakt/config")
        assert response.status_code == 200
        data = response.json()
        assert "client_id" in data


@pytest.mark.asyncio
async def test_get_trakt_config_no_config(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试获取不存在的 Trakt 配置"""
    mock_trakt_auth_service.get_user_trakt_config.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/trakt/config")
        assert response.status_code == 200
        data = response.json()
        assert data["is_connected"] is False


@pytest.mark.asyncio
async def test_get_trakt_config_exception(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试获取 Trakt 配置时抛出异常"""
    mock_trakt_auth_service.get_user_trakt_config.side_effect = RuntimeError("db error")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/trakt/config")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_update_trakt_config(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
    mock_database_manager,
):
    """测试更新 Trakt 配置"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/trakt/config",
            json={"enabled": True, "sync_interval": "0 */6 * * *"},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_trakt_config_not_found(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
    mock_database_manager,
):
    """测试更新不存在的 Trakt 配置"""
    mock_trakt_auth_service.get_user_trakt_config.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/trakt/config",
            json={"enabled": True, "sync_interval": "0 */6 * * *"},
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_trakt_config_save_failure(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
    mock_database_manager,
):
    """测试更新 Trakt 配置保存失败"""
    mock_database_manager.save_trakt_config.return_value = False

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/trakt/config",
            json={"enabled": True, "sync_interval": "0 */6 * * *"},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_update_trakt_config_exception(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
    mock_database_manager,
):
    """测试更新 Trakt 配置时抛出异常"""
    mock_trakt_auth_service.get_user_trakt_config.side_effect = RuntimeError("fail")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/trakt/config",
            json={"enabled": True},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_update_trakt_api_config(app_with_auth, mock_config_manager):
    """测试更新 Trakt API 配置"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/trakt/config/api",
            json={
                "client_id": "new_client_id",
                "client_secret": "new_client_secret",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


@pytest.mark.asyncio
async def test_update_trakt_api_config_skips_empty_client_secret(
    app_with_auth, mock_config_manager
):
    """client_secret 为空字符串时不应调用 set 覆盖已有密文。"""
    mock_config_manager.reset_mock()
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/trakt/config/api",
            json={"client_id": "new_only", "client_secret": ""},
        )

    assert response.status_code == 200
    secret_calls = [
        c
        for c in mock_config_manager.set.call_args_list
        if len(c[0]) >= 2 and c[0][0] == "trakt" and c[0][1] == "client_secret"
    ]
    assert secret_calls == []
    id_calls = [
        c
        for c in mock_config_manager.set.call_args_list
        if len(c[0]) >= 2 and c[0][0] == "trakt" and c[0][1] == "client_id"
    ]
    assert len(id_calls) == 1
    assert id_calls[0][0][2] == "new_only"


@pytest.mark.asyncio
async def test_update_trakt_api_config_with_redirect_uri(
    app_with_auth, mock_config_manager
):
    """测试更新 redirect_uri"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/trakt/config/api",
            json={"redirect_uri": "https://example.com/callback"},
        )
    assert response.status_code == 200
    mock_config_manager.set.assert_any_call(
        "trakt", "redirect_uri", "https://example.com/callback"
    )


@pytest.mark.asyncio
async def test_update_trakt_api_config_exception(app_with_auth, mock_config_manager):
    """测试更新 Trakt API 配置时抛出异常"""
    mock_config_manager.get_trakt_config.side_effect = RuntimeError("fail")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/trakt/config/api",
            json={"client_id": "new"},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_trakt_sync_status(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
    mock_database_manager,
):
    """测试获取 Trakt 同步状态"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/trakt/sync/status")
        assert response.status_code == 200
        data = response.json()
        assert "success_count" in data


@pytest.mark.asyncio
async def test_get_trakt_sync_status_no_config(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
    mock_database_manager,
):
    """测试获取无配置的 Trakt 同步状态"""
    mock_trakt_auth_service.get_user_trakt_config.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/trakt/sync/status")
        assert response.status_code == 200
        data = response.json()
        assert data["is_running"] is False


@pytest.mark.asyncio
async def test_get_trakt_sync_status_exception(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
    mock_database_manager,
):
    """测试获取 Trakt 同步状态时抛出异常"""
    mock_trakt_auth_service.get_user_trakt_config.side_effect = RuntimeError("fail")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/trakt/sync/status")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_trakt_sync_status_no_job(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
    mock_database_manager,
):
    """测试无调度作业时的同步状态"""
    mock_trakt_scheduler.get_user_job_status.return_value = None
    mock_database_manager.get_sync_records.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/trakt/sync/status")
        assert response.status_code == 200
        data = response.json()
        assert data["next_sync_time"] is None


@pytest.mark.asyncio
async def test_manual_trakt_sync(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_trakt_sync_service,
    mock_config_manager,
):
    """测试手动触发 Trakt 同步"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/trakt/sync/manual",
            json={"user_id": "test_user", "full_sync": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


@pytest.mark.asyncio
async def test_manual_trakt_sync_exception(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_trakt_sync_service,
    mock_config_manager,
):
    """测试手动触发 Trakt 同步时抛出异常"""
    mock_trakt_sync_service.start_user_sync_task = AsyncMock(
        side_effect=RuntimeError("sync fail")
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/trakt/sync/manual",
            json={"user_id": "test_user", "full_sync": True},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_disconnect_trakt(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试断开 Trakt 连接"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.delete("/api/trakt/disconnect")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


@pytest.mark.asyncio
async def test_disconnect_trakt_failure(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试断开 Trakt 连接失败"""
    mock_trakt_auth_service.disconnect_trakt.return_value = False

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.delete("/api/trakt/disconnect")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_disconnect_trakt_exception(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试断开 Trakt 连接时抛出异常"""
    mock_trakt_auth_service.disconnect_trakt.side_effect = RuntimeError("fail")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.delete("/api/trakt/disconnect")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_trakt_auth_callback_no_state(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试 callback 缺少 state 参数"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/trakt/auth/callback?code=test_code")
        assert response.status_code == 302  # redirect


@pytest.mark.asyncio
async def test_trakt_auth_callback_failure(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试 callback 处理失败"""
    mock_trakt_auth_service.handle_callback = AsyncMock(
        return_value=MagicMock(success=False, message="Auth failed")
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/trakt/auth/callback?code=test_code&state=test_state"
        )
        assert response.status_code == 302


@pytest.mark.asyncio
async def test_trakt_auth_callback_exception(
    app_with_auth,
    mock_trakt_auth_service,
    mock_trakt_scheduler,
    mock_config_manager,
):
    """测试 callback 处理时抛出异常"""
    mock_trakt_auth_service.extract_user_id_from_state.side_effect = RuntimeError(
        "bad state"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/trakt/auth/callback?code=test_code&state=test_state"
        )
        assert response.status_code == 302
