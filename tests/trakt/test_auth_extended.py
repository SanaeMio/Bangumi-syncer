"""
Trakt 认证扩展测试
"""

from unittest.mock import MagicMock, patch


class TestTraktAuthService:
    """Trakt 认证服务测试"""

    @patch("app.services.trakt.auth.config_manager")
    def test_trakt_auth_service_import(self, mock_config):
        """测试导入 TraktAuthService"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.services.trakt.auth import TraktAuthService

        assert TraktAuthService is not None


class TestTraktModels:
    """Trakt 模型测试"""

    def test_trakt_history_item_model_import(self):
        """测试导入 TraktHistoryItem"""
        from app.services.trakt.models import TraktHistoryItem

        assert TraktHistoryItem is not None


class TestTraktClientMethods:
    """Trakt 客户端方法测试"""

    @patch("app.services.trakt.client.config_manager")
    def test_trakt_client_init(self, mock_config):
        """测试 TraktClient 初始化"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.services.trakt.client import TraktClient

        client = TraktClient(access_token="test_token")
        assert client is not None
        assert client.access_token == "test_token"
