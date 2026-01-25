"""
Trakt API 接口测试
"""

import time
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.trakt import router as trakt_router
from app.models.trakt import (
    TraktAuthResponse,
)

# 创建测试应用
app = FastAPI()
app.include_router(trakt_router)


class TestTraktAPI:
    """Trakt API 接口测试类"""

    def setup_method(self):
        """每个测试方法前执行"""
        self.client = TestClient(app)

    def test_init_trakt_auth_success(self):
        """测试初始化 Trakt 授权成功"""
        # 模拟认证服务
        mock_auth_response = TraktAuthResponse(
            auth_url="https://api.trakt.tv/oauth/authorize?client_id=test",
            state="test_state",
        )

        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.init_oauth = AsyncMock(return_value=mock_auth_response)

            # 准备请求数据
            request_data = {"user_id": "test_user"}

            # 执行
            response = self.client.post("/api/trakt/auth/init", json=request_data)

            # 验证
            assert response.status_code == 200
            data = response.json()
            assert (
                data["auth_url"]
                == "https://api.trakt.tv/oauth/authorize?client_id=test"
            )
            assert data["state"] == "test_state"
            mock_auth_service.init_oauth.assert_called_once_with("test_user")

    def test_init_trakt_auth_failure(self):
        """测试初始化 Trakt 授权失败"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.init_oauth = AsyncMock(return_value=None)

            request_data = {"user_id": "test_user"}

            # 执行
            response = self.client.post("/api/trakt/auth/init", json=request_data)

            # 验证
            assert response.status_code == 500
            assert "Trakt 配置无效或初始化失败" in response.json()["detail"]

    def test_init_trakt_auth_exception(self):
        """测试初始化 Trakt 授权时发生异常"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.init_oauth = AsyncMock(side_effect=Exception("测试异常"))

            request_data = {"user_id": "test_user"}

            # 执行
            response = self.client.post("/api/trakt/auth/init", json=request_data)

            # 验证
            assert response.status_code == 500
            assert "初始化授权失败" in response.json()["detail"]

    def test_get_trakt_config_connected(self):
        """测试获取已连接的 Trakt 配置"""
        # 模拟配置
        mock_config = Mock(
            user_id="test_user",
            enabled=True,
            sync_interval="0 */6 * * *",
            last_sync_time=int(time.time()) - 3600,
            access_token="valid_token",
            expires_at=int(time.time()) + 7200,
            is_token_expired=Mock(return_value=False),
        )

        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.get_user_trakt_config = Mock(return_value=mock_config)

            # 执行
            response = self.client.get("/api/trakt/config")

            # 验证
            assert response.status_code == 200
            data = response.json()
            assert data["user_id"] == "test_user"
            assert data["enabled"] is True
            assert data["sync_interval"] == "0 */6 * * *"
            assert data["last_sync_time"] == mock_config.last_sync_time
            assert data["is_connected"] is True
            assert data["token_expires_at"] == mock_config.expires_at

    def test_get_trakt_config_not_connected(self):
        """测试获取未连接的 Trakt 配置"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.get_user_trakt_config = Mock(return_value=None)

            # 执行
            response = self.client.get("/api/trakt/config")

            # 验证
            assert response.status_code == 200
            data = response.json()
            assert data["user_id"] == "default_user"
            assert data["enabled"] is False
            assert data["is_connected"] is False

    def test_update_trakt_config_success(self):
        """测试更新 Trakt 配置成功"""
        # 模拟现有配置
        mock_config = Mock(
            user_id="test_user",
            enabled=True,
            sync_interval="0 */6 * * *",
            last_sync_time=int(time.time()) - 3600,
            access_token="valid_token",
            expires_at=int(time.time()) + 7200,
            is_token_expired=Mock(return_value=False),
        )

        # 模拟数据库操作
        mock_db_success = True

        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.get_user_trakt_config = Mock(return_value=mock_config)

            with patch("app.api.trakt.database_manager") as mock_db:
                mock_db.save_trakt_config = Mock(return_value=mock_db_success)

                # 准备更新数据
                update_data = {"enabled": False, "sync_interval": "0 */3 * * *"}

                # 执行
                response = self.client.put("/api/trakt/config", json=update_data)

                # 验证
                assert response.status_code == 200
                data = response.json()
                assert data["enabled"] is False
                assert data["sync_interval"] == "0 */3 * * *"
                assert data["is_connected"] is True

                # 验证配置更新
                assert mock_config.enabled is False
                assert mock_config.sync_interval == "0 */3 * * *"
                mock_db.save_trakt_config.assert_called_once()

    def test_update_trakt_config_not_found(self):
        """测试更新不存在的 Trakt 配置"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.get_user_trakt_config = Mock(return_value=None)

            update_data = {"enabled": True}

            # 执行
            response = self.client.put("/api/trakt/config", json=update_data)

            # 验证
            assert response.status_code == 404
            assert "Trakt 配置未找到" in response.json()["detail"]

    def test_update_trakt_config_save_failed(self):
        """测试保存 Trakt 配置失败"""
        # 模拟现有配置
        mock_config = Mock(
            user_id="test_user",
            enabled=True,
            sync_interval="0 */6 * * *",
            last_sync_time=None,
            access_token="valid_token",
            expires_at=int(time.time()) + 7200,
            is_token_expired=Mock(return_value=False),
        )

        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.get_user_trakt_config = Mock(return_value=mock_config)

            with patch("app.api.trakt.database_manager") as mock_db:
                mock_db.save_trakt_config = Mock(return_value=False)

                update_data = {"enabled": False}

                # 执行
                response = self.client.put("/api/trakt/config", json=update_data)

                # 验证
                assert response.status_code == 500
                assert "保存配置失败" in response.json()["detail"]

    def test_get_trakt_sync_status_with_config(self):
        """测试获取 Trakt 同步状态（有配置）"""
        # 模拟配置
        mock_config = Mock(
            user_id="test_user",
            last_sync_time=int(time.time()) - 3600,
            access_token="valid_token",
            expires_at=int(time.time()) + 7200,
            is_token_expired=Mock(return_value=False),
        )

        # 模拟调度器状态
        mock_job_status = {
            "next_run_time": int(time.time()) + 21600  # 6小时后
        }

        # 模拟数据库查询结果
        mock_sync_stats = {
            "records": [
                {"status": "success"},
                {"status": "success"},
                {"status": "error"},
                {"status": "success"},
            ]
        }

        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.get_user_trakt_config = Mock(return_value=mock_config)

            with patch("app.api.trakt.trakt_scheduler") as mock_scheduler:
                mock_scheduler.get_user_job_status = Mock(return_value=mock_job_status)

                with patch("app.api.trakt.database_manager") as mock_db:
                    mock_db.get_sync_records = Mock(return_value=mock_sync_stats)

                    # 执行
                    response = self.client.get("/api/trakt/sync/status")

                    # 验证
                    assert response.status_code == 200
                    data = response.json()
                    assert data["last_sync_time"] == mock_config.last_sync_time
                    assert data["next_sync_time"] == mock_job_status["next_run_time"]
                    assert data["success_count"] == 3
                    assert data["error_count"] == 1
                    assert data["total_count"] == 4
                    assert data["is_running"] is False

    def test_get_trakt_sync_status_no_config(self):
        """测试获取 Trakt 同步状态（无配置）"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.get_user_trakt_config = Mock(return_value=None)

            # 执行
            response = self.client.get("/api/trakt/sync/status")

            # 验证
            assert response.status_code == 200
            data = response.json()
            assert data["last_sync_time"] is None
            assert data["next_sync_time"] is None
            assert data["success_count"] == 0
            assert data["error_count"] == 0
            assert data["total_count"] == 0

    @pytest.mark.asyncio
    async def test_manual_trakt_sync_success(self):
        """测试手动触发 Trakt 同步成功"""
        # 模拟同步服务
        mock_task_id = "trakt_sync_test_user_123456"

        with patch("app.api.trakt.trakt_sync_service") as mock_sync_service:
            mock_sync_service.start_user_sync_task = AsyncMock(
                return_value=mock_task_id
            )

            # 准备请求数据
            request_data = {"user_id": "test_user", "full_sync": True}

            # 执行
            response = self.client.post("/api/trakt/sync/manual", json=request_data)

            # 验证
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "同步任务已提交"
            assert data["job_id"] == mock_task_id

            mock_sync_service.start_user_sync_task.assert_called_once_with(
                user_id="test_user", full_sync=True
            )

    @pytest.mark.asyncio
    async def test_manual_trakt_sync_exception(self):
        """测试手动触发 Trakt 同步时发生异常"""
        with patch("app.api.trakt.trakt_sync_service") as mock_sync_service:
            mock_sync_service.start_user_sync_task = AsyncMock(
                side_effect=Exception("测试异常")
            )

            request_data = {"user_id": "test_user", "full_sync": False}

            # 执行
            response = self.client.post("/api/trakt/sync/manual", json=request_data)

            # 验证
            assert response.status_code == 500
            assert "触发同步失败" in response.json()["detail"]

    def test_disconnect_trakt_success(self):
        """测试断开 Trakt 连接成功"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.disconnect_trakt = Mock(return_value=True)

            # 执行
            response = self.client.delete("/api/trakt/disconnect")

            # 验证
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Trakt 连接已断开"
            mock_auth_service.disconnect_trakt.assert_called_once_with("default_user")

    def test_disconnect_trakt_failure(self):
        """测试断开 Trakt 连接失败"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.disconnect_trakt = Mock(return_value=False)

            # 执行
            response = self.client.delete("/api/trakt/disconnect")

            # 验证
            assert response.status_code == 500
            assert "断开连接失败" in response.json()["detail"]

    def test_disconnect_trakt_exception(self):
        """测试断开 Trakt 连接时发生异常"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.disconnect_trakt = Mock(side_effect=Exception("测试异常"))

            # 执行
            response = self.client.delete("/api/trakt/disconnect")

            # 验证
            assert response.status_code == 500
            assert "断开连接失败" in response.json()["detail"]

    def test_trakt_auth_callback_success(self):
        """测试 Trakt OAuth 回调成功"""
        # 模拟认证服务
        mock_callback_response = Mock(success=True, message="授权成功")

        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.handle_callback = AsyncMock(
                return_value=mock_callback_response
            )

            # 执行回调请求
            response = self.client.get(
                "/api/trakt/auth/callback?code=test_code&state=test_state",
                follow_redirects=False,
            )

            # 验证
            assert response.status_code == 307  # 重定向
            assert response.headers["location"] == "/trakt/config?status=success"

            mock_auth_service.handle_callback.assert_called_once()

    def test_trakt_auth_callback_failure(self):
        """测试 Trakt OAuth 回调失败"""
        mock_callback_response = Mock(success=False, message="授权失败")

        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.handle_callback = AsyncMock(
                return_value=mock_callback_response
            )

            # 执行回调请求
            response = self.client.get(
                "/api/trakt/auth/callback?code=test_code&state=test_state",
                follow_redirects=False,
            )

            # 验证
            assert response.status_code == 307
            location = response.headers["location"]
            assert "/trakt/auth?status=error" in location
            unquoted_location = unquote(location)
            assert "message=授权失败" in unquoted_location

    def test_trakt_auth_callback_exception(self):
        """测试 Trakt OAuth 回调时发生异常"""
        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.handle_callback = AsyncMock(
                side_effect=Exception("回调异常")
            )

            # 执行回调请求
            response = self.client.get(
                "/api/trakt/auth/callback?code=test_code&state=test_state",
                follow_redirects=False,
            )

            # 验证
            assert response.status_code == 307
            location = response.headers["location"]
            assert "/trakt/auth?status=error" in location
            unquoted_location = unquote(location)
            assert "message=回调异常" in unquoted_location

    def test_trakt_auth_callback_no_state(self):
        """测试 Trakt OAuth 回调没有 state 参数"""
        mock_callback_response = Mock(success=True, message="授权成功")

        with patch("app.api.trakt.trakt_auth_service") as mock_auth_service:
            mock_auth_service.handle_callback = AsyncMock(
                return_value=mock_callback_response
            )

            # 执行回调请求（无 state 参数）
            response = self.client.get(
                "/api/trakt/auth/callback?code=test_code",
                follow_redirects=False,
            )

            # 验证
            assert response.status_code == 307
            mock_auth_service.handle_callback.assert_called_once()
            # 应该使用空字符串作为 state
            call_args = mock_auth_service.handle_callback.call_args[0]
            assert call_args[0].state == ""
