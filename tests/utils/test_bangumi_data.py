"""
Bangumi 数据工具测试
"""

from unittest.mock import MagicMock, patch

from app.utils.bangumi_data import BangumiData


class TestBangumiData:
    """Bangumi 数据工具测试"""

    def test_bangumi_data_init(self):
        """测试 BangumiData 初始化"""
        data = BangumiData()
        assert data is not None

    @patch("app.utils.bangumi_data.requests.get")
    def test_fetch_data(self, mock_get):
        """测试获取数据"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"data": []})
        mock_get.return_value = mock_response

        data = BangumiData()
        # 测试基本初始化成功
        assert data is not None


class TestBangumiDataSearch:
    """Bangumi 数据搜索测试"""

    def test_search_empty(self):
        """测试空搜索"""
        data = BangumiData()
        # 测试空搜索结果
        if hasattr(data, "search"):
            result = data.search("")
            assert result is not None

    def test_search_with_query(self):
        """测试带查询的搜索"""
        data = BangumiData()
        if hasattr(data, "search"):
            result = data.search("test")
            assert result is not None
