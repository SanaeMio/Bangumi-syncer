"""
Bangumi 数据工具测试
"""

import time
from unittest.mock import MagicMock, mock_open, patch

import pytest

from app.utils.bangumi_data import BangumiData


def _make_data():
    """创建跳过初始化副作用的 BangumiData"""
    with (
        patch.object(BangumiData, "_check_and_download_cache_on_startup"),
        patch.object(BangumiData, "_preload_data_to_memory"),
        patch.object(BangumiData, "_build_tmdb_mapping"),
        patch.object(BangumiData, "_build_title_index"),
    ):
        return BangumiData()


class TestBangumiData:
    """Bangumi 数据工具测试"""

    def test_bangumi_data_init(self):
        data = BangumiData()
        assert data is not None

    @patch("app.utils.bangumi_data.requests.get")
    def test_fetch_data(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"data": []})
        mock_get.return_value = mock_response
        data = BangumiData()
        assert data is not None


class TestBangumiDataSearch:
    """Bangumi 数据搜索测试"""

    def test_search_empty(self):
        data = BangumiData()
        if hasattr(data, "search"):
            result = data.search("")
            assert result is not None

    def test_search_with_query(self):
        data = BangumiData()
        if hasattr(data, "search"):
            result = data.search("test")
            assert result is not None


class TestBangumiDataCacheValidity:
    """测试缓存有效性检查"""

    def test_is_cache_valid_no_file(self):
        data = _make_data()
        with patch("os.path.exists", return_value=False):
            assert data._is_cache_valid() is False

    def test_is_cache_valid_recent_file(self):
        data = _make_data()
        with (
            patch("os.path.exists", return_value=True),
            patch("os.path.getmtime", return_value=time.time()),
        ):
            assert data._is_cache_valid() is True

    def test_is_cache_valid_expired_file(self):
        data = _make_data()
        with (
            patch("os.path.exists", return_value=True),
            patch("os.path.getmtime", return_value=time.time() - 8 * 24 * 3600),
        ):
            assert data._is_cache_valid() is False

    def test_is_cache_valid_exception(self):
        data = _make_data()
        with (
            patch("os.path.exists", return_value=True),
            patch("os.path.getmtime", side_effect=Exception("Error")),
        ):
            assert data._is_cache_valid() is False


class TestBangumiDataDownload:
    """测试数据下载"""

    @patch("app.utils.bangumi_data._request_with_retry")
    def test_download_data_success(self, mock_request):
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b"test data"]
        mock_request.return_value = mock_response
        data = _make_data()
        data.use_cache = True
        with patch("builtins.open", MagicMock()):
            assert data._download_data() is True

    @patch("app.utils.bangumi_data._request_with_retry")
    def test_download_data_failure(self, mock_request):
        mock_request.side_effect = Exception("Network error")
        data = _make_data()
        assert data._download_data() is False

    @patch("app.utils.bangumi_data._request_with_retry")
    def test_download_data_with_proxy(self, mock_request):
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b"data"]
        mock_request.return_value = mock_response
        data = _make_data()
        data.use_cache = True
        data.http_proxy = "http://proxy:8080"
        with patch("builtins.open", MagicMock()):
            result = data._download_data()
            assert result is True
            call_kwargs = mock_request.call_args
            assert call_kwargs[1]["proxies"] == {
                "http": "http://proxy:8080",
                "https": "http://proxy:8080",
            }

    @patch("app.utils.bangumi_data._request_with_retry")
    def test_download_data_no_cache(self, mock_request):
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b"data"]
        mock_request.return_value = mock_response
        data = _make_data()
        data.use_cache = False
        result = data._download_data()
        assert result is True


class TestBangumiDataEnsureFresh:
    """测试数据新鲜度确保"""

    def test_ensure_fresh_data_no_cache(self):
        data = _make_data()
        data.use_cache = False
        assert data._ensure_fresh_data() is True

    def test_ensure_fresh_data_cache_valid(self):
        data = _make_data()
        data.use_cache = True
        with patch.object(data, "_is_cache_valid", return_value=True):
            assert data._ensure_fresh_data() is True

    def test_ensure_fresh_data_cache_expired(self):
        data = _make_data()
        data.use_cache = True
        with (
            patch.object(data, "_is_cache_valid", return_value=False),
            patch.object(data, "_download_data", return_value=True),
        ):
            assert data._ensure_fresh_data() is True

    def test_ensure_fresh_data_download_fail(self):
        data = _make_data()
        data.use_cache = True
        with (
            patch.object(data, "_is_cache_valid", return_value=False),
            patch.object(data, "_download_data", return_value=False),
        ):
            assert data._ensure_fresh_data() is False


class TestBangumiDataMatchTitle:
    """测试标题匹配"""

    def test_match_title_zh_hans_match(self):
        data = _make_data()
        item = {
            "title": "原版标题",
            "titleTranslate": {"zh-Hans": ["中文标题", "测试标题"]},
        }
        assert data._match_title(item, "中文标题") == "zh-hans"

    def test_match_title_ori_title_match(self):
        data = _make_data()
        item = {"title": "原版标题"}
        assert data._match_title(item, "其他标题", "原版标题") == "title"

    def test_match_title_title_field_match(self):
        data = _make_data()
        item = {"title": "标题"}
        assert data._match_title(item, "标题") == "title"

    def test_match_title_no_match(self):
        data = _make_data()
        item = {"title": "其他标题"}
        assert data._match_title(item, "完全不同的标题") is None

    def test_match_title_no_item(self):
        data = _make_data()
        assert data._match_title(None, "标题") is None

    def test_match_title_no_title_field(self):
        data = _make_data()
        item = {"name": "something"}
        assert data._match_title(item, "标题") is None


class TestBangumiDataMatchTitleFuzzy:
    """测试模糊匹配"""

    def test_match_title_fuzzy_zh_hans_contain(self):
        data = _make_data()
        item = {"title": "原版", "titleTranslate": {"zh-Hans": ["测试番剧第一季"]}}
        assert data._match_title_fuzzy(item, "测试番剧") is True

    def test_match_title_fuzzy_zh_hans_high_similarity(self):
        data = _make_data()
        item = {"title": "原版", "titleTranslate": {"zh-Hans": ["测试番剧第一季"]}}
        assert data._match_title_fuzzy(item, "测试番剧第二季") is True

    def test_match_title_fuzzy_ori_title_contain(self):
        data = _make_data()
        item = {"title": "原版标题动画"}
        assert data._match_title_fuzzy(item, "测试", "原版标题") is True

    def test_match_title_fuzzy_title_contain(self):
        data = _make_data()
        item = {"title": "原版标题"}
        assert data._match_title_fuzzy(item, "原版") is True

    def test_match_title_fuzzy_no_match(self):
        data = _make_data()
        item = {"title": "完全不同的标题"}
        assert data._match_title_fuzzy(item, "测试") is False

    def test_match_title_fuzzy_no_item(self):
        data = _make_data()
        assert data._match_title_fuzzy(None, "标题") is False

    def test_match_title_fuzzy_no_title_field(self):
        data = _make_data()
        item = {"name": "something"}
        assert data._match_title_fuzzy(item, "标题") is False


