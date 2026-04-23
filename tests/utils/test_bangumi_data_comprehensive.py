"""
Bangumi 数据工具完整测试
"""

from unittest.mock import patch

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

    @patch("app.utils.bangumi_data.BangumiData._preload_data_to_memory")
    @patch("app.utils.bangumi_data.BangumiData._parse_data")
    def test_find_bangumi_id_optimized_date_override(
        self, mock_parse_data, mock_preload
    ):
        """测试日期择优机制：当完全匹配的日期差距过大时，采用日期接近的部分匹配"""
        data = BangumiData()

        # 模拟内存中的 bangumi-data 数据流
        mock_parse_data.return_value = [
            {
                "title": "玉响",
                "titleTranslate": {"zh-Hans": ["玉响"]},
                "begin": "2010-11-26",
                "sites": [{"site": "bangumi", "id": "8452"}],
            },
            {
                "title": "たまゆら～hitotose～",
                "titleTranslate": {"zh-Hans": ["玉响～hitotose～", "玉响～1年～"]},
                "begin": "2011-10-03",
                "sites": [{"site": "bangumi", "id": "18605"}],
            },
        ]

        # 模拟 Emby 传入的请求：标题完全等于 OVA，但给定的播出时间是正片第一季的时间
        result = data._find_bangumi_id_optimized(
            title="玉响", ori_title="", release_date="2011-10-02", season=1
        )

        # 断言：必须舍弃 ID 8452，返回正片第一季的 ID 18605
        assert result is not None
        assert result[0] == "18605"
        # 断言：返回的应当是最佳中文匹配名
        assert result[1] == "玉响～hitotose～"
        # 断言：由于是严格依靠日期反杀的，可信度必须被标记为 True
        assert result[2] is True

    @patch("app.utils.bangumi_data.BangumiData._preload_data_to_memory")
    @patch("app.utils.bangumi_data.BangumiData._parse_data")
    def test_find_bangumi_id_date_override_rejected_by_safety_lock(
        self, mock_parse_data, mock_preload
    ):
        """测试日期择优机制的安全校验：当日期接近但名称完全不包含时，拒绝采用该匹配条目"""
        data = BangumiData()

        # 模拟内存数据：小圆和伊莉雅的哈希碰撞
        mock_parse_data.return_value = [
            {
                "title": "魔法少女まどか☆マギカ",
                "titleTranslate": {"zh-Hans": ["魔法少女小圆"]},
                "begin": "2011-01-07",
                "sites": [{"site": "bangumi", "id": "10001"}],
            },
            {
                "title": "Fate/kaleid liner プリズマ☆イリヤ",
                "titleTranslate": {"zh-Hans": ["魔法少女伊莉雅"]},
                "begin": "2013-07-13",
                "sites": [{"site": "bangumi", "id": "90009"}],
            },
        ]

        # 模拟请求：搜索“魔法少女小圆”，但传入的是 2013 年（伊莉雅播出的时间）
        # 此时伊莉雅因为带有“魔法少女”四个字，会有基础模糊得分，且日期极度接近
        result = data._find_bangumi_id_optimized(
            title="魔法少女小圆", ori_title="", release_date="2013-07-12", season=1
        )

        # 断言：安全校验必须生效拒绝采用，退回到完全匹配（小圆的 ID 10001）
        assert result is not None
        assert result[0] == "10001"
        assert result[1] == "魔法少女小圆"
        # 断言：由于防误判锁生效拒绝了日期择优，可信度必须退回到 False，交给后端去爬树
        assert result[2] is False

    @patch("app.utils.bangumi_data.BangumiData._preload_data_to_memory")
    @patch("app.utils.bangumi_data.BangumiData._parse_data")
    def test_find_bangumi_id_ori_title_without_zh_hans(
        self, mock_parse_data, mock_preload
    ):
        """条目无简中翻译时，仍可用 ori_title 与日文 title 精确匹配（剧场版等）"""
        data = BangumiData()
        mock_parse_data.return_value = [
            {
                "title": "劇場版サンプル映画",
                "begin": "2020-03-15",
                "sites": [{"site": "bangumi", "id": "123456"}],
            },
        ]
        result = data.find_bangumi_id(
            title="某中文片名",
            ori_title="劇場版サンプル映画",
            release_date="2020-03-15",
            season=1,
        )
        assert result is not None
        assert result[0] == "123456"
        assert result[1] == "劇場版サンプル映画"


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
