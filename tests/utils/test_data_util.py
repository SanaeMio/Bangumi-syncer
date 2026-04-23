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

    def test_extract_plex_data_movie(self):
        from app.utils.data_util import extract_plex_data

        plex_data = {
            "event": "media.scrobble",
            "Account": {"title": "test_user"},
            "Metadata": {
                "type": "movie",
                "title": "某剧场版",
                "originalTitle": "Gekijouban",
                "originallyAvailableAt": "2024-06-01",
            },
        }
        result = extract_plex_data(plex_data)
        assert result.media_type == "movie"
        assert result.title == "某剧场版"
        assert result.season == 1
        assert result.episode == 1
        assert result.release_date == "2024-06-01"

    def test_extract_plex_data_movie_no_originally_available_at(self):
        """电影无 originallyAvailableAt 时 release_date 为空并走缺省日志分支"""
        from app.utils.data_util import extract_plex_data

        plex_data = {
            "event": "media.play",
            "Account": {"title": "test_user"},
            "Metadata": {
                "type": "movie",
                "title": "无日期剧场版",
            },
        }
        result = extract_plex_data(plex_data)
        assert result.media_type == "movie"
        assert result.title == "无日期剧场版"
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
        assert result.ori_title is None
        assert result.season == 2
        assert result.episode == 10
        assert result.release_date == "2024-01-15"
        assert result.user_name == "test_user"
        assert result.source == "emby"

    def test_extract_emby_data_episode_does_not_use_original_title(self):
        """Emby 剧集 OriginalTitle 为分集名，不作为 ori_title（避免条目匹配跑偏）"""
        from app.utils.data_util import extract_emby_data

        emby_data = {
            "Event": "item.markplayed",
            "User": {"Name": "test_user"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "某动画",
                "SeriesOriginalTitle": "シリーズ原文",
                "OriginalTitle": "第3話 サブタイトル",
                "ParentIndexNumber": 1,
                "IndexNumber": 3,
                "PremiereDate": "2024-02-01T00:00:00.0000000Z",
            },
        }
        result = extract_emby_data(emby_data)
        assert result.title == "某动画"
        assert result.ori_title is None

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
        assert result.ori_title is None

    def test_extract_emby_data_movie(self):
        from app.utils.data_util import extract_emby_data

        emby_data = {
            "Event": "item.markplayed",
            "User": {"Name": "test_user"},
            "Item": {
                "Type": "Movie",
                "Name": "剧场版 Y",
                "PremiereDate": "2024-07-01T00:00:00.0000000Z",
            },
        }
        result = extract_emby_data(emby_data)
        assert result.media_type == "movie"
        assert result.title == "剧场版 Y"
        assert result.ori_title is None
        assert result.season == 1
        assert result.episode == 1
        assert result.release_date == "2024-07-01"

    def test_extract_emby_data_movie_original_title(self):
        """Emby 电影应携带 OriginalTitle 作为 ori_title（剧场版等日文检索）"""
        from app.utils.data_util import extract_emby_data

        emby_data = {
            "Event": "item.markplayed",
            "User": {"Name": "test_user"},
            "Item": {
                "Type": "Movie",
                "Name": "花开伊吕波剧场版：甜蜜的家",
                "OriginalTitle": "劇場版 花咲くいろは HOME SWEET HOME",
                "PremiereDate": "2013-03-08T16:00:00.0000000Z",
            },
        }
        result = extract_emby_data(emby_data)
        assert result.media_type == "movie"
        assert result.title == "花开伊吕波剧场版：甜蜜的家"
        assert result.ori_title == "劇場版 花咲くいろは HOME SWEET HOME"
        assert result.release_date == "2013-03-08"

    def test_extract_emby_data_movie_original_title_blank(self):
        """OriginalTitle 为空或仅空白时 ori_title 为 None"""
        from app.utils.data_util import extract_emby_data

        emby_data = {
            "Event": "item.markplayed",
            "User": {"Name": "test_user"},
            "Item": {
                "Type": "Movie",
                "Name": "剧场版 X",
                "OriginalTitle": "   ",
                "PremiereDate": "2024-01-01T00:00:00.0000000Z",
            },
        }
        result = extract_emby_data(emby_data)
        assert result.ori_title is None

    def test_extract_emby_data_movie_no_premiere_date(self):
        """电影无 PremiereDate 时 release_date 为空"""
        from app.utils.data_util import extract_emby_data

        emby_data = {
            "Event": "playback.start",
            "User": {"Name": "test_user"},
            "Item": {"Type": "Movie", "Name": "剧场版 Z"},
        }
        result = extract_emby_data(emby_data)
        assert result.media_type == "movie"
        assert result.title == "剧场版 Z"
        assert result.release_date == ""
        assert result.ori_title is None


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

    def test_extract_jellyfin_data_movie(self):
        from app.utils.data_util import extract_jellyfin_data

        jellyfin_data = {
            "NotificationType": "PlaybackStop",
            "PlayedToCompletion": "True",
            "media_type": "Movie",
            "title": "剧场版 Z",
            "ori_title": "Z Movie",
            "season": 0,
            "episode": 3,
            "user_name": "test_user",
            "release_date": "2024-08-01",
        }
        result = extract_jellyfin_data(jellyfin_data)
        assert result.media_type == "movie"
        assert result.season == 1
        assert result.episode == 1
        assert result.title == "剧场版 Z"
