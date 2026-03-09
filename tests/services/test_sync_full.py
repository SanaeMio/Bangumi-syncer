"""
更多 sync_service 测试
"""

from unittest.mock import MagicMock, patch

from app.models.sync import CustomItem


class TestSyncServiceFind:
    """sync_service 查找方法测试"""

    def test_find_subject_id(self):
        """测试查找 subject ID"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
            patch("app.services.sync_service.BangumiApi") as mock_bgm,
            patch("app.services.sync_service.BangumiData"),
        ):
            mock_bgm_instance = MagicMock()
            mock_bgm_instance.search_subject.return_value = {
                "id": 12345,
                "name": "Test Show",
            }
            mock_bgm.return_value = mock_bgm_instance

            mock_config = MagicMock()
            mock_config.get.side_effect = lambda s, k, f=None: {
                ("sync", "mode"): "single",
                ("sync", "single_username"): "admin",
                ("sync", "blocked_keywords"): "",
                ("bangumi_data", "enabled"): False,
            }.get((s, k), f)

            from app.services.sync_service import SyncService

            service = SyncService()

            item = CustomItem(
                media_type="episode",
                title="Test Show",
                season=1,
                episode=1,
                release_date="2024-01-01",
                user_name="admin",
            )

            result = service._find_subject_id(item)
            assert result is not None


class TestSyncServiceMore:
    """更多 sync_service 测试"""

    def test_sync_task_counter(self):
        """测试任务计数器"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            initial = service._task_counter
            assert initial == 0


class TestCustomItemMore:
    """CustomItem 更多测试"""

    def test_custom_item_defaults(self):
        """测试默认值"""
        item = CustomItem(
            media_type="episode",
            title="Test",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="test",
        )
        assert item.source is None
        assert item.ori_title is None

    def test_custom_item_model_dump(self):
        """测试模型导出"""
        item = CustomItem(
            media_type="episode",
            title="Test",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="test",
        )
        d = item.model_dump()
        assert "media_type" in d
        assert "title" in d
