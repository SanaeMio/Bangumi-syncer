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
