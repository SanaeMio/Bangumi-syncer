"""
Bangumi 数据工具完整测试
"""

from app.utils.bangumi_data import BangumiData


class TestBangumiDataComprehensive:
    """Bangumi 数据工具综合测试"""

    def test_init(self):
        """测试初始化"""
        data = BangumiData()
        assert data is not None

    def test_cache_stats(self):
        """测试缓存统计"""
        data = BangumiData()
        stats = data.get_cache_stats()
        assert isinstance(stats, dict)

    def test_clear_cache(self):
        """测试清空缓存"""
        data = BangumiData()
        data.clear_cache()  # 不应该抛出异常


class TestBangumiDataMatching:
    """标题匹配测试"""

    def test_match_title_fuzzy(self):
        """测试模糊匹配"""
        data = BangumiData()
        item = {"name": "测试", "name_cn": "测试"}

        result = data._match_title_fuzzy(item, "测试")
        assert isinstance(result, bool)

    def test_match_title_fuzzy_ori_title(self):
        """测试模糊匹配带原标题"""
        data = BangumiData()
        item = {"name": "Test", "name_cn": "测试"}

        result = data._match_title_fuzzy(item, "测试", "Test")
        assert isinstance(result, bool)

    def test_check_key_characters(self):
        """测试关键字符检查"""
        data = BangumiData()

        result = data._check_key_characters("测试", "测试")
        assert result is True

        result = data._check_key_characters("测试", "其他")
        assert result is False

    def test_date_diff(self):
        """测试日期差计算"""
        data = BangumiData()

        diff = data._date_diff("2024-01-01", "2024-01-10")
        assert diff == 9

    def test_is_date_close(self):
        """测试日期是否接近"""
        data = BangumiData()

        result = data._is_date_close("2024-01-01", "2024-01-10")
        assert isinstance(result, bool)


class TestBangumiDataTitle:
    """标题处理测试"""

    def test_get_zh_hans_titles(self):
        """测试获取中文标题"""
        data = BangumiData()
        item = {"name": "Test", "name_cn": "测试", "tags": [{"name": "中文"}]}

        titles = data._get_zh_hans_titles(item)
        assert isinstance(titles, list)

    def test_get_best_matched_title(self):
        """测试获取最佳匹配标题"""
        data = BangumiData()
        item = {"name": "Test", "name_cn": "测试"}

        title = data._get_best_matched_title(item)
        assert isinstance(title, str)


class TestBangumiDataExtract:
    """ID提取测试"""

    def test_extract_bangumi_id(self):
        """测试提取Bangumi ID"""
        data = BangumiData()

        # 测试从URL提取
        item = {"url": "https://bgm.tv/subject/123"}
        _result = data._extract_bangumi_id(item)

        # 测试从其他字段提取
        item2 = {"id": 456}
        _result2 = data._extract_bangumi_id(item2)


class TestBangumiDataSearch:
    """搜索测试"""

    def test_search_title(self):
        """测试搜索标题"""
        data = BangumiData()

        # 空搜索
        result = data.search_title("")
        assert isinstance(result, list)

        # 带查询的搜索
        result = data.search_title("test")
        assert isinstance(result, list)

    def test_get_title_by_tmdb_id(self):
        """测试通过TMDB ID获取标题"""
        data = BangumiData()

        result = data.get_title_by_tmdb_id("123")
        # 可能返回 None 如果没有缓存
        assert result is None or isinstance(result, str)


class TestBangumiDataForceUpdate:
    """强制更新测试"""

    def test_force_update(self):
        """测试强制更新"""
        data = BangumiData()

        result = data.force_update()
        # 可能返回 True/False 取决于网络
        assert isinstance(result, bool)


class TestBangumiDataCalculate:
    """匹配计算测试"""

    def test_calculate_match_info(self):
        """测试匹配信息计算"""
        data = BangumiData()

        item = {"name": "Test", "name_cn": "测试"}

        result = data._calculate_match_info(item, "测试")
        assert isinstance(result, dict)

    def test_calculate_match_score(self):
        """测试匹配分数计算"""
        data = BangumiData()

        item = {"name": "Test", "name_cn": "测试"}

        result = data._calculate_match_score(item, "测试", "Test")
        assert isinstance(result, (int, float))
