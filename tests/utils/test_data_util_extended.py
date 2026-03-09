"""
更多测试 - 简化版
"""

from app.utils.data_util import extract_plex_data


class TestDataUtil:
    """数据提取工具测试 - 简化版"""

    def test_extract_plex_data(self):
        """测试提取 Plex 数据"""
        plex_data = {
            "event": "media.scrobble",
            "Account": {"title": "user1"},
            "Metadata": {
                "title": "Test Show",
                "type": "episode",
                "grandparentTitle": "Test Series",
                "index": 5,
                "parentIndex": 1,
            },
        }
        result = extract_plex_data(plex_data)
        assert result is not None