class TestBangumiDataGetZhHansTitles:
    """测试获取中文标题"""

    def test_get_zh_hans_titles_with_translate(self):
        data = _make_data()
        item = {"title": "原版标题", "titleTranslate": {"zh-Hans": ["中文1", "中文2"]}}
        titles = data._get_zh_hans_titles(item)
        assert "原版标题" in titles
        assert "中文1" in titles
        assert "中文2" in titles

    def test_get_zh_hans_titles_no_translate(self):
        data = _make_data()
        item = {"title": "原版标题"}
        titles = data._get_zh_hans_titles(item)
        assert titles == ["原版标题"]

    def test_get_zh_hans_titles_no_title(self):
        data = _make_data()
        item = {"titleTranslate": {"zh-Hans": ["中文"]}}
        titles = data._get_zh_hans_titles(item)
        assert titles == ["中文"]


class TestBangumiDataGetBestMatchedTitle:
    """测试获取最佳匹配标题"""

    def test_get_best_matched_title_with_zh(self):
        data = _make_data()
        item = {"title": "原版", "titleTranslate": {"zh-Hans": ["中文1", "中文2"]}}
        assert data._get_best_matched_title(item) == "中文1"

    def test_get_best_matched_title_no_zh(self):
        data = _make_data()
        item = {"title": "原版标题"}
        assert data._get_best_matched_title(item) == "原版标题"

    def test_get_best_matched_title_empty_zh(self):
        data = _make_data()
        item = {"title": "原版", "titleTranslate": {"zh-Hans": []}}
        assert data._get_best_matched_title(item) == "原版"

    def test_get_best_matched_title_no_title(self):
        data = _make_data()
        item = {}
        assert data._get_best_matched_title(item) == ""


class TestBangumiDataDateDiff:
    """测试日期差计算"""

    def test_date_diff_normal(self):
        data = _make_data()
        assert data._date_diff("2024-01-01", "2024-01-10") == 9

    def test_date_diff_same_date(self):
        data = _make_data()
        assert data._date_diff("2024-01-01", "2024-01-01") == 0

    def test_date_diff_with_time(self):
        data = _make_data()
        assert data._date_diff("2024-01-01T12:00:00", "2024-01-10T00:00:00") == 9

    def test_date_diff_invalid_date(self):
        data = _make_data()
        assert data._date_diff("invalid", "2024-01-01") == 999999


class TestBangumiDataIsDateClose:
    """测试日期接近判断"""

    def test_is_date_close_within_range(self):
        data = _make_data()
        assert data._is_date_close("2024-01-01", "2024-01-30", max_days=60) is True

    def test_is_date_close_out_of_range(self):
        data = _make_data()
        assert data._is_date_close("2024-01-01", "2024-06-01", max_days=60) is False

    def test_is_date_close_parse_error(self):
        data = _make_data()
        assert data._is_date_close("invalid", "2024-01-01") is False


class TestBangumiDataCheckKeyCharacters:
    """测试关键字符检查"""

    def test_check_key_characters_identical(self):
        data = _make_data()
        assert data._check_key_characters("测试番剧", "测试番剧") is True

    def test_check_key_characters_different(self):
        data = _make_data()
        assert data._check_key_characters("测试", "完全不同") is False

    def test_check_key_characters_with_spaces(self):
        data = _make_data()
        assert data._check_key_characters("测试 番剧", "测试番剧") is True

    def test_check_key_characters_empty(self):
        data = _make_data()
        assert data._check_key_characters("", "测试") is False

    def test_check_key_characters_short_similar(self):
        data = _make_data()
        assert data._check_key_characters("ab", "ac") is False


class TestBangumiDataExtractBangumiId:
    """测试提取Bangumi ID"""

    def test_extract_bangumi_id_found(self):
        data = _make_data()
        item = {"sites": [{"site": "bangumi", "id": "12345"}]}
        assert data._extract_bangumi_id(item) == "12345"

    def test_extract_bangumi_id_not_found(self):
        data = _make_data()
        item = {"sites": [{"site": "other", "id": "12345"}]}
        assert data._extract_bangumi_id(item) is None

    def test_extract_bangumi_id_no_sites(self):
        data = _make_data()
        item = {"title": "测试"}
        assert data._extract_bangumi_id(item) is None

    def test_extract_bangumi_id_empty_item(self):
        data = _make_data()
        assert data._extract_bangumi_id(None) is None

    def test_extract_bangumi_id_empty_id(self):
        data = _make_data()
        item = {"sites": [{"site": "bangumi", "id": ""}]}
        assert data._extract_bangumi_id(item) is None


class TestBangumiDataCacheStats:
    """测试缓存统计"""

    def test_get_cache_stats_no_requests(self):
        data = _make_data()
        data._cache_hit_count = 0
        data._cache_miss_count = 0
        data._data_cache = None
        stats = data.get_cache_stats()
        assert stats["cache_hits"] == 0
        assert stats["total_requests"] == 0
        assert stats["hit_rate"] == 0

    def test_get_cache_stats_with_hits(self):
        data = _make_data()
        data._cache_hit_count = 8
        data._cache_miss_count = 2
        data._data_cache = [1, 2, 3]
        data._cache_timestamp = time.time()
        stats = data.get_cache_stats()
        assert stats["cache_hits"] == 8
        assert stats["total_requests"] == 10
        assert stats["hit_rate"] == 80.0
        assert stats["cache_size"] == 3

    def test_get_cache_stats_no_timestamp(self):
        data = _make_data()
        data._cache_timestamp = None
        stats = data.get_cache_stats()
        assert stats["cache_age_minutes"] == 0


class TestBangumiDataClearCache:
    """测试缓存清理"""

    def test_clear_cache(self):
        data = _make_data()
        data._data_cache = [1, 2, 3]
        data._cache_timestamp = time.time()
        data._title_index = {"test": []}
        data.clear_cache()
        assert data._data_cache is None
        assert data._cache_timestamp is None
        assert len(data._title_index) == 0


class TestBangumiDataForceUpdate:
    """测试强制更新"""

    @patch("app.utils.bangumi_data.BangumiData._download_data", return_value=True)
    @patch("app.utils.bangumi_data.BangumiData.clear_cache")
    def test_force_update_success(self, mock_clear, mock_download):
        data = _make_data()
        result = data.force_update()
        assert result is True
        mock_clear.assert_called_once()

    @patch("app.utils.bangumi_data.BangumiData._download_data", return_value=False)
    def test_force_update_failure(self, mock_download):
        data = _make_data()
        result = data.force_update()
        assert result is False


