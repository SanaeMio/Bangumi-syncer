"""各驱动 OVA/OAD/三次元媒体类型检测测试

合并自 test_driver_media_type.py 与 test_jellyfin_trakt_media_type.py。

测试数据来源（均为真实条目，非占位符）：
- Bangumi API 真实条目（https://api.bgm.tv/v0/search/subjects，SubjectType=2/6）：
  * 鬼滅の刃        id=245665  platform=TV    date=2019-04-06  type=2（动画）
  * 呪術廻戦        id=294993  platform=TV    date=2020-10-02  type=2
  * 進撃の巨人 OAD   id=80993   platform=OVA   date=2013-12-09  type=2
  * 劇場版 鬼滅の刃 無限列車編  id=291494  platform=剧场版  date=2020-10-16  type=2
  * 逃げるは恥だが役に立つ      id=188108  platform=日剧  date=2016-10-11  type=6（三次元）
  * 半沢直樹        id=73955   platform=日剧  date=2013-07-07  type=6
  * コード・ブルー   id=1346    platform=日剧  date=2008-07-03  type=6
- Bangumi API 文档（https://bangumi.github.io/api/）：
  * SubjectType 枚举：1=书籍 2=动画 3=音乐 4=游戏 6=三次元
  * Subject schema 字段：name / name_cn / platform / date / type / eps
- GitHub Issues（https://github.com/SanaeMio/Bangumi-syncer/issues）：
  * #182 无职转生第三季匹配错误（Plex webhook 真实字段：
    title='无职转生：到了异世界就拿出真本事' ori_title=' ' season=3 source='plex'）
"""

from unittest.mock import patch

from app.services.emby.extractor import extract_emby_data
from app.services.fongmi.client import media_to_record
from app.services.fongmi.models import FongmiDevice
from app.services.jellyfin.extractor import extract_jellyfin_data
from app.services.plex.extractor import extract_plex_data
from app.services.trakt.models import TraktHistoryItem
from app.services.trakt.sync_service import TraktSyncService

# ===== Plex =====


class TestPlexMediaTypeDetection:
    """Plex 驱动媒体类型检测

    Plex webhook 字段参考 issue#182 真实载荷：
      Account.title / Metadata.type / Metadata.grandparentTitle /
      Metadata.originalTitle / Metadata.parentIndex / Metadata.index /
      Metadata.originallyAvailableAt
    """

    def test_plex_episode_normal(self):
        """鬼滅の刃（TV 动画，id=245665）→ episode"""
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "鬼滅の刃",
                "originalTitle": "Demon Slayer: Kimetsu no Yaiba",
                "parentIndex": 1,
                "index": 5,
                "originallyAvailableAt": "2019-04-06",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.media_type == "episode"
        assert item.title == "鬼滅の刃"

    def test_plex_movie_theatrical(self):
        """劇場版 鬼滅の刃 無限列車編（id=291494, platform=剧场版）→ movie"""
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "movie",
                "title": "劇場版 鬼滅の刃 無限列車編",
                "originalTitle": "Demon Slayer the Movie: Mugen Train",
                "originallyAvailableAt": "2020-10-16",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.media_type == "movie"

    def test_plex_real_action_requires_source_declaration(self):
        """Plex 只声明 movie/episode，**不再**用标题关键词推断三次元

        历史行为：Plex 标 type=movie、originalTitle 含 "(Japanese Drama)"
        会被 detect_media_type 改判为 real_action。
        现在改为直接采信 Plex 的 movie 声明 —— 三次元改由配置
        ``sync.enable_real_action`` 决定是否把 type=6 纳入搜索范围
        （避免「真人快打」这类动画被误判后收窄搜索范围导致漏标）。
        """
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "movie",
                "title": "逃げるは恥だが役に立つ",
                "originalTitle": "The Full-Time Wife Escapist (Japanese Drama)",
                "originallyAvailableAt": "2016-10-11",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.media_type == "movie"

    def test_plex_oad_keyword_no_longer_refined(self):
        """Plex 声明 episode → 就是 episode，不再被标题的 OAD 改判

        源的声明是权威信息。OVA/OAD 的细分改由**候选侧**的 platform 字段
        判定（``_detect_candidate_media_type``），请求侧不再猜测。
        """
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "進撃の巨人 OAD",
                "originalTitle": "Attack on Titan OAD",
                "parentIndex": 1,
                "index": 1,
                "originallyAvailableAt": "2013-12-09",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.media_type == "episode"

    def test_plex_ori_title_extracted_correctly(self):
        """Plex 原始标题（originalTitle）正确提取（issue#182 真实字段）"""
        plex_data = {
            "Account": {"title": "user1"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "无职转生：到了异世界就拿出真本事",
                "originalTitle": "Mushoku Tensei",
                "parentIndex": 3,
                "index": 1,
                "originallyAvailableAt": "2026-07-04",
            },
        }
        item = extract_plex_data(plex_data)
        assert item.ori_title == "Mushoku Tensei"
        assert item.title == "无职转生：到了异世界就拿出真本事"
        assert item.season == 3


