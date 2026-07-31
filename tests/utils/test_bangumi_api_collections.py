"""Bangumi API 收藏列表批量查询测试"""

from unittest.mock import MagicMock

from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api.collection import (
    _watching_cache,
    get_watching_subject_ids,
    invalidate_watching_cache,
)


class TestListUserCollections:
    """list_user_collections 分页拉满逻辑"""

    def test_single_page(self):
        """单页即拉满"""
        api = BangumiApi()
        api.username = "test_user"
        api.get = MagicMock(
            return_value={
                "data": [{"subject_id": 1}, {"subject_id": 2}],
                "total": 2,
            }
        )
        result = api.list_user_collections(subject_type=2, collection_type=3)
        assert len(result) == 2
        assert {r["subject_id"] for r in result} == {1, 2}
        # 只调用一次（拉满即停）
        assert api.get.call_count == 1

    def test_multi_page(self):
        """多页拉取直到 total"""
        api = BangumiApi()
        api.username = "test_user"
        # 模拟 3 页：每页 2 条，total=5
        responses = [
            {"data": [{"subject_id": 1}, {"subject_id": 2}], "total": 5},
            {"data": [{"subject_id": 3}, {"subject_id": 4}], "total": 5},
            {"data": [{"subject_id": 5}], "total": 5},
        ]
        api.get = MagicMock(side_effect=responses)
        result = api.list_user_collections(limit=2)
        assert len(result) == 5
        assert api.get.call_count == 3

    def test_empty_response(self):
        """空列表立即返回"""
        api = BangumiApi()
        api.username = "test_user"
        api.get = MagicMock(return_value={"data": [], "total": 0})
        result = api.list_user_collections()
        assert result == []
        assert api.get.call_count == 1

    def test_max_total_cap(self):
        """max_total 上限截断"""
        api = BangumiApi()
        api.username = "test_user"
        # total 很大，但 max_total=3 应截断
        responses = [
            {"data": [{"subject_id": 1}, {"subject_id": 2}], "total": 100},
            {"data": [{"subject_id": 3}, {"subject_id": 4}], "total": 100},
        ]
        api.get = MagicMock(side_effect=responses)
        result = api.list_user_collections(limit=2, max_total=3)
        assert len(result) == 3  # 截断到 3 条

    def test_params_passed_through(self):
        """subject_type 和 collection_type 应作为查询参数"""
        api = BangumiApi()
        api.username = "test_user"
        api.get = MagicMock(return_value={"data": [], "total": 0})
        api.list_user_collections(subject_type=2, collection_type=3, limit=30)
        call_args = api.get.call_args
        path, params = call_args[0][0], call_args[1]["params"]
        assert path == "users/test_user/collections"
        assert params["subject_type"] == 2
        assert params["type"] == 3
        assert params["limit"] == 30
        assert params["offset"] == 0

    def test_limit_clamped_to_50(self):
        """limit 超过 API 上限 50 应被截断"""
        api = BangumiApi()
        api.username = "test_user"
        api.get = MagicMock(return_value={"data": [], "total": 0})
        api.list_user_collections(limit=200)
        params = api.get.call_args[1]["params"]
        assert params["limit"] == 50


class TestGetWatchingSubjectIds:
    """get_watching_subject_ids 缓存与降级"""

    def setup_method(self):
        """每个测试前清空缓存"""
        _watching_cache.clear()

    def test_returns_merged_ids(self):
        """同时拉动画+三次元的在看，合并返回"""
        api = BangumiApi()
        api.username = "test_user"
        anime_resp = {"data": [{"subject_id": 1}, {"subject_id": 2}], "total": 2}
        real_resp = {"data": [{"subject_id": 10}], "total": 1}
        api.get = MagicMock(side_effect=[anime_resp, real_resp])
        ids = get_watching_subject_ids(api)
        assert ids == {1, 2, 10}

    def test_cache_hit_avoids_api(self):
        """缓存命中时不调 API"""
        api = BangumiApi()
        api.username = "test_user"
        resp = {"data": [{"subject_id": 1}], "total": 1}
        api.get = MagicMock(return_value=resp)
        # 第一次调用，写缓存
        ids1 = get_watching_subject_ids(api)
        assert ids1 == {1}
        assert api.get.call_count == 2  # 动画 + 三次元各一次
        # 第二次调用，命中缓存，不再调 API
        ids2 = get_watching_subject_ids(api)
        assert ids2 == {1}
        assert api.get.call_count == 2  # 仍是 2，没新增调用

    def test_api_failure_falls_back_to_cache(self):
        """API 失败时降级返回缓存值"""
        api = BangumiApi()
        api.username = "test_user"
        # 第一次成功，写缓存
        api.get = MagicMock(return_value={"data": [{"subject_id": 1}], "total": 1})
        ids1 = get_watching_subject_ids(api)
        assert ids1 == {1}
        # 第二次 API 失败，应降级返回缓存
        api.get = MagicMock(side_effect=Exception("network error"))
        ids2 = get_watching_subject_ids(api)
        assert ids2 == {1}  # 返回缓存值

    def test_api_failure_no_cache_returns_empty(self):
        """API 失败且无缓存时返回空集合"""
        api = BangumiApi()
        api.username = "test_user"
        api.get = MagicMock(side_effect=Exception("network error"))
        ids = get_watching_subject_ids(api)
        assert ids == set()

    def test_no_username_returns_empty(self):
        """username 为空时返回空集合"""
        api = BangumiApi()
        api.username = None
        api.get = MagicMock()
        ids = get_watching_subject_ids(api)
        assert ids == set()
        api.get.assert_not_called()

    def test_invalidate_specific_user(self):
        """失效指定用户缓存"""
        api = BangumiApi()
        api.username = "test_user"
        api.get = MagicMock(return_value={"data": [{"subject_id": 1}], "total": 1})
        get_watching_subject_ids(api)
        assert "test_user" in _watching_cache
        invalidate_watching_cache("test_user")
        assert "test_user" not in _watching_cache

    def test_invalidate_all(self):
        """清空全部缓存"""
        api = BangumiApi()
        api.username = "test_user"
        api.get = MagicMock(return_value={"data": [{"subject_id": 1}], "total": 1})
        get_watching_subject_ids(api)
        assert len(_watching_cache) > 0
        invalidate_watching_cache()
        assert len(_watching_cache) == 0