class TestBangumiDataSearchTitle:
    """测试标题搜索"""

    def test_search_title_empty_query(self):
        data = _make_data()
        with patch.object(data, "_parse_data", return_value=[]):
            result = data.search_title("")
            assert isinstance(result, list)

    def test_search_title_with_results(self):
        data = _make_data()
        items = [
            {
                "title": "测试番剧",
                "titleTranslate": {"zh-Hans": ["测试"]},
                "begin": "2024-01-01",
                "sites": [{"site": "bangumi", "id": "123"}],
            }
        ]
        with patch.object(data, "_parse_data", return_value=items):
            result = data.search_title("测试")
            assert len(result) > 0

    def test_search_title_no_bangumi_site(self):
        data = _make_data()
        items = [
            {
                "title": "测试",
                "sites": [{"site": "other", "id": "123"}],
            }
        ]
        with patch.object(data, "_parse_data", return_value=items):
            result = data.search_title("测试")
            assert len(result) == 0

    def test_search_title_zh_hans_match(self):
        data = _make_data()
        items = [
            {
                "title": "原名",
                "titleTranslate": {"zh-Hans": ["匹配标题"]},
                "begin": "2024-01-01",
                "sites": [{"site": "bangumi", "id": "456"}],
            }
        ]
        with patch.object(data, "_parse_data", return_value=items):
            result = data.search_title("匹配标题")
            assert len(result) == 1
            assert result[0]["bangumi_id"] == "456"


class TestBangumiDataCalculateMatchInfo:
    """测试匹配信息计算"""

    def test_calculate_match_info_zh_hans_exact(self):
        data = _make_data()
        item = {"title": "原版", "titleTranslate": {"zh-Hans": ["测试标题"]}}
        result = data._calculate_match_info(item, "测试标题")
        assert result["exact_match"] is True
        assert result["match_type"] == "zh-hans"
        assert result["score"] == 1.0

    def test_calculate_match_info_ori_title_exact(self):
        data = _make_data()
        item = {"title": "测试标题"}
        result = data._calculate_match_info(item, "其他", "测试标题")
        assert result["exact_match"] is True
        assert result["match_type"] == "title"

    def test_calculate_match_info_title_exact_no_ori(self):
        data = _make_data()
        item = {"title": "标题"}
        result = data._calculate_match_info(item, "标题")
        assert result["exact_match"] is True

    def test_calculate_match_info_no_match(self):
        data = _make_data()
        item = {"title": "完全不同的标题"}
        result = data._calculate_match_info(item, "测试")
        assert result["exact_match"] is False
        assert result["score"] < 1.0

    def test_calculate_match_info_with_date(self):
        data = _make_data()
        item = {
            "title": "原版",
            "titleTranslate": {"zh-Hans": ["测试"]},
            "begin": "2024-01-01",
        }
        result = data._calculate_match_info(item, "测试", release_date="2024-01-15")
        assert result["score"] > 0

    def test_calculate_match_info_date_far(self):
        data = _make_data()
        item = {
            "title": "原版",
            "titleTranslate": {"zh-Hans": ["测试"]},
            "begin": "2024-01-01",
        }
        result = data._calculate_match_info(item, "测试", release_date="2024-06-01")
        assert result["score"] >= 0

    def test_calculate_match_info_zh_hans_high_similarity(self):
        """相似度>0.9 触发 exact_match"""
        data = _make_data()
        item = {"title": "原版", "titleTranslate": {"zh-Hans": ["测试番剧标题一二三"]}}
        result = data._calculate_match_info(item, "测试番剧标题一二")
        assert result["exact_match"] is True

    def test_calculate_match_info_containment_score(self):
        """包含关系得分"""
        data = _make_data()
        item = {"title": "原版", "titleTranslate": {"zh-Hans": ["测试番剧第一季"]}}
        result = data._calculate_match_info(item, "测试番剧")
        assert result["score"] > 0

    def test_calculate_match_info_ori_fuzzy_score(self):
        """ori_title 模糊匹配得分"""
        data = _make_data()
        item = {"title": "TestShowTitle"}
        result = data._calculate_match_info(item, "中文", "TestShowName")
        assert result["score"] > 0

    def test_calculate_match_info_ori_containment(self):
        """ori_title 包含关系"""
        data = _make_data()
        item = {"title": "TestShowTitleExtra"}
        result = data._calculate_match_info(item, "中文", "TestShowTitle")
        assert result["score"] > 0

    def test_calculate_match_info_title_no_ori(self):
        """无 ori_title 时用 title 匹配"""
        data = _make_data()
        item = {"title": "TestTitle"}
        result = data._calculate_match_info(item, "TestTitle")
        assert result["exact_match"] is True

    def test_calculate_match_info_no_title_translate(self):
        data = _make_data()
        item = {"title": "原版"}
        result = data._calculate_match_info(item, "", "原版")
        assert result["exact_match"] is True


class TestBangumiDataGetTitleByTmdbId:
    """测试通过TMDB ID获取标题"""

    def test_get_title_by_tmdb_id_found(self):
        data = _make_data()
        data._cache_tmdb_mapping = {"12345": "测试番剧"}
        assert data.get_title_by_tmdb_id("12345") == "测试番剧"

    def test_get_title_by_tmdb_id_not_found(self):
        data = _make_data()
        data._cache_tmdb_mapping = {}
        assert data.get_title_by_tmdb_id("99999") is None


class TestParseData:
    """测试 _parse_data"""

    def test_cache_hit(self):
        data = _make_data()
        data._data_cache = [{"title": "a"}, {"title": "b"}]
        data._cache_timestamp = time.time()
        items = list(data._parse_data())
        assert len(items) == 2
        assert data._cache_hit_count == 1

    def test_cache_miss_from_file(self):
        data = _make_data()
        data._data_cache = None
        data.use_cache = True
        data.local_cache_path = "/tmp/test_cache.json"
        mock_items = [{"title": "test"}]
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open()),
            patch("app.utils.bangumi_data.ijson.items", return_value=iter(mock_items)),
            patch.object(data, "_build_title_index"),
        ):
            items = list(data._parse_data())
            assert len(items) == 1
            assert data._cache_miss_count == 1

    def test_cache_miss_file_parse_fail_redownload(self):
        data = _make_data()
        data._data_cache = None
        data.use_cache = True
        data.local_cache_path = "/tmp/test_cache.json"
        mock_items = [{"title": "test"}]
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open()),
            patch(
                "app.utils.bangumi_data.ijson.items",
                side_effect=[Exception("parse err"), iter(mock_items)],
            ),
            patch.object(data, "_download_data", return_value=True),
            patch.object(data, "_build_title_index"),
        ):
            items = list(data._parse_data())
            assert len(items) == 1

    def test_cache_miss_network(self):
        data = _make_data()
        data._data_cache = None
        data.use_cache = False
        data.local_cache_path = "/tmp/test_cache.json"
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=False),
            patch("app.utils.bangumi_data._request_with_retry") as mock_req,
            patch("app.utils.bangumi_data.ijson.items", return_value=iter([])),
            patch.object(data, "_build_title_index"),
        ):
            mock_resp = MagicMock()
            mock_resp.raw = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_req.return_value = mock_resp
            items = list(data._parse_data())
            assert isinstance(items, list)

    def test_cache_miss_network_fail_fallback_cache(self):
        data = _make_data()
        data._data_cache = None
        data.use_cache = False
        data.local_cache_path = "/tmp/test_cache.json"
        mock_items = [{"title": "fallback"}]
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=True),
            patch(
                "app.utils.bangumi_data._request_with_retry",
                side_effect=Exception("network err"),
            ),
            patch("builtins.open", mock_open()),
            patch("app.utils.bangumi_data.ijson.items", return_value=iter(mock_items)),
            patch.object(data, "_build_title_index"),
        ):
            items = list(data._parse_data())
            assert len(items) == 1

    def test_cache_miss_network_fail_no_fallback(self):
        data = _make_data()
        data._data_cache = None
        data.use_cache = False
        data.local_cache_path = "/tmp/test_cache.json"
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=False),
            patch(
                "app.utils.bangumi_data._request_with_retry",
                side_effect=Exception("network err"),
            ),
            patch.object(data, "_build_title_index"),
        ):
            items = list(data._parse_data())
            assert items == []


