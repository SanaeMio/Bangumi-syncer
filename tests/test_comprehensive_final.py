"""
Final comprehensive tests
"""

from unittest.mock import MagicMock, patch


class TestSimpleFunctionCalls:
    """Simple function call tests"""

    def test_data_util_functions(self):
        """Test extractor functions exist in services subpackages"""
        from app.services.emby.extractor import extract_emby_data
        from app.services.jellyfin.extractor import extract_jellyfin_data
        from app.services.plex.extractor import extract_plex_data

        assert callable(extract_plex_data)
        assert callable(extract_emby_data)
        assert callable(extract_jellyfin_data)


class TestNotifierBasics:
    """Basic notifier tests"""

    def test_notifier_cooldown(self):
        """Test cooldown works"""
        mock_config = MagicMock()
        from app.utils.notifier import Notifier

        notifier = Notifier(mock_config)
        # Test first call
        result = notifier._should_send_notification("test_type")
        assert result is True


class TestBangumiApiBasics:
    """Basic Bangumi API tests"""

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_api_host(self, mock_session):
        """Test API has correct host"""
        from app.utils.bangumi_api import BangumiApi

        api = BangumiApi()
        assert api.host == "https://api.bgm.tv/v0"


class TestSyncServiceBasics:
    """Basic sync service tests"""

    def test_service_init(self):
        """Test service can be created and exposes core sync methods"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()
            assert hasattr(service, "sync_custom_item")
            assert hasattr(service, "sync_plex_item")
            assert hasattr(service, "sync_emby_item")
            assert hasattr(service, "sync_jellyfin_item")


class TestTraktBasics:
    """Basic Trakt tests"""

    def test_trakt_auth_init(self):
        """Test TraktAuthService can be created"""
        from app.services.trakt.auth import TraktAuthService

        service = TraktAuthService()
        assert service.base_url == "https://api.trakt.tv"

    def test_trakt_client_init(self):
        """Test TraktClient can be created"""
        with patch("app.services.trakt.client.config_manager"):
            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test")
            assert client.access_token == "test"
