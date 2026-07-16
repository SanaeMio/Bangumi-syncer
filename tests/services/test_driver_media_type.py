"""各驱动 OVA/OAD/三次元数据支持测试

基于调研数据验证各驱动正确检测和传递新媒体类型。
"""

from app.services.emby.extractor import extract_emby_data
from app.services.fongmi.client import media_to_record
from app.services.fongmi.models import FongmiDevice
from app.services.plex.extractor import extract_plex_data


class TestPlexMediaTypeDetection:
    """Plex 驱动媒体类型检测"""

    def _make_device(self):
        return FongmiDevice(
            ip="192.168.1.1",
            port=9978,
            uuid="test-uuid",
            name="test-device",
            device_type=1,
        )

    def test_plex_episode_normal(self):
        """Plex 普通剧集 → episode"""
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "鬼滅の刃",
                "originalTitle": "Demon Slayer",
                "parentIndex": 1,
                "index": 5,
                "originallyAvailableAt": "2024-01-01",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.media_type == "episode"
        assert item.title == "鬼滅の刃"

    def test_plex_movie_with_movie_keyword(self):
        """Plex 电影 → movie"""
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "movie",
                "title": "鬼滅の刃 無限列車編",
                "originalTitle": "Demon Slayer Movie",
                "originallyAvailableAt": "2024-01-01",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.media_type == "movie"

    def test_plex_movie_real_action(self):
        """Plex 真人电影 → real_action"""
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "movie",
                "title": "ある日劇 真人版",
                "originalTitle": "Some Live Action",
                "originallyAvailableAt": "2024-01-01",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.media_type == "real_action"

    def test_plex_episode_with_ova_in_title(self):
        """Plex 标题含 OVA → ova"""
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "某番剧 OVA",
                "originalTitle": "Anime OVA",
                "parentIndex": 1,
                "index": 1,
                "originallyAvailableAt": "2024-01-01",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.media_type == "ova"

    def test_plex_ori_title_extracted_correctly(self):
        """Plex 原始标题正确提取"""
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "某番剧",
                "originalTitle": "Original Title",
                "parentIndex": 1,
                "index": 1,
                "originallyAvailableAt": "2024-01-01",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.ori_title == "Original Title"


class TestEmbyMediaTypeDetection:
    """Emby 驱动媒体类型检测"""

    def test_emby_episode_normal(self):
        """Emby 普通剧集 → episode"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "進撃の巨人",
                "OriginalTitle": "第3話 サブタイトル",
                "ParentIndexNumber": 1,
                "IndexNumber": 3,
                "PremiereDate": "2024-02-01T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.media_type == "episode"
        assert item.title == "進撃の巨人"

    def test_emby_movie_normal(self):
        """Emby 电影 → movie"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Movie",
                "Name": "ある劇場版",
                "OriginalTitle": "Movie Original",
                "PremiereDate": "2024-01-01T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.media_type == "movie"

    def test_emby_episode_ori_title_not_hardcoded_space(self):
        """Emby episode 分支 ori_title 不再硬编码空格"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "某番剧",
                "OriginalTitle": "原始标题",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "PremiereDate": "2024-01-01T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.ori_title == "原始标题"

    def test_emby_episode_no_original_title_falls_back_to_space(self):
        """Emby episode 无 OriginalTitle 时回退为空格（保持兼容）"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "某番剧",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "PremiereDate": "2024-01-01T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.ori_title == " "

    def test_emby_real_action_movie(self):
        """Emby 真人电影 → real_action"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Movie",
                "Name": "ある日劇 真人版",
                "OriginalTitle": "Live Action Movie",
                "PremiereDate": "2024-01-01T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.media_type == "real_action"

    def test_emby_episode_with_ova_in_series_name(self):
        """Emby 标题含 OVA → ova"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "某番剧 OVA",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "PremiereDate": "2024-01-01T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.media_type == "ova"