# ===== Emby =====


class TestEmbyMediaTypeDetection:
    """Emby 驱动媒体类型检测

    Emby webhook 字段：User.Name / Item.Type / Item.SeriesName /
    Item.OriginalTitle / Item.ParentIndexNumber / Item.IndexNumber / Item.PremiereDate
    """

    def test_emby_episode_normal(self):
        """呪術廻戦（id=294993, platform=TV）→ episode"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "呪術廻戦",
                "OriginalTitle": "Jujutsu Kaisen",
                "ParentIndexNumber": 1,
                "IndexNumber": 3,
                "PremiereDate": "2020-10-02T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.media_type == "episode"
        assert item.title == "呪術廻戦"

    def test_emby_movie_theatrical(self):
        """劇場版 鬼滅の刃 無限列車編（id=291494, platform=剧场版）→ movie"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Movie",
                "Name": "劇場版 鬼滅の刃 無限列車編",
                "OriginalTitle": "Demon Slayer the Movie: Mugen Train",
                "PremiereDate": "2020-10-16T00:00:00.0000000Z",
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
                "SeriesName": "鬼滅の刃",
                "OriginalTitle": "Demon Slayer: Kimetsu no Yaiba",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "PremiereDate": "2019-04-06T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.ori_title == "Demon Slayer: Kimetsu no Yaiba"

    def test_emby_episode_no_original_title_is_none(self):
        """Emby episode 无 OriginalTitle 时 ori_title 为 None，与 CustomItem 默认值一致"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "鬼滅の刃",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "PremiereDate": "2019-04-06T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.ori_title is None

    def test_emby_real_action_requires_source_declaration(self):
        """Emby 只声明 Movie/Episode → 直接采信，不再用标题关键词推断三次元"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Movie",
                "Name": "半沢直樹",
                "OriginalTitle": "Hanzawa Naoki (Japanese Drama)",
                "PremiereDate": "2013-07-07T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.media_type == "movie"

    def test_emby_oad_keyword_no_longer_refined(self):
        """Emby 声明 Episode → 就是 episode，不再被标题的 OAD 改判"""
        emby_data = {
            "User": {"Name": "user1"},
            "Item": {
                "Type": "Episode",
                "SeriesName": "進撃の巨人 OAD",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "PremiereDate": "2013-12-09T00:00:00.0000000Z",
            },
        }
        item = extract_emby_data(emby_data)
        assert item.media_type == "episode"


# ===== Fongmi =====


