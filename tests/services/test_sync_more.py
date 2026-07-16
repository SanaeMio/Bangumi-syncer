"""
更多 sync_service 测试
"""

from unittest.mock import patch

from app.models.sync import CustomItem
from app.services.sync_service.title_normalize import TitleNormalizeMixin


class TestTitleNormalize:
    """标题归一化与候选排序测试"""

    def test_normalize_title_strips_release_group_markers(self):
        """去除方括号包裹的发布组/分辨率标记"""
        assert (
            TitleNormalizeMixin.normalize_title("[ANi] Test Anime 1080p")
            == "Test Anime"
        )

    def test_normalize_title_strips_resolution_keywords(self):
        """去除裸露的分辨率/编码关键词"""
        assert (
            TitleNormalizeMixin.normalize_title("Test Anime 1080p HEVC") == "Test Anime"
        )

    def test_normalize_title_collapses_multiple_spaces(self):
        """折叠连续空白"""
        assert (
            TitleNormalizeMixin.normalize_title("Test   Anime    S1") == "Test Anime S1"
        )

    def test_normalize_title_translates_chinese_punctuation(self):
        """中文标点归一化为半角（首尾标点会被剥离）"""
        assert TitleNormalizeMixin.normalize_title("测试：番剧。") == "测试:番剧"
        # 标点在中间时保留
        assert (
            TitleNormalizeMixin.normalize_title("测试：番剧 第二季")
            == "测试:番剧 第二季"
        )

    def test_normalize_title_strips_file_extension(self):
        """去除文件扩展名残留"""
        assert TitleNormalizeMixin.normalize_title("Test Anime.mp4") == "Test Anime"

    def test_normalize_title_strips_fps_markers(self):
        """去除帧率标记"""
        assert TitleNormalizeMixin.normalize_title("Test Anime 60fps") == "Test Anime"

    def test_normalize_title_empty_input(self):
        """空输入返回空字符串"""
        assert TitleNormalizeMixin.normalize_title("") == ""

    def test_normalize_title_preserves_season_keywords(self):
        """保留季度关键词（不剥离 Season/第X季 等）"""
        result = TitleNormalizeMixin.normalize_title("[BD] 某番 第二季 1080p")
        assert "第二季" in result
        assert "[BD]" not in result
        assert "1080p" not in result

    def test_normalize_title_strips_leading_trailing_punctuation(self):
        """去除首尾标点与空白"""
        assert TitleNormalizeMixin.normalize_title("  - Test Anime -  ") == "Test Anime"

    def test_sort_candidates_tv_mode_prefers_tv(self):
        """非剧场版场景下 TV 排在 OVA/剧场版之前"""
        candidates = [
            {"id": 1, "name": "A", "platform": "OVA"},
            {"id": 2, "name": "B", "platform": "TV"},
            {"id": 3, "name": "C", "platform": "剧场版"},
        ]
        result = TitleNormalizeMixin._sort_candidates_by_platform(
            candidates, is_movie=False
        )
        assert result[0]["id"] == 2  # TV 优先

    def test_sort_candidates_movie_mode_prefers_movie(self):
        """剧场版场景下 剧场版/电影 排在 TV 之前"""
        candidates = [
            {"id": 1, "name": "A", "platform": "TV"},
            {"id": 2, "name": "B", "platform": "剧场版"},
            {"id": 3, "name": "C", "platform": "OVA"},
        ]
        result = TitleNormalizeMixin._sort_candidates_by_platform(
            candidates, is_movie=True
        )
        assert result[0]["id"] == 2  # 剧场版 优先

    def test_sort_candidates_respects_limit(self):
        """limit 参数限制返回数量"""
        candidates = [{"id": i, "platform": "TV"} for i in range(10)]
        result = TitleNormalizeMixin._sort_candidates_by_platform(candidates, limit=3)
        assert len(result) == 3

    def test_sort_candidates_empty_list(self):
        """空列表返回空列表"""
        assert TitleNormalizeMixin._sort_candidates_by_platform([]) == []

    def test_sort_candidates_stable_for_equal_weights(self):
        """同权重候选项保持原始顺序（稳定排序）"""
        candidates = [
            {"id": 1, "platform": "TV"},
            {"id": 2, "platform": "TV"},
            {"id": 3, "platform": "TV"},
        ]
        result = TitleNormalizeMixin._sort_candidates_by_platform(candidates)
        assert [c["id"] for c in result] == [1, 2, 3]

    def test_sort_candidates_unknown_platform_uses_default_weight(self):
        """未知 platform 使用默认权重，不报错"""
        candidates = [
            {"id": 1, "platform": "未知形态"},
            {"id": 2, "platform": "TV"},
        ]
        result = TitleNormalizeMixin._sort_candidates_by_platform(candidates)
        assert result[0]["id"] == 2  # TV > 未知

    def test_sort_candidates_non_list_input_returns_unchanged(self):
        """非列表输入原样返回（防御异常调用方）"""
        assert TitleNormalizeMixin._sort_candidates_by_platform(None) is None


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

    def test_get_explicit_season_from_title(self):
        """测试从标题提取明确声明的季度编号"""
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

            # 无季度声明 → None（可能是第一季本体）
            assert service._get_explicit_season_from_title("凡人修仙传") is None
            assert service._get_explicit_season_from_title("") is None
            assert service._get_explicit_season_from_title("鬼滅の刃") is None

            # 阿拉伯数字"第N季/第N期"
            assert service._get_explicit_season_from_title("凡人修仙传 第五季") == 5
            assert service._get_explicit_season_from_title("某番剧 第3季") == 3
            assert service._get_explicit_season_from_title("某番剧 第2期") == 2

            # 中文数字"第N季/第N期"
            assert service._get_explicit_season_from_title("凡人修仙传 第五季") == 5
            assert service._get_explicit_season_from_title("某番剧 第二季") == 2
            assert service._get_explicit_season_from_title("某番剧 第十一季") == 11

            # 英文"Xnd/Xrd/Xth season" / "Season X"
            assert service._get_explicit_season_from_title("Anime 2nd season") == 2
            assert service._get_explicit_season_from_title("Anime Season 3") == 3

            # 第一季也应识别为 1（用于判断"明确声明了季度"）
            assert service._get_explicit_season_from_title("某番剧 第一季") == 1


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