class TestFongmiMediaTypeDetection:
    """Fongmi 驱动媒体类型检测"""

    def _make_device(self):
        return FongmiDevice(
            ip="192.168.1.1",
            port=9978,
            uuid="test-uuid",
            name="test-device",
            device_type=1,
        )

    def test_fongmi_episode_normal(self):
        """Fongmi 普通剧集 → episode"""
        device = self._make_device()
        media = {
            "title": "某番剧",
            "url": "/storage/anime/episode01.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "episode"
        assert rec.is_movie is False

    def test_fongmi_movie_from_url_keyword(self):
        """Fongmi URL 含剧场版 → movie"""
        device = self._make_device()
        media = {
            "title": "某番剧",
            "url": "/storage/剧场版/movie.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "movie"
        assert rec.is_movie is True

    def test_fongmi_ova_from_url(self):
        """Fongmi URL 含 OVA → ova"""
        device = self._make_device()
        media = {
            "title": "某番剧",
            "url": "/storage/OVA/special.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "ova"

    def test_fongmi_oad_from_url(self):
        """Fongmi URL 含 OAD → oad"""
        device = self._make_device()
        media = {
            "title": "某番剧",
            "url": "/storage/OAD/bonus.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "oad"

    def test_fongmi_real_action_from_title(self):
        """Fongmi 标题含日剧 → real_action"""
        device = self._make_device()
        media = {
            "title": "某日剧 第一季",
            "url": "/storage/drama/ep01.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "real_action"

    def test_fongmi_ova_sets_season_episode_to_1(self):
        """Fongmi OVA 时 season=1, episode=1"""
        device = self._make_device()
        media = {
            "title": "某番剧 OVA",
            "url": "/storage/ova/special.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.season == 1
        assert rec.episode == 1

    def test_fongmi_record_to_custom_item_uses_media_type(self):
        """Fongmi _record_to_custom_item 使用 media_type 字段"""
        from app.services.fongmi.sync_service import FongmiSyncService

        svc = FongmiSyncService()
        device = self._make_device()
        media = {
            "title": "某番剧 OVA",
            "url": "/storage/OVA/special.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        item = svc._record_to_custom_item(rec)
        assert item.media_type == "ova"

    def test_fongmi_record_to_custom_item_fallback_is_movie(self):
        """Fongmi media_type 为空时回退到 is_movie 二分"""
        from app.services.fongmi.models import FongmiWatchRecord
        from app.services.fongmi.sync_service import FongmiSyncService

        svc = FongmiSyncService()
        rec = FongmiWatchRecord(
            device_ip="1.1.1.1",
            device_name="dev",
            title="某番剧",
            episode=1,
            season=1,
            episode_url="/video.mp4",
            is_movie=True,
            media_type="",  # 为空，回退到 is_movie
        )
        item = svc._record_to_custom_item(rec)
        assert item.media_type == "movie"


class TestFeiniuMediaTypeDetection:
    """飞牛驱动媒体类型检测"""

    def _make_record(self, **kwargs):
        from app.services.feiniu.models import FeiniuWatchRecord

        base = dict(
            item_guid="it1",
            user_guid="u1",
            username="viewer",
            display_title="测试番剧",
            original_title=None,
            season=1,
            episode=1,
            release_date="2024-01-01",
            update_time_ms=1_700_000_000_000,
        )
        base.update(kwargs)
        return FeiniuWatchRecord(**base)

    def test_feiniu_movie_by_item_type(self):
        """飞牛 item_type=Movie → movie"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(
            item_type="Movie", episode_from_db=False, season_from_db=False
        )
        assert _feiniu_detect_media_type(rec) == "movie"

    def test_feiniu_episode_by_item_type(self):
        """飞牛 item_type=Episode → episode"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(
            item_type="Episode", episode_from_db=True, season_from_db=False
        )
        assert _feiniu_detect_media_type(rec) == "episode"

    def test_feiniu_ova_from_title(self):
        """飞牛标题含 OVA → ova"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(display_title="某番剧 OVA", item_type="Series")
        assert _feiniu_detect_media_type(rec) == "ova"

    def test_feiniu_oad_from_title(self):
        """飞牛标题含 OAD → oad"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(display_title="某番剧 OAD", item_type="Series")
        assert _feiniu_detect_media_type(rec) == "oad"

    def test_feiniu_real_action_from_title(self):
        """飞牛标题含日剧 → real_action"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(display_title="某日剧 第一季", item_type="Series")
        assert _feiniu_detect_media_type(rec) == "real_action"

    def test_feiniu_real_action_from_item_type(self):
        """飞牛 item_type=drama → real_action"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(
            item_type="drama", episode_from_db=True, season_from_db=True
        )
        assert _feiniu_detect_media_type(rec) == "real_action"

    def test_feiniu_no_type_no_season_ep_is_movie(self):
        """飞牛无 item_type 且无季集 → movie（启发式回退）"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(
            item_type=None, episode_from_db=False, season_from_db=False
        )
        assert _feiniu_detect_media_type(rec) == "movie"

    def test_feiniu_record_to_custom_item_ova(self):
        """飞牛 _record_to_custom_item OVA 场景"""
        from app.services.feiniu.sync_service import feiniu_sync_service

        rec = self._make_record(
            display_title="某番剧 OVA",
            item_type="Series",
            episode_from_db=True,
            season_from_db=True,
        )
        item = feiniu_sync_service._record_to_custom_item(rec)
        assert item is not None
        assert item.media_type == "ova"
        assert item.season == 1
        assert item.episode == 1