class TestFongmiMediaTypeDetection:
    """Fongmi 驱动媒体类型检测

    Fongmi /media 端点返回 title / url / artist 字段。
    """

    def _make_device(self):
        return FongmiDevice(
            ip="192.168.1.1",
            port=9978,
            uuid="test-uuid",
            name="test-device",
            device_type=1,
        )

    def test_fongmi_episode_normal(self):
        """鬼滅の刃（id=245665, platform=TV）→ episode"""
        device = self._make_device()
        media = {
            "title": "鬼滅の刃",
            "url": "/storage/anime/kimetsu/ep01.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "episode"
        assert rec.is_movie is False

    def test_fongmi_movie_from_url_keyword(self):
        """URL 含「剧场版」→ movie（劇場版 鬼滅の刃 無限列車編场景）"""
        device = self._make_device()
        media = {
            "title": "鬼滅の刃 無限列車編",
            "url": "/storage/剧场版/mugen_train.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "movie"
        assert rec.is_movie is True

    def test_fongmi_ova_url_treated_as_episode(self):
        """fongmi 不再细分 OVA —— 无可靠信号，细分交由候选侧 platform 判定

        fongmi 只有文件名。历史实现把 URL 里的 ``OVA`` 当类型标记，
        但 OVA 与 episode 在下游**无控制流差异**，且 ``特别篇`` 之类
        关键词误判率高。现在只区分 movie/episode。
        """
        device = self._make_device()
        media = {
            "title": "鬼滅の刃",
            "url": "/storage/OVA/special.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "episode"
        assert rec.is_movie is False

    def test_fongmi_oad_url_treated_as_episode(self):
        """fongmi 不再细分 OAD（理由同上）"""
        device = self._make_device()
        media = {
            "title": "進撃の巨人",
            "url": "/storage/OAD/bonus.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "episode"

    def test_fongmi_real_action_not_inferred(self):
        """fongmi 不再从标题推断三次元（实测「真人快打」等动画误判）

        三次元改由 ``sync.enable_real_action`` 配置控制搜索范围。
        """
        device = self._make_device()
        media = {
            "title": "逃げるは恥だが役に立つ 日剧",
            "url": "/storage/drama/ep01.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        # 有 EP01 季集结构 → episode（不再被「日剧」改判 real_action）
        assert rec.media_type == "episode"

    def test_fongmi_movie_by_year_when_no_episode_structure(self):
        """无季集结构 + 带年份 → movie（单片命名习惯）"""
        device = self._make_device()
        media = {
            "title": "千与千寻",
            "url": "/storage/movies/Spirited.Away.2001.1080p.mkv",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "movie"
        assert rec.is_movie is True
        assert (rec.season, rec.episode) == (1, 1)

    def test_fongmi_episode_structure_wins_over_year(self):
        """有季集结构时优先判 episode（即便文件名带年份）"""
        device = self._make_device()
        media = {
            "title": "某番剧",
            "url": "/storage/anime/Show.2021.S02E05.1080p.mkv",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.media_type == "episode"
        assert rec.season == 2
        assert rec.episode == 5

    def test_fongmi_ova_sets_season_episode_to_1(self):
        """OVA 时 season=1, episode=1"""
        device = self._make_device()
        media = {
            "title": "鬼滅の刃 OVA",
            "url": "/storage/ova/special.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        assert rec.season == 1
        assert rec.episode == 1

    def test_fongmi_record_to_custom_item_uses_media_type(self):
        """Fongmi _record_to_custom_item 使用 media_type 字段

        fongmi 现只产 movie/episode（不细分 OAD），故此处断言 episode。
        """
        from app.services.fongmi.sync_service import FongmiSyncService

        svc = FongmiSyncService()
        device = self._make_device()
        media = {
            "title": "進撃の巨人 OAD",
            "url": "/storage/OAD/bonus.mp4",
            "artist": None,
        }
        rec = media_to_record(device, media)
        item = svc._record_to_custom_item(rec)
        assert item.media_type == "episode"

    def test_fongmi_record_to_custom_item_fallback_is_movie(self):
        """Fongmi media_type 为空时回退到 is_movie 二分"""
        from app.services.fongmi.models import FongmiWatchRecord
        from app.services.fongmi.sync_service import FongmiSyncService

        svc = FongmiSyncService()
        rec = FongmiWatchRecord(
            device_ip="1.1.1.1",
            device_name="dev",
            title="劇場版 鬼滅の刃 無限列車編",
            episode=1,
            season=1,
            episode_url="/video.mp4",
            is_movie=True,
            media_type="",  # 为空，回退到 is_movie
        )
        item = svc._record_to_custom_item(rec)
        assert item.media_type == "movie"


# ===== 飞牛 =====


class TestFeiniuMediaTypeDetection:
    """飞牛驱动媒体类型检测

    飞牛 webhook 字段：item_type / display_title / original_title /
    season / episode / release_date / episode_from_db / season_from_db
    """

    def _make_record(self, **kwargs):
        from app.services.feiniu.models import FeiniuWatchRecord

        base = dict(
            item_guid="it1",
            user_guid="u1",
            username="viewer",
            display_title="鬼滅の刃",
            original_title=None,
            season=1,
            episode=1,
            release_date="2019-04-06",
            update_time_ms=1_700_000_000_000,
        )
        base.update(kwargs)
        return FeiniuWatchRecord(**base)

    def test_feiniu_movie_by_item_type(self):
        """飞牛 item_type=Movie → movie"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(
            display_title="劇場版 鬼滅の刃 無限列車編",
            item_type="Movie",
            episode_from_db=False,
            season_from_db=False,
        )
        assert _feiniu_detect_media_type(rec) == "movie"

    def test_feiniu_episode_by_item_type(self):
        """飞牛 item_type=Episode → episode（呪術廻戦场景）"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(
            display_title="呪術廻戦",
            item_type="Episode",
            episode_from_db=True,
            season_from_db=False,
        )
        assert _feiniu_detect_media_type(rec) == "episode"

    def test_feiniu_item_type_is_authoritative(self):
        """飞牛自带 item_type → **直接采信**，不再被标题关键词细化

        飞牛是本项目里少数**提供类型字段**的源（其余是 Plex/Emby/Jellyfin）。
        历史实现会先用标题关键词判 OVA/三次元，导致源声明被覆盖；
        现在改为采信 item_type，OVA/OAD/三次元的细分交由**候选侧**的
        platform/type 字段判定。
        """
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        # 标题含 OVA 但 item_type=Series → episode（源权威）
        rec = self._make_record(display_title="鬼滅の刃 OVA", item_type="Series")
        assert _feiniu_detect_media_type(rec) == "episode"

        # 标题含 OAD 但 item_type=Series → episode
        rec = self._make_record(display_title="進撃の巨人 OAD", item_type="Series")
        assert _feiniu_detect_media_type(rec) == "episode"

    def test_feiniu_real_action_requires_item_type(self):
        """飞牛三次元：仅当 item_type 显式声明（drama/日剧/真人）才判 real_action"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        # 标题含「日剧」但 item_type=Series → 不再判 real_action
        rec = self._make_record(
            display_title="逃げるは恥だが役に立つ 日剧", item_type="Series"
        )
        assert _feiniu_detect_media_type(rec) == "episode"

        # item_type=drama → 保留 real_action（源权威）
        rec = self._make_record(
            display_title="半沢直樹",
            item_type="drama",
            episode_from_db=True,
            season_from_db=True,
        )
        assert _feiniu_detect_media_type(rec) == "real_action"

    def test_feiniu_no_type_no_season_ep_is_movie(self):
        """飞牛无 item_type 且无季集 → movie（启发式回退）"""
        from app.services.feiniu.sync_service import _feiniu_detect_media_type

        rec = self._make_record(
            display_title="コード・ブルー",
            item_type=None,
            episode_from_db=False,
            season_from_db=False,
        )
        assert _feiniu_detect_media_type(rec) == "movie"

    def test_feiniu_record_to_custom_item_uses_item_type(self):
        """飞牛 _record_to_custom_item 采信 item_type（OVA 细分交候选侧）"""
        from app.services.feiniu.sync_service import feiniu_sync_service

        rec = self._make_record(
            display_title="鬼滅の刃 OVA",
            item_type="Series",
            episode_from_db=True,
            season_from_db=True,
        )
        item = feiniu_sync_service._record_to_custom_item(rec)
        assert item is not None
        assert item.media_type == "episode"


# ===== Jellyfin =====


def _jellyfin_episode_data(title="鬼滅の刃", ori_title="", media_type="episode"):
    return {
        "title": title,
        "ori_title": ori_title,
        "media_type": media_type,
        "season": 1,
        "episode": 5,
        "release_date": "2019-04-06",
        "user_name": "tester",
    }


def _jellyfin_movie_data(title="劇場版 鬼滅の刃 無限列車編", ori_title=""):
    return {
        "title": title,
        "ori_title": ori_title,
        "media_type": "movie",
        "season": 1,
        "episode": 1,
        "release_date": "2020-10-16",
        "user_name": "tester",
    }


class TestJellyfinExtractorMediaTypeDetection:
    """Jellyfin extractor 采信源声明的类型（不再用标题关键词细化）"""

    def test_episode_declaration_is_authoritative(self):
        """media_type=episode → episode，标题关键词不再改判

        Jellyfin 明确声明类型，这是权威信息。OVA/OAD/三次元的细分改由
        **候选侧**的 platform/type 字段判定。
        """
        for title in ("鬼滅の刃 OVA", "進撃の巨人 OAD", "逃げるは恥だが役に立つ 日剧"):
            data = _jellyfin_episode_data(title=title, media_type="episode")
            item = extract_jellyfin_data(data)
            assert item.media_type == "episode", title

    def test_episode_normal_title_keeps_episode(self):
        """普通剧集标题保持 episode（呪術廻戦场景）"""
        data = _jellyfin_episode_data(title="呪術廻戦", media_type="episode")
        item = extract_jellyfin_data(data)
        assert item.media_type == "episode"

    def test_movie_declaration_is_authoritative(self):
        """media_type=movie → movie，不被标题的「真人版」改判 real_action"""
        data = _jellyfin_movie_data(title="真人版 鬼滅の刃")
        item = extract_jellyfin_data(data)
        assert item.media_type == "movie"

    def test_movie_normal_title_keeps_movie(self):
        """普通电影标题保持 movie（劇場版 鬼滅の刃 無限列車編场景）"""
        data = _jellyfin_movie_data(title="劇場版 鬼滅の刃 無限列車編")
        item = extract_jellyfin_data(data)
        assert item.media_type == "movie"

    def test_movie_keeps_movie_regardless_of_ori_title(self):
        """ori_title 含 Drama 也不再改判（源声明优先）"""
        data = _jellyfin_movie_data(title="半沢直樹", ori_title="Hanzawa Naoki Drama")
        item = extract_jellyfin_data(data)
        assert item.media_type == "movie"


# ===== Trakt =====


def _make_trakt_episode_item(title="鬼滅の刃", original_title=None):
    show = {"ids": {"tmdb": "123"}, "title": title}
    if original_title:
        show["original_title"] = original_title
    episode = {"season": 1, "number": 1, "ids": {"trakt": 100}}
    return TraktHistoryItem(
        id=1,
        watched_at="2024-06-01T12:00:00.000Z",
        action="watch",
        type="episode",
        show=show,
        episode=episode,
    )


def _make_trakt_movie_item(
    title="劇場版 鬼滅の刃 無限列車編", original_title=None, tmdb="456"
):
    movie = {
        "title": title,
        "ids": {"trakt": 200, "tmdb": tmdb},
        "released": "2020-10-16",
    }
    if original_title:
        movie["original_title"] = original_title
    return TraktHistoryItem(
        id=2,
        watched_at="2024-06-01T12:00:00.000Z",
        action="watch",
        type="movie",
        movie=movie,
    )


class TestTraktMediaTypeDetection:
    """Trakt sync_service 接入 detect_media_type"""

    def _make_service(self):
        return TraktSyncService()

    def test_episode_declaration_is_authoritative(self):
        """Trakt type=episode → episode，标题关键词不再改判

        Trakt 的 history ``type`` 明确声明 episode/movie，是权威信息。
        """
        svc = self._make_service()
        for title in ("鬼滅の刃 OVA", "進撃の巨人 OAD", "逃げるは恥だが役に立つ 日剧"):
            item = _make_trakt_episode_item(title=title)
            with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
                mock_bd.get_title_by_tmdb_id.return_value = title
                result = svc._convert_trakt_history_to_custom_item("user1", item)
            assert result is not None
            assert result.media_type == "episode", title

    def test_episode_normal_title_keeps_episode(self):
        """普通剧集标题保持 episode（呪術廻戦场景）"""
        svc = self._make_service()
        item = _make_trakt_episode_item(title="呪術廻戦")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "呪術廻戦"
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "episode"

    def test_movie_declaration_is_authoritative(self):
        """Trakt type=movie → movie，不被标题的「真人版」改判 real_action"""
        svc = self._make_service()
        item = _make_trakt_movie_item(title="真人版 鬼滅の刃")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = None
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "movie"

    def test_movie_normal_title_keeps_movie(self):
        """普通电影标题保持 movie（劇場版 鬼滅の刃 無限列車編场景）"""
        svc = self._make_service()
        item = _make_trakt_movie_item(title="劇場版 鬼滅の刃 無限列車編")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = None
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "movie"
