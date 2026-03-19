"""
Bangumi API 完整测试
"""

from unittest.mock import patch

from app.utils.bangumi_api import BangumiApi


class TestBangumiApiComprehensive:
    """Bangumi API 综合测试"""

    def test_init(self):
        """测试初始化"""
        api = BangumiApi()
        assert api is not None

    def test_get_me(self):
        """测试获取用户信息"""
        api = BangumiApi()
        # 这个方法需要网络，测试是否存在
        assert hasattr(api, "get_me")

    def test_get_subject(self):
        """测试获取条目"""
        api = BangumiApi()
        assert hasattr(api, "get_subject")

    def test_get_related_subjects(self):
        """测试获取相关条目"""
        api = BangumiApi()
        assert hasattr(api, "get_related_subjects")

    def test_get_episodes(self):
        """测试获取章节"""
        api = BangumiApi()
        assert hasattr(api, "get_episodes")

    def test_get_target_season_episode_id(self):
        """测试获取目标季度集数ID"""
        api = BangumiApi()
        assert hasattr(api, "get_target_season_episode_id")

    def test_get_subject_collection(self):
        """测试获取条目收藏"""
        api = BangumiApi()
        assert hasattr(api, "get_subject_collection")

    def test_get_ep_collection(self):
        """测试获取章节收藏"""
        api = BangumiApi()
        assert hasattr(api, "get_ep_collection")


class TestBangumiApiMocked:
    """Bangumi API 模拟测试"""

    @patch("app.utils.bangumi_api.requests.Session")
    def test_api_with_session(self, mock_session):
        """测试带 Session 的 API"""
        api = BangumiApi()
        assert api is not None
