"""
Trakt 同步服务完整测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.trakt.models import TraktSyncResult
from app.services.trakt.sync_service import TraktSyncService


class TestTraktSyncServiceComprehensive:
    """Trakt 同步服务综合测试"""

    def test_init(self):
        """测试初始化"""
        service = TraktSyncService()
        assert service._active_syncs == {}
        assert service._sync_results == {}


@pytest.mark.asyncio
async def test_sync_user_trakt_data_no_config():
    """测试同步时配置不存在"""
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.database_manager"),
    ):
        mock_auth.get_user_trakt_config.return_value = None

        service = TraktSyncService()
        result = await service.sync_user_trakt_data("user1")

        assert result.success is False
        assert "不存在" in result.message


@pytest.mark.asyncio
async def test_sync_user_trakt_data_no_token():
    """测试同步时没有令牌"""
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.database_manager"),
    ):
        mock_config = MagicMock()
        mock_config.access_token = None
        mock_auth.get_user_trakt_config.return_value = mock_config

        service = TraktSyncService()
        result = await service.sync_user_trakt_data("user1")

        assert result.success is False
        assert "未授权" in result.message


@pytest.mark.asyncio
async def test_sync_user_trakt_data_token_expired():
    """测试令牌过期"""
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.database_manager"),
        patch("app.services.trakt.sync_service.TraktClientFactory"),
    ):
        mock_config = MagicMock()
        mock_config.access_token = "old_token"
        mock_config.is_token_expired.return_value = True
        mock_auth.get_user_trakt_config.return_value = mock_config
        mock_auth.refresh_token = AsyncMock(return_value=False)

        service = TraktSyncService()
        result = await service.sync_user_trakt_data("user1")

        assert result.success is False
        assert "过期" in result.message


class TestTraktSyncResult:
    """Trakt 同步结果测试"""

    def test_sync_result_creation(self):
        """测试创建同步结果"""
        result = TraktSyncResult(
            success=True,
            message="Test message",
            synced_count=10,
            skipped_count=5,
            error_count=1,
            details={"test": "data"},
        )

        assert result.success is True
        assert result.synced_count == 10
        assert result.skipped_count == 5
        assert result.error_count == 1


def test_trakt_sync_service_module_import():
    """测试模块导入"""
    from app.services.trakt.sync_service import TraktSyncService

    assert TraktSyncService is not None
