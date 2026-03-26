"""
Bangumi API 更多测试
"""

from unittest.mock import patch

from app.utils.bangumi_api import BangumiApi


class TestBangumiApiMethods:
    """Bangumi API 方法测试"""

    @patch("app.utils.bangumi_api.requests.Session")
    def test_init_no_proxy(self, mock_session):
        """测试无代理初始化"""
        api = BangumiApi(http_proxy=None)
        assert api.http_proxy is None

    @patch("app.utils.bangumi_api.requests.Session")
    def test_init_with_ssl_verify(self, mock_session):
        """测试 SSL 验证设置"""
        api = BangumiApi(ssl_verify=True)
        assert api.ssl_verify is True

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_operations(self, mock_session):
        """测试缓存操作"""
        api = BangumiApi()

        # 测试设置缓存
        api._cache["search"]["test_key"] = {"data": "value"}
        assert "test_key" in api._cache["search"]

        # 测试删除缓存
        del api._cache["search"]["test_key"]
        assert "test_key" not in api._cache["search"]


class TestBangumiApiCache:
    """Bangumi API 缓存测试"""

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_clear_all(self, mock_session):
        """测试清除所有缓存"""
        api = BangumiApi()

        # 填充缓存
        api._cache["search"]["key1"] = "value1"
        api._cache["search_old"]["key2"] = "value2"
        api._cache["get_subject"]["key3"] = "value3"

        # 清除所有缓存
        for cache_type in api._cache:
            api._cache[cache_type].clear()

        assert len(api._cache["search"]) == 0
        assert len(api._cache["search_old"]) == 0
        assert len(api._cache["get_subject"]) == 0

    @patch("app.utils.bangumi_api.requests.Session")
    def test_cache_types(self, mock_session):
        """测试缓存类型"""
        api = BangumiApi()

        # 测试各种缓存类型
        api._cache["search"]["test"] = "value"
        api._cache["search_old"]["test"] = "value"
        api._cache["get_subject"]["test"] = "value"
        api._cache["get_related_subjects"]["test"] = "value"
        api._cache["get_episodes"]["test"] = "value"

        assert len(api._cache) == 5