class TestCheckAndDownloadCacheOnStartup:
    """测试 _check_and_download_cache_on_startup"""

    def test_no_cache(self):
        data = _make_data()
        data.use_cache = False
        data._check_and_download_cache_on_startup()

    def test_file_not_exists_download_success(self):
        data = _make_data()
        data.use_cache = True
        with (
            patch("os.path.exists", return_value=False),
            patch.object(data, "_download_data", return_value=True),
        ):
            data._check_and_download_cache_on_startup()

    def test_file_not_exists_download_fail(self):
        data = _make_data()
        data.use_cache = True
        with (
            patch("os.path.exists", return_value=False),
            patch.object(data, "_download_data", return_value=False),
        ):
            data._check_and_download_cache_on_startup()

    def test_file_expired_update_success(self):
        data = _make_data()
        data.use_cache = True
        with (
            patch("os.path.exists", return_value=True),
            patch.object(data, "_is_cache_valid", return_value=False),
            patch.object(data, "_download_data", return_value=True),
        ):
            data._check_and_download_cache_on_startup()

    def test_file_expired_update_fail(self):
        data = _make_data()
        data.use_cache = True
        with (
            patch("os.path.exists", return_value=True),
            patch.object(data, "_is_cache_valid", return_value=False),
            patch.object(data, "_download_data", return_value=False),
        ):
            data._check_and_download_cache_on_startup()

    def test_file_valid(self):
        data = _make_data()
        data.use_cache = True
        with (
            patch("os.path.exists", return_value=True),
            patch.object(data, "_is_cache_valid", return_value=True),
        ):
            data._check_and_download_cache_on_startup()


class TestPreloadDataToMemory:
    """测试 _preload_data_to_memory"""

    def test_cache_file_preload(self):
        data = _make_data()
        data.use_cache = True
        data.local_cache_path = "/tmp/test.json"
        mock_items = [{"title": "a"}]
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open()),
            patch("app.utils.bangumi_data.ijson.items", return_value=iter(mock_items)),
        ):
            data._preload_data_to_memory()
            assert data._data_cache == mock_items

    def test_cache_file_parse_error(self):
        data = _make_data()
        data.use_cache = True
        data.local_cache_path = "/tmp/test.json"
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open()),
            patch(
                "app.utils.bangumi_data.ijson.items",
                side_effect=Exception("parse err"),
            ),
        ):
            data._preload_data_to_memory()
            assert data._data_cache is None

    def test_network_preload(self):
        data = _make_data()
        data.use_cache = False
        data.local_cache_path = "/tmp/test.json"
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=False),
            patch("app.utils.bangumi_data._request_with_retry") as mock_req,
            patch("app.utils.bangumi_data.ijson.items", return_value=iter([])),
        ):
            mock_resp = MagicMock()
            mock_resp.raw = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_req.return_value = mock_resp
            data._preload_data_to_memory()
            assert data._data_cache == []

    def test_network_error(self):
        data = _make_data()
        data.use_cache = False
        data.local_cache_path = "/tmp/test.json"
        with (
            patch.object(data, "_ensure_fresh_data"),
            patch("os.path.exists", return_value=False),
            patch(
                "app.utils.bangumi_data._request_with_retry",
                side_effect=Exception("fail"),
            ),
        ):
            data._preload_data_to_memory()
            assert data._data_cache is None

    def test_exception_handler(self):
        data = _make_data()
        with patch.object(
            data, "_ensure_fresh_data", side_effect=RuntimeError("fatal")
        ):
            data._preload_data_to_memory()


class TestBuildTmdbMapping:
    """测试 _build_tmdb_mapping"""

    def test_build_mapping(self):
        data = _make_data()
        items = [
            {
                "title": "番剧A",
                "sites": [
                    {"site": "tmdb", "id": "tv/12345"},
                    {"site": "bangumi", "id": "100"},
                ],
            },
            {
                "title": "番剧B",
                "sites": [{"site": "bangumi", "id": "200"}],
            },
        ]
        with patch.object(data, "_parse_data", return_value=items):
            data._build_tmdb_mapping()
            assert data._cache_tmdb_mapping.get("tv/12345") == "番剧A"
            assert len(data._cache_tmdb_mapping) == 1


class TestBuildTitleIndex:
    """测试 _build_title_index"""

    def test_build_index(self):
        data = _make_data()
        items = [
            {
                "title": "原名",
                "titleTranslate": {"zh-Hans": ["中文1", "中文2"]},
            },
            {"title": "原名2"},
            {"title": ""},
        ]
        with patch.object(data, "_parse_data", return_value=items):
            data._build_title_index()
            assert "原名" in data._title_index
            assert "中文1" in data._title_index
            assert "中文2" in data._title_index
            assert "原名2" in data._title_index
            assert "" not in data._title_index


