"""
More comprehensive tests for bangumi_api
"""

from unittest.mock import patch

from app.utils.bangumi_api import BangumiApi


class TestBangumiApiFull:
    """Bangumi API 完整测试"""

    @patch("app.utils.bangumi_api.requests.Session")
    def test_multiple_instances(self, mock_session):
        """测试多个实例"""
        api1 = BangumiApi(username="user1")
        api2 = BangumiApi(username="user2")

        assert api1.username == "user1"
        assert api2.username == "user2"

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_isolation(self, mock_session):
        """测试缓存隔离"""
        api1 = BangumiApi(username="user1")
        api2 = BangumiApi(username="user2")

        api1._cache["search"]["key"] = "value1"
        api2._cache["search"]["key"] = "value2"

        assert api1._cache["search"]["key"] == "value1"
        assert api2._cache["search"]["key"] == "value2"

    @patch("app.utils.bangumi_api.requests.Session")
    def test_proxy_failed_flag_independent(self, mock_session):
        """测试代理失败标志独立"""
        api1 = BangumiApi(http_proxy="http://proxy1:8080")
        api2 = BangumiApi(http_proxy="http://proxy2:8080")

        api1._proxy_failed = True

        assert api1._proxy_failed is True
        assert api2._proxy_failed is False


class TestBangumiApiCacheFull:
    """Bangumi API 缓存完整测试"""

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_search(self, mock_session):
        """测试搜索缓存"""
        api = BangumiApi()
        api._cache["search"]["anime1"] = {"id": 1, "name": "Anime 1"}
        api._cache["search"]["anime2"] = {"id": 2, "name": "Anime 2"}

        assert len(api._cache["search"]) == 2

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_search_old(self, mock_session):
        """测试旧搜索缓存"""
        api = BangumiApi()
        api._cache["search_old"]["query"] = "result"

        assert api._cache["search_old"]["query"] == "result"

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_get_subject(self, mock_session):
        """测试获取 subject 缓存"""
        api = BangumiApi()
        api._cache["get_subject"]["12345"] = {"id": 12345, "name": "Test"}

        assert api._cache["get_subject"]["12345"]["id"] == 12345

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_get_related(self, mock_session):
        """测试关联条目缓存"""
        api = BangumiApi()
        api._cache["get_related_subjects"]["123"] = [{"id": 1}, {"id": 2}]

        assert len(api._cache["get_related_subjects"]["123"]) == 2

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_get_episodes(self, mock_session):
        """测试剧集缓存"""
        api = BangumiApi()
        api._cache["get_episodes"]["12345"] = [{"id": 1}, {"id": 2}, {"id": 3}]

        assert len(api._cache["get_episodes"]["12345"]) == 3
