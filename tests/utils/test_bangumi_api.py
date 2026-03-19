"""
Bangumi API 工具测试
"""

from app.utils.bangumi_api import BangumiApi


class TestBangumiApi:
    """Bangumi API 测试"""

    def test_init_default(self):
        """测试默认初始化"""
        api = BangumiApi()
        assert api.host == "https://api.bgm.tv/v0"
        assert api.username is None
        assert api.access_token is None
        assert api.private is True
        assert api.ssl_verify is True

    def test_init_with_params(self):
        """测试带参数初始化"""
        api = BangumiApi(
            username="testuser",
            access_token="test_token",
            private=False,
            http_proxy="http://proxy:8080",
            ssl_verify=False,
        )
        assert api.username == "testuser"
        assert api.access_token == "test_token"
        assert api.private is False
        assert api.http_proxy == "http://proxy:8080"
        assert api.ssl_verify is False

    def test_init_sets_cache(self):
        """测试初始化缓存"""
        api = BangumiApi()
        assert "search" in api._cache
        assert "search_old" in api._cache
        assert "get_subject" in api._cache
        assert "get_related_subjects" in api._cache
        assert "get_episodes" in api._cache

    def test_init_proxy_failed_flag(self):
        """测试代理失败标记初始化"""
        api = BangumiApi()
        assert api._proxy_failed is False

    def test_init_sets_headers(self):
        """测试初始化设置请求头"""
        api = BangumiApi(access_token="test_token")
        # 检查 headers 是否有必要的键
        assert "Accept" in api.req.headers
        assert "User-Agent" in api.req.headers

    def test_cache_clear(self):
        """测试缓存清理"""
        api = BangumiApi()
        api._cache["search"]["test"] = "value"
        api._cache["search"].clear()
        assert api._cache["search"] == {}

    def test_cache_keys(self):
        """测试缓存键"""
        api = BangumiApi()
        cache_keys = list(api._cache.keys())
        expected_keys = [
            "search",
            "search_old",
            "get_subject",
            "get_related_subjects",
            "get_episodes",
        ]
        assert cache_keys == expected_keys


class TestBangumiApiMethods:
    """Bangumi API 方法测试"""

    def test_cache_get(self):
        """测试从缓存获取"""
        api = BangumiApi()
        api._cache["search"]["test_key"] = {"data": "test_value"}
        assert api._cache["search"].get("test_key") == {"data": "test_value"}

    def test_cache_set(self):
        """测试设置缓存"""
        api = BangumiApi()
        api._cache["search"]["new_key"] = {"data": "new_value"}
        assert api._cache["search"]["new_key"] == {"data": "new_value"}

    def test_proxy_failed_flag_set(self):
        """测试设置代理失败标记"""
        api = BangumiApi()
        api._proxy_failed = True
        assert api._proxy_failed is True

    def test_proxy_failed_flag_reset(self):
        """测试重置代理失败标记"""
        api = BangumiApi()
        api._proxy_failed = True
        api._proxy_failed = False
        assert api._proxy_failed is False
