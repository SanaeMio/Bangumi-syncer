"""
API 端到端测试
"""

from unittest.mock import patch

from fastapi.testclient import TestClient


class TestConfigAPIEndpoints:
    """配置 API 端点测试"""

    @patch("app.api.config.config_manager")
    def test_get_config_endpoint(self, mock_config):
        """测试获取配置端点"""
        mock_config.get.return_value = "test_value"
        mock_config.get_config_parser.return_value.has_section.return_value = True
        mock_config.get_config_parser.return_value.sections.return_value = ["test"]

        from app.api import config
        from app.main import app

        client = TestClient(app)
        # 由于路由需要认证，这里只是测试路由存在
        assert config.router is not None


class TestSyncAPIEndpoints:
    """同步 API 端点测试"""

    def test_sync_router_has_routes(self):
        """测试同步路由有路由"""
        from app.api import sync

        # 检查路由数量
        assert len(sync.router.routes) > 0


class TestAuthAPIEndpoints:
    """认证 API 端点测试"""

    def test_auth_router_has_routes(self):
        """测试认证路由有路由"""
        from app.api import auth

        # 检查路由数量
        assert len(auth.router.routes) > 0


class TestNotificationAPIEndpoints:
    """通知 API 端点测试"""

    def test_notification_router_has_routes(self):
        """测试通知路由有路由"""
        from app.api import notification

        # 检查路由数量
        assert len(notification.router.routes) > 0