class TestFindBangumiIdOptimizedTitleIndex:
    """测试 _find_bangumi_id_optimized 标题索引命中"""

    def test_exact_index_hit_with_date(self):
        data = _make_data()
        data._title_index = {
            "标题": [
                {
                    "title": "标题",
                    "begin": "2024-01-15",
                    "sites": [{"site": "bangumi", "id": "100"}],
                }
            ]
        }
        result = data._find_bangumi_id_optimized("标题", release_date="2024-01-16")
        assert result is not None
        assert result[0] == "100"
        assert result[2] is True

    def test_exact_index_hit_no_date(self):
        data = _make_data()
        data._title_index = {
            "标题": [
                {
                    "title": "标题",
                    "sites": [{"site": "bangumi", "id": "200"}],
                }
            ]
        }
        with patch.object(data, "_parse_data", return_value=[]):
            result = data._find_bangumi_id_optimized("标题")
        assert result is not None
        assert result[0] == "200"
        assert result[2] is False

    def test_exact_index_hit_ori_title(self):
        data = _make_data()
        data._title_index = {
            "OriginalTitle": [
                {
                    "title": "OriginalTitle",
                    "sites": [{"site": "bangumi", "id": "300"}],
                }
            ]
        }
        with patch.object(data, "_parse_data", return_value=[]):
            result = data._find_bangumi_id_optimized("中文", ori_title="OriginalTitle")
        assert result is not None
        assert result[0] == "300"

    def test_exact_index_hit_with_date_best_match(self):
        """多个精确匹配时选择日期最近的"""
        data = _make_data()
        data._title_index = {
            "标题": [
                {
                    "title": "标题",
                    "begin": "2024-01-01",
                    "sites": [{"site": "bangumi", "id": "100"}],
                },
                {
                    "title": "标题",
                    "begin": "2024-06-01",
                    "sites": [{"site": "bangumi", "id": "200"}],
                },
            ]
        }
        result = data._find_bangumi_id_optimized("标题", release_date="2024-05-30")
        assert result is not None
        assert result[0] == "200"

    def test_exact_index_date_diff_gt_180_falls_through(self):
        """标题索引命中但日期差>180天时，应回退线性扫描检查部分匹配"""
        data = _make_data()
        # 标题索引只有第一季（2016年）
        data._title_index = {
            "Re：从零开始的异世界生活": [
                {
                    "title": "Re:ゼロから始める異世界生活",
                    "begin": "2016-04-01",
                    "titleTranslate": {"zh-Hans": ["Re：从零开始的异世界生活"]},
                    "sites": [{"site": "bangumi", "id": "140001"}],
                }
            ]
        }
        # 线性扫描数据包含第一季和第四季
        all_items = [
            {
                "title": "Re:ゼロから始める異世界生活",
                "begin": "2016-04-01",
                "titleTranslate": {"zh-Hans": ["Re：从零开始的异世界生活"]},
                "sites": [{"site": "bangumi", "id": "140001"}],
            },
            {
                "title": "Re:ゼロから始める異世界生活 第4期",
                "begin": "2026-04-01",
                "titleTranslate": {
                    "zh-Hans": [
                        "Re：从零开始的异世界生活 第四季",
                        "Re：从零开始的异世界生活 第四季 丧失篇",
                    ]
                },
                "sites": [{"site": "bangumi", "id": "547888"}],
            },
        ]
        with patch.object(data, "_parse_data", return_value=all_items):
            result = data._find_bangumi_id_optimized(
                "Re：从零开始的异世界生活", release_date="2026-04-28"
            )
        assert result is not None
        assert result[0] == "547888"
        assert result[2] is True  # date_matched

    def test_exact_index_date_diff_le_180_returns_directly(self):
        """标题索引命中且日期差≤180天时，直接返回不回退"""
        data = _make_data()
        data._title_index = {
            "标题": [
                {
                    "title": "标题",
                    "begin": "2024-01-01",
                    "sites": [{"site": "bangumi", "id": "100"}],
                }
            ]
        }
        with patch.object(data, "_parse_data", return_value=[]):
            result = data._find_bangumi_id_optimized("标题", release_date="2024-06-01")
        assert result is not None
        assert result[0] == "100"

    def test_mushoku_tensei_s2_episode_24_returns_correct_id(self):
        """无职转生 S2E24 (2024-04-07) 搜索标题匹配到S1，日期差>180天，
        180天回退机制检查部分匹配，S2 Part 2日期差=0天且标题包含搜索词，
        直接返回S2 Part 2 (ID=444557, date_matched=True)"""
        data = _make_data()
        # 标题索引：搜索词"无职转生～到了异世界就拿出真本事～"精确匹配S1
        data._title_index = {
            "无职转生～到了异世界就拿出真本事～": [
                {
                    "title": "無職転生～異世界行ったら本気だす～",
                    "begin": "2021-01-10T15:00:00.000Z",
                    "titleTranslate": {
                        "zh-Hans": ["无职转生～到了异世界就拿出真本事～"]
                    },
                    "sites": [{"site": "bangumi", "id": "277554"}],
                }
            ]
        }
        all_items = [
            # S1 - 精确匹配
            {
                "title": "無職転生～異世界行ったら本気だす～",
                "begin": "2021-01-10T15:00:00.000Z",
                "titleTranslate": {"zh-Hans": ["无职转生～到了异世界就拿出真本事～"]},
                "sites": [{"site": "bangumi", "id": "277554"}],
            },
            # S2 Part 1 - 部分匹配（标题包含搜索词）
            {
                "title": "無職転生Ⅱ ～異世界行ったら本気だす～",
                "begin": "2023-07-02T15:00:00.000Z",
                "titleTranslate": {
                    "zh-Hans": [
                        "无职转生Ⅱ ～到了异世界就拿出真本事～",
                        "无职转生 ～在异世界认真地活下去～ 第二季",
                    ]
                },
                "sites": [{"site": "bangumi", "id": "373247"}],
            },
            # S2 Part 2 (ID=444557) - 不同标题，不在索引候选中
            {
                "title": "無職転生Ⅱ ～異世界行ったら本気だす～(第2クール)",
                "begin": "2024-04-07T15:00:00.000Z",
                "titleTranslate": {
                    "zh-Hans": [
                        "无职转生Ⅱ ～到了异世界就拿出真本事～ 第2部分",
                        "无职转生 ～在异世界认真地活下去～ 第二季 第2部分",
                    ]
                },
                "sites": [{"site": "bangumi", "id": "444557"}],
            },
        ]
        with patch.object(data, "_parse_data", return_value=all_items):
            # S2E24 首播日期 2024-04-07
            result = data._find_bangumi_id_optimized(
                "无职转生～到了异世界就拿出真本事～",
                release_date="2024-04-07",
            )
        # 180天回退：S2 Part 2 日期差=0天，标题包含搜索词，直接返回
        assert result is not None
        assert result[0] == "444557"
        assert result[2] is True  # date_matched=True

    def test_oshi_no_ko_s3_matches_517057(self):
        """【我推的孩子】 S03E01 (2026-01-14) 应匹配到 S3 (ID=517057)"""
        data = _make_data()
        data._title_index = {
            "【我推的孩子】": [
                {
                    "title": "【推しの子】",
                    "begin": "2023-04-12T14:00:00.000Z",
                    "titleTranslate": {"zh-Hans": ["【我推的孩子】"]},
                    "sites": [{"site": "bangumi", "id": "386809"}],
                }
            ]
        }
        all_items = [
            {
                "title": "【推しの子】",
                "begin": "2023-04-12T14:00:00.000Z",
                "titleTranslate": {"zh-Hans": ["【我推的孩子】"]},
                "sites": [{"site": "bangumi", "id": "386809"}],
            },
            {
                "title": "【推しの子】(第2期)",
                "begin": "2024-07-03T14:00:00.000Z",
                "titleTranslate": {"zh-Hans": ["【我推的孩子】 第二季"]},
                "sites": [{"site": "bangumi", "id": "443428"}],
            },
            {
                "title": "【推しの子】(第3期)",
                "begin": "2026-01-14T14:00:00.000Z",
                "titleTranslate": {"zh-Hans": ["【我推的孩子】 第三季"]},
                "sites": [{"site": "bangumi", "id": "517057"}],
            },
        ]
        with patch.object(data, "_parse_data", return_value=all_items):
            result = data._find_bangumi_id_optimized(
                "【我推的孩子】", release_date="2026-01-14"
            )
        assert result is not None
        assert result[0] == "517057"
        assert result[2] is True

    def test_kanojo_okarishimasu_s5_matches_533027(self):
        """租借女友 S05E01 (2026-04-09) 应匹配到 S5 (ID=533027)"""
        data = _make_data()
        data._title_index = {
            "租借女友": [
                {
                    "title": "彼女、お借りします",
                    "begin": "2020-07-10T16:25:00.000Z",
                    "titleTranslate": {"zh-Hans": ["租借女友"]},
                    "sites": [{"site": "bangumi", "id": "296076"}],
                }
            ]
        }
        all_items = [
            {
                "title": "彼女、お借りします",
                "begin": "2020-07-10T16:25:00.000Z",
                "titleTranslate": {"zh-Hans": ["租借女友"]},
                "sites": [{"site": "bangumi", "id": "296076"}],
            },
            {
                "title": "彼女、お借りします(第2期)",
                "begin": "2022-07-01T16:25:00.000Z",
                "titleTranslate": {"zh-Hans": ["租借女友 第二季"]},
                "sites": [{"site": "bangumi", "id": "315745"}],
            },
            {
                "title": "彼女、お借りします(第3期)",
                "begin": "2023-07-06T16:23:00.000Z",
                "titleTranslate": {"zh-Hans": ["租借女友 第三季"]},
                "sites": [{"site": "bangumi", "id": "401783"}],
            },
            {
                "title": "彼女、お借りします(第4期)",
                "begin": "2025-07-03T17:23:00.000Z",
                "titleTranslate": {"zh-Hans": ["租借女友 第四季"]},
                "sites": [{"site": "bangumi", "id": "503450"}],
            },
            {
                "title": "彼女、お借りします(第5期)",
                "begin": "2026-04-09T16:53:00.000Z",
                "titleTranslate": {"zh-Hans": ["租借女友 第五季"]},
                "sites": [{"site": "bangumi", "id": "533027"}],
            },
        ]
        with patch.object(data, "_parse_data", return_value=all_items):
            result = data._find_bangumi_id_optimized(
                "租借女友", release_date="2026-04-09"
            )
        assert result is not None
        assert result[0] == "533027"
        assert result[2] is True

    def test_youkoso_jitsuryoku_s4_matches_510710(self):
        """欢迎来到实力至上主义教室 S04E01 (2026-04-01) 应匹配到 S4 (ID=510710)"""
        data = _make_data()
        data._title_index = {
            "欢迎来到实力至上主义教室": [
                {
                    "title": "ようこそ実力至上主義の教室へ",
                    "begin": "2017-07-12T14:30:00.000Z",
                    "titleTranslate": {"zh-Hans": ["欢迎来到实力至上主义教室"]},
                    "sites": [{"site": "bangumi", "id": "214272"}],
                }
            ]
        }
        all_items = [
            {
                "title": "ようこそ実力至上主義の教室へ",
                "begin": "2017-07-12T14:30:00.000Z",
                "titleTranslate": {"zh-Hans": ["欢迎来到实力至上主义教室"]},
                "sites": [{"site": "bangumi", "id": "214272"}],
            },
            {
                "title": "ようこそ実力至上主義の教室へ 2nd Season",
                "begin": "2022-07-04T12:00:00.000Z",
                "titleTranslate": {
                    "zh-Hans": [
                        "欢迎来到实力至上主义教室 2nd Season",
                        "欢迎来到实力至上主义教室 第二季",
                    ]
                },
                "sites": [{"site": "bangumi", "id": "371546"}],
            },
            {
                "title": "ようこそ実力至上主義の教室へ 3rd Season",
                "begin": "2024-01-03T13:30:00.000Z",
                "titleTranslate": {"zh-Hans": ["欢迎来到实力至上主义教室 第三季"]},
                "sites": [{"site": "bangumi", "id": "373266"}],
            },
            {
                "title": "ようこそ実力至上主義の教室へ 4th Season 2年生編1学期",
                "begin": "2026-04-01T13:00:00.000Z",
                "titleTranslate": {"zh-Hans": ["欢迎来到实力至上主义教室 第四季"]},
                "sites": [{"site": "bangumi", "id": "510710"}],
            },
        ]
        with patch.object(data, "_parse_data", return_value=all_items):
            result = data._find_bangumi_id_optimized(
                "欢迎来到实力至上主义教室", release_date="2026-04-01"
            )
        assert result is not None
        assert result[0] == "510710"
        assert result[2] is True

    def test_maou_gakuen_s2_matches_455981(self):
        """魔王学院的不适任者 S2E13 (2024-04-12) 应匹配到 S2 Part 2 (ID=455981)"""
        data = _make_data()
        data._title_index = {
            "魔王学院的不适任者～史上最强的魔王始祖，转生就读子孙们的学校～": [
                {
                    "title": "魔王学院の不適合者～史上最強の魔王の始祖、転生して子孫たちの学校へ通う～",
                    "begin": "2020-07-04T14:30:00.000Z",
                    "titleTranslate": {
                        "zh-Hans": [
                            "魔王学院的不适任者～史上最强的魔王始祖，转生就读子孙们的学校～"
                        ]
                    },
                    "sites": [{"site": "bangumi", "id": "292222"}],
                }
            ]
        }
        all_items = [
            {
                "title": "魔王学院の不適合者～史上最強の魔王の始祖、転生して子孫たちの学校へ通う～",
                "begin": "2020-07-04T14:30:00.000Z",
                "titleTranslate": {
                    "zh-Hans": [
                        "魔王学院的不适任者～史上最强的魔王始祖，转生就读子孙们的学校～"
                    ]
                },
                "sites": [{"site": "bangumi", "id": "292222"}],
            },
            {
                "title": "魔王学院の不適合者II～史上最強の魔王の始祖、転生して子孫たちの学校へ通う～",
                "begin": "2023-01-07T15:30:00.000Z",
                "titleTranslate": {
                    "zh-Hans": [
                        "魔王学院的不适任者～史上最强的魔王始祖，转生就读子孙们的学校～ Ⅱ",
                        "魔王学院的不适任者～史上最强的魔王始祖，转生就读子孙们的学校～ 第二季",
                    ]
                },
                "sites": [{"site": "bangumi", "id": "330054"}],
            },
            {
                "title": "魔王学院の不適合者II～史上最強の魔王の始祖、転生して子孫たちの学校へ通う～(2ndクール)",
                "begin": "2024-04-12T13:00:00.000Z",
                "titleTranslate": {
                    "zh-Hans": [
                        "魔王学院的不适任者～史上最强的魔王始祖，转生就读子孙们的学校～ 第二季 第2部分"
                    ]
                },
                "sites": [{"site": "bangumi", "id": "455981"}],
            },
        ]
        with patch.object(data, "_parse_data", return_value=all_items):
            result = data._find_bangumi_id_optimized(
                "魔王学院的不适任者～史上最强的魔王始祖，转生就读子孙们的学校～",
                release_date="2024-04-12",
            )
        assert result is not None
        assert result[0] == "455981"
        assert result[2] is True

    def test_aharen_san_s2_matches_506922(self):
        """测不准的阿波连同学 S2E9 (2025-04-07) 应匹配到 S2 (ID=506922)"""
        data = _make_data()
        data._title_index = {
            "测不准的阿波连同学": [
                {
                    "title": "阿波連さんははかれない",
                    "begin": "2022-04-01T17:25:00.000Z",
                    "titleTranslate": {"zh-Hans": ["测不准的阿波连同学"]},
                    "sites": [{"site": "bangumi", "id": "343656"}],
                }
            ]
        }
        all_items = [
            {
                "title": "阿波連さんははかれない",
                "begin": "2022-04-01T17:25:00.000Z",
                "titleTranslate": {"zh-Hans": ["测不准的阿波连同学"]},
                "sites": [{"site": "bangumi", "id": "343656"}],
            },
            {
                "title": "阿波連さんははかれない season2",
                "begin": "2025-04-07T13:00:00.000Z",
                "titleTranslate": {
                    "zh-Hans": [
                        "测不准的阿波连同学 第二季",
                        "不会拿捏距离的阿波连同学 第二季",
                    ]
                },
                "sites": [{"site": "bangumi", "id": "506922"}],
            },
        ]
        with patch.object(data, "_parse_data", return_value=all_items):
            result = data._find_bangumi_id_optimized(
                "测不准的阿波连同学", release_date="2025-04-07"
            )
        assert result is not None
        assert result[0] == "506922"
        assert result[2] is True

    def test_akane_banashi_chinese_title_matches_576121(self):
        """落语朱音 S1E1 (中文标题) 应匹配到 576121"""
        data = _make_data()
        data._title_index = {
            "落语朱音": [
                {
                    "title": "あかね噺",
                    "begin": "2026-04-04T14:30:00.000Z",
                    "titleTranslate": {"zh-Hans": ["落语朱音", "朱音落语"]},
                    "sites": [{"site": "bangumi", "id": "576121"}],
                }
            ]
        }
        with patch.object(data, "_parse_data", return_value=[]):
            result = data._find_bangumi_id_optimized(
                "落语朱音", release_date="2026-04-04"
            )
        assert result is not None
        assert result[0] == "576121"
        assert result[2] is True

    def test_akane_banashi_japanese_title_matches_576121(self):
        """あかね噺 S1E1 (日文原标题) 应匹配到 576121"""
        data = _make_data()
        data._title_index = {
            "あかね噺": [
                {
                    "title": "あかね噺",
                    "begin": "2026-04-04T14:30:00.000Z",
                    "titleTranslate": {"zh-Hans": ["落语朱音", "朱音落语"]},
                    "sites": [{"site": "bangumi", "id": "576121"}],
                }
            ]
        }
        with patch.object(data, "_parse_data", return_value=[]):
            result = data._find_bangumi_id_optimized(
                "あかね噺", release_date="2026-04-04"
            )
        assert result is not None
        assert result[0] == "576121"
        assert result[2] is True

    # ── 边界测试 ──────────────────────────────────────────

    def test_index_date_diff_exactly_180_returns_directly(self):
        """标题索引日期差恰好=180天时，应直接返回（<=180边界）"""
        data = _make_data()
        data._title_index = {
            "标题": [
                {
                    "title": "标题",
                    "begin": "2024-01-01",
                    "sites": [{"site": "bangumi", "id": "100"}],
                }
            ]
        }
        # 2024-01-01 + 180天 = 2024-06-29
        with patch.object(data, "_parse_data", return_value=[]):
            result = data._find_bangumi_id_optimized("标题", release_date="2024-06-29")
        assert result is not None
        assert result[0] == "100"
        assert result[2] is True

    def test_index_date_diff_exactly_181_falls_through(self):
        """标题索引日期差恰好=181天时，应回退线性扫描（>180边界）"""
        data = _make_data()
        data._title_index = {
            "标题": [
                {
                    "title": "标题",
                    "begin": "2024-01-01",
                    "titleTranslate": {"zh-Hans": ["标题"]},
                    "sites": [{"site": "bangumi", "id": "100"}],
                }
            ]
        }
        exact_item = {
            "title": "标题",
            "begin": "2024-01-01",
            "titleTranslate": {"zh-Hans": ["标题"]},
            "sites": [{"site": "bangumi", "id": "100"}],
        }
        # 2024-01-01 + 181天 = 2024-06-30
        with (
            patch.object(data, "_parse_data", return_value=[exact_item]),
            patch.object(
                data,
                "_calculate_match_info",
                return_value={
                    "exact_match": True,
                    "match_type": "zh-hans",
                    "score": 1.0,
                    "best_zh_score": 1.0,
                    "best_zh_title": "标题",
                },
            ),
        ):
            result = data._find_bangumi_id_optimized("标题", release_date="2024-06-30")
        # 回退到线性扫描后，精确匹配日期差181天>180，无部分匹配，返回精确匹配
        assert result is not None
        assert result[0] == "100"

    def test_partial_match_date_diff_exactly_90_accepts(self):
        """部分匹配日期差恰好=90天时，应采纳（<=90边界）"""
        data = _make_data()
        data._title_index = {
            "测试番剧": [
                {
                    "title": "テスト",
                    "begin": "2020-01-01",
                    "titleTranslate": {"zh-Hans": ["测试番剧"]},
                    "sites": [{"site": "bangumi", "id": "100"}],
                }
            ]
        }
        exact_item = {
            "title": "テスト",
            "begin": "2020-01-01",
            "titleTranslate": {"zh-Hans": ["测试番剧"]},
            "sites": [{"site": "bangumi", "id": "100"}],
        }
        partial_item = {
            "title": "テスト2",
            "begin": "2022-04-11",  # 与搜索日期差90天
            "titleTranslate": {"zh-Hans": ["测试番剧 第二季"]},
            "sites": [{"site": "bangumi", "id": "200"}],
        }

        def fake_match_info(item, title, ori_title=None, release_date=None):
            zh = item.get("titleTranslate", {}).get("zh-Hans", [""])[0]
            if zh == "测试番剧":
                return {
                    "exact_match": True,
                    "match_type": "zh-hans",
                    "score": 1.0,
                    "best_zh_score": 1.0,
                    "best_zh_title": zh,
                }
            return {
                "exact_match": False,
                "match_type": None,
                "score": 0.7,
                "best_zh_score": 0.7,
                "best_zh_title": zh,
            }

        # 搜索日期 2022-07-10, 精确匹配diff=920天>180, 部分匹配diff=90天
        with (
            patch.object(data, "_parse_data", return_value=[exact_item, partial_item]),
            patch.object(data, "_calculate_match_info", side_effect=fake_match_info),
        ):
            result = data._find_bangumi_id_optimized(
                "测试番剧", release_date="2022-07-10"
            )
        assert result is not None
        assert result[0] == "200"
        assert result[2] is True

    def test_partial_match_date_diff_exactly_91_rejects(self):
        """部分匹配日期差恰好=91天时，应拒绝回退精确匹配（>90边界）"""
        data = _make_data()
        data._title_index = {
            "测试番剧": [
                {
                    "title": "テスト",
                    "begin": "2020-01-01",
                    "titleTranslate": {"zh-Hans": ["测试番剧"]},
                    "sites": [{"site": "bangumi", "id": "100"}],
                }
            ]
        }
        exact_item = {
            "title": "テスト",
            "begin": "2020-01-01",
            "titleTranslate": {"zh-Hans": ["测试番剧"]},
            "sites": [{"site": "bangumi", "id": "100"}],
        }
        partial_item = {
            "title": "テスト2",
            "begin": "2022-04-11",  # 与搜索日期差91天
            "titleTranslate": {"zh-Hans": ["测试番剧 第二季"]},
            "sites": [{"site": "bangumi", "id": "200"}],
        }

        def fake_match_info(item, title, ori_title=None, release_date=None):
            zh = item.get("titleTranslate", {}).get("zh-Hans", [""])[0]
            if zh == "测试番剧":
                return {
                    "exact_match": True,
                    "match_type": "zh-hans",
                    "score": 1.0,
                    "best_zh_score": 1.0,
                    "best_zh_title": zh,
                }
            return {
                "exact_match": False,
                "match_type": None,
                "score": 0.7,
                "best_zh_score": 0.7,
                "best_zh_title": zh,
            }

        with (
            patch.object(data, "_parse_data", return_value=[exact_item, partial_item]),
            patch.object(data, "_calculate_match_info", side_effect=fake_match_info),
        ):
            result = data._find_bangumi_id_optimized(
                "测试番剧", release_date="2022-07-11"
            )
        # 91天>90，拒绝部分匹配，回退到精确匹配
        assert result is not None
        assert result[0] == "100"

    def test_safety_check_rejects_when_title_not_contained_and_low_score(self):
        """安全校验：标题不包含且分数<0.8时，应拒绝部分匹配"""
        data = _make_data()
        data._title_index = {
            "测试番剧": [
                {
                    "title": "テスト",
                    "begin": "2020-01-01",
                    "titleTranslate": {"zh-Hans": ["测试番剧"]},
                    "sites": [{"site": "bangumi", "id": "100"}],
                }
            ]
        }
        exact_item = {
            "title": "テスト",
            "begin": "2020-01-01",
            "titleTranslate": {"zh-Hans": ["测试番剧"]},
            "sites": [{"site": "bangumi", "id": "100"}],
        }
        # 标题完全不同，不包含搜索词，且分数低
        partial_item = {
            "title": "别的动画",
            "begin": "2022-04-11",
            "titleTranslate": {"zh-Hans": ["完全不同的作品名"]},
            "sites": [{"site": "bangumi", "id": "300"}],
        }

        def fake_match_info(item, title, ori_title=None, release_date=None):
            zh = item.get("titleTranslate", {}).get("zh-Hans", [""])[0]
            if zh == "测试番剧":
                return {
                    "exact_match": True,
                    "match_type": "zh-hans",
                    "score": 1.0,
                    "best_zh_score": 1.0,
                    "best_zh_title": zh,
                }
            # 低分部分匹配
            return {
                "exact_match": False,
                "match_type": None,
                "score": 0.5,
                "best_zh_score": 0.5,
                "best_zh_title": zh,
            }

        with (
            patch.object(data, "_parse_data", return_value=[exact_item, partial_item]),
            patch.object(data, "_calculate_match_info", side_effect=fake_match_info),
        ):
            result = data._find_bangumi_id_optimized(
                "测试番剧", release_date="2022-07-10"
            )
        # 安全校验失败：标题不包含且分数0.5<0.8，回退精确匹配
        assert result is not None
        assert result[0] == "100"

    def test_safety_check_passes_when_high_score_despite_no_containment(self):
        """安全校验：标题不包含但分数>=0.8时，应采纳部分匹配"""
        data = _make_data()
        data._title_index = {
            "测试番剧": [
                {
                    "title": "テスト",
                    "begin": "2020-01-01",
                    "titleTranslate": {"zh-Hans": ["测试番剧"]},
                    "sites": [{"site": "bangumi", "id": "100"}],
                }
            ]
        }
        exact_item = {
            "title": "テスト",
            "begin": "2020-01-01",
            "titleTranslate": {"zh-Hans": ["测试番剧"]},
            "sites": [{"site": "bangumi", "id": "100"}],
        }
        # 标题不包含搜索词，但分数高
        partial_item = {
            "title": "テスト2",
            "begin": "2022-04-11",
            "titleTranslate": {"zh-Hans": ["测试番外剧集"]},
            "sites": [{"site": "bangumi", "id": "300"}],
        }

        def fake_match_info(item, title, ori_title=None, release_date=None):
            zh = item.get("titleTranslate", {}).get("zh-Hans", [""])[0]
            if zh == "测试番剧":
                return {
                    "exact_match": True,
                    "match_type": "zh-hans",
                    "score": 1.0,
                    "best_zh_score": 1.0,
                    "best_zh_title": zh,
                }
            # 高分部分匹配但标题不包含
            return {
                "exact_match": False,
                "match_type": None,
                "score": 0.85,
                "best_zh_score": 0.85,
                "best_zh_title": zh,
            }

        with (
            patch.object(data, "_parse_data", return_value=[exact_item, partial_item]),
            patch.object(data, "_calculate_match_info", side_effect=fake_match_info),
        ):
            result = data._find_bangumi_id_optimized(
                "测试番剧", release_date="2022-07-10"
            )
        # 安全校验通过：分数0.85>=0.8，采纳部分匹配
        assert result is not None
        assert result[0] == "300"
        assert result[2] is True


