"""
更多 sync_service 测试
"""

from unittest.mock import patch

from app.models.sync import CustomItem


class TestSyncServiceHelper:
    """SyncService 辅助方法测试"""

    def test_check_season_info_in_title(self):
        """测试检查季度信息"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
                ("sync", "blocked_keywords"): "",
                ("bangumi_data", "enabled"): False,
            }.get((section, key), fallback)
            mock_config.get_single_mode_media_usernames.return_value = ["admin"]

            from app.services.sync_service import SyncService

            service = SyncService()

            # 测试标题包含季度信息
            result = service._check_season_info_in_title("测试第一季", 1)
            assert result is True

            # 测试标题不包含季度信息
            result = service._check_season_info_in_title("测试番剧", 1)
            assert result is False


class TestCustomItem:
    """CustomItem 测试"""

    def test_custom_item_all_fields(self):
        """测试所有字段"""
        item = CustomItem(
            media_type="episode",
            title="测试番剧",
            ori_title="Test Show",
            season=1,
            episode=5,
            release_date="2024-01-01",
            user_name="test_user",
            source="custom",
        )
        assert item.media_type == "episode"
        assert item.title == "测试番剧"
        assert item.ori_title == "Test Show"
        assert item.season == 1
        assert item.episode == 5
        assert item.release_date == "2024-01-01"
        assert item.user_name == "test_user"
        assert item.source == "custom"

    def test_custom_item_dict(self):
        """测试转换为字典"""
        item = CustomItem(
            media_type="episode",
            title="测试番剧",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="test",
        )
        d = item.model_dump()
        assert d["media_type"] == "episode"
        assert d["title"] == "测试番剧"
