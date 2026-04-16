"""
数据提取工具测试
"""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


class TestExtractPlexJson:
    """测试 Plex JSON 提取"""

    def test_extract_plex_json_with_prefix(self):
        """测试提取带前缀的 Plex JSON"""
        from app.utils.data_util import extract_plex_json

        # Plex webhook 格式：\r\n{...}\r\n
        raw_data = b'\r\n{"event": "media.scrobble", "Account": {"title": "user"}}\r\n'
        result = extract_plex_json(raw_data)

        assert result is not None
        assert '"event"' in result
        assert '"media.scrobble"' in result

    def test_extract_plex_json_string_input(self):
        """测试字符串输入"""
        from app.utils.data_util import extract_plex_json

        raw_data = '\r\n{"event": "test"}\r\n'
        result = extract_plex_json(raw_data)

        assert result is not None
        assert '"event"' in result

    def test_extract_plex_json_invalid(self):
        """测试无效输入"""
        from app.utils.data_util import extract_plex_json

        # 无效数据
        result = extract_plex_json(b"invalid data")
        assert result is None

    def test_extract_plex_json_no_json(self):
        """测试不包含 JSON"""
        from app.utils.data_util import extract_plex_json

        result = extract_plex_json(b"no json here")
        assert result is None

    def test_extract_plex_json_no_closing_delimiter(self):
        """有起始 \\r\\n{ 但缺少结尾 }\\r\\n 时返回 None"""
        from app.utils.data_util import extract_plex_json

        assert extract_plex_json(b'\r\n{"only": "open"}\n') is None


class TestExtractPlexData:
    """测试 Plex 数据提取"""

    def test_extract_plex_data_basic(self):
        """测试基本 Plex 数据提取"""
        from app.utils.data_util import extract_plex_data

        plex_data = {
            "event": "media.scrobble",
            "Account": {"title": "test_user"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "测试番剧",
                "originalTitle": "Test Anime",
                "parentIndex": 1,
                "index": 5,
                "originallyAvailableAt": "2024-01-15",
            },
        }

        result = extract_plex_data(plex_data)

        assert result.media_type == "episode"
        assert result.title == "测试番剧"
        assert result.ori_title == "Test Anime"
        assert result.season == 1
        assert result.episode == 5
        assert result.release_date == "2024-01-15"
        assert result.user_name == "test_user"
        assert result.source == "plex"

    def test_extract_plex_data_no_release_date(self):
        """测试无发行日期"""
        from app.utils.data_util import extract_plex_data

        plex_data = {
            "event": "media.scrobble",
            "Account": {"title": "test_user"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "测试番剧",
                "originalTitle": "Test Anime",
                "parentIndex": 1,
                "index": 1,
                # 无 originallyAvailableAt
            },
        }

        result = extract_plex_data(plex_data)

        assert result.release_date == ""


class TestExtractEmbyData:
    """测试 Emby 数据提取"""

    def test_extract_emby_data_basic(self):
        """测试基本 Emby 数据提取"""
        from app.utils.data_util import extract_emby_data

        emby_data = {
            "Event": "item.markplayed",
            "User": {"Name": "test_user"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "测试番剧",
                "ParentIndexNumber": 2,
                "IndexNumber": 10,
                "PremiereDate": "2024-01-15T00:00:00.0000000Z",
            },
        }

        result = extract_emby_data(emby_data)

        assert result.media_type == "episode"
        assert result.title == "测试番剧"
        assert result.season == 2
        assert result.episode == 10
        assert result.release_date == "2024-01-15"
        assert result.user_name == "test_user"
        assert result.source == "emby"

    def test_extract_emby_data_no_premiere_date(self):
        """测试无发行日期"""
        from app.utils.data_util import extract_emby_data

        emby_data = {
            "Event": "item.markplayed",
            "User": {"Name": "test_user"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "测试番剧",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                # 无 PremiereDate
            },
        }

        result = extract_emby_data(emby_data)

        assert result.release_date == ""


class TestExtractJellyfinData:
    """测试 Jellyfin 数据提取"""

    def test_extract_jellyfin_data_basic(self):
        """测试基本 Jellyfin 数据提取"""
        from app.utils.data_util import extract_jellyfin_data

        jellyfin_data = {
            "NotificationType": "PlaybackStop",
            "PlayedToCompletion": "True",
            "media_type": "Episode",
            "title": "测试番剧",
            "ori_title": "Test Anime",
            "season": 3,
            "episode": 12,
            "user_name": "test_user",
            "release_date": "2024-03-01",
        }

        result = extract_jellyfin_data(jellyfin_data)

        assert result.media_type == "episode"
        assert result.title == "测试番剧"
        assert result.ori_title == "Test Anime"
        assert result.season == 3
        assert result.episode == 12
        assert result.release_date == "2024-03-01"
        assert result.user_name == "test_user"
        assert result.source == "jellyfin"

    def test_extract_jellyfin_data_no_release_date(self):
        """测试无发行日期"""
        from app.utils.data_util import extract_jellyfin_data

        jellyfin_data = {
            "NotificationType": "PlaybackStop",
            "PlayedToCompletion": "True",
            "media_type": "Episode",
            "title": "测试番剧",
            "ori_title": "Test Anime",
            "season": 1,
            "episode": 1,
            "user_name": "test_user",
            # 无 release_date
        }

        result = extract_jellyfin_data(jellyfin_data)

        assert result.release_date == ""