class TestRequestWithRetry:
    """测试带重试的请求"""

    @patch("app.utils.bangumi_data.requests.get")
    def test_request_with_retry_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        from app.utils.bangumi_data import _request_with_retry

        result = _request_with_retry("https://example.com")
        assert result.status_code == 200

    @patch("app.utils.bangumi_data.time.sleep")
    @patch("app.utils.bangumi_data.requests.get")
    def test_request_with_retry_server_error(self, mock_get, mock_sleep):
        mock_response1 = MagicMock()
        mock_response1.status_code = 500
        mock_response1.raise_for_status = MagicMock()
        mock_response2 = MagicMock()
        mock_response2.status_code = 200
        mock_response2.raise_for_status = MagicMock()
        mock_get.side_effect = [mock_response1, mock_response2]
        from app.utils.bangumi_data import _request_with_retry

        result = _request_with_retry("https://example.com", max_retries=1)
        assert result.status_code == 200

    @patch("app.utils.bangumi_data.time.sleep")
    @patch("app.utils.bangumi_data.requests.get")
    def test_request_with_retry_connection_error(self, mock_get, mock_sleep):
        import requests as req

        mock_get.side_effect = [
            req.exceptions.ConnectionError("Connection failed"),
            MagicMock(status_code=200, raise_for_status=MagicMock()),
        ]
        from app.utils.bangumi_data import _request_with_retry

        result = _request_with_retry("https://example.com", max_retries=1)
        assert result.status_code == 200

    @patch("app.utils.bangumi_data.requests.get")
    def test_request_with_retry_ssl_disabled(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        from app.utils.bangumi_data import _request_with_retry

        _request_with_retry("https://example.com", ssl_verify=False)
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["verify"] is False

    @patch("app.utils.bangumi_data.time.sleep")
    @patch("app.utils.bangumi_data.requests.get")
    def test_request_with_retry_exhausted(self, mock_get, mock_sleep):
        import requests as req

        mock_get.side_effect = req.exceptions.ConnectionError("fail")
        from app.utils.bangumi_data import _request_with_retry

        with pytest.raises(req.exceptions.ConnectionError):
            _request_with_retry("https://example.com", max_retries=0)
