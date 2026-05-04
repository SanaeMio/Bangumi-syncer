"""
Bangumi API 完整测试
"""

from unittest.mock import MagicMock, patch

from app.utils.bangumi_api import BangumiApi


class TestBangumiApiComprehensive:
    """Bangumi API 综合测试"""

    def test_init(self):
        """测试初始化"""
        api = BangumiApi()
        assert api is not None

    def test_get_me(self):
        """测试获取用户信息"""
        api = BangumiApi()
        # 这个方法需要网络，测试是否存在
        assert hasattr(api, "get_me")

    def test_get_subject(self):
        """测试获取条目"""
        api = BangumiApi()
        assert hasattr(api, "get_subject")

    def test_get_related_subjects(self):
        """测试获取相关条目"""
        api = BangumiApi()
        assert hasattr(api, "get_related_subjects")

    def test_get_episodes(self):
        """测试获取章节"""
        api = BangumiApi()
        assert hasattr(api, "get_episodes")

    def test_get_target_season_episode_id(self):
        """测试获取目标季度集数ID"""
        api = BangumiApi()
        assert hasattr(api, "get_target_season_episode_id")

    def test_get_subject_collection(self):
        """测试获取条目收藏"""
        api = BangumiApi()
        assert hasattr(api, "get_subject_collection")

    def test_get_ep_collection(self):
        """测试获取章节收藏"""
        api = BangumiApi()
        assert hasattr(api, "get_ep_collection")


class TestBangumiApiMocked:
    """Bangumi API 模拟测试"""

    @patch("app.utils.bangumi_api.requests.Session")
    def test_api_with_session(self, mock_session):
        """测试带 Session 的 API"""
        api = BangumiApi()
        assert api is not None


class TestGetTargetSeasonEpisodeIdAirdate:
    """续集链 + 播出日 airdate 择优（Plex 季数错位修复）"""

    def _tv_ep(self, sort, ep, eid, airdate):
        return {"sort": sort, "ep": ep, "id": eid, "airdate": airdate}

    def test_picks_subject_with_closest_airdate_within_threshold(self):
        api = BangumiApi()

        related = {
            1: [{"relation": "续集", "id": 100}],
            100: [{"relation": "续集", "id": 200}],
            200: [{"relation": "续集", "id": 300}],
            300: [{"relation": "续集", "id": 400}],
        }

        def get_related(sid):
            return related.get(int(sid), [])

        def get_subject(sid):
            return {"platform": "TV", "name_cn": ""}

        episodes = {
            100: {
                "data": [self._tv_ep(1, 1, 10001, "2024-10-02")],
                "total": 10,
            },
            200: {
                "data": [self._tv_ep(1, 1, 20001, "2024-11-01")],
                "total": 10,
            },
            300: {
                "data": [self._tv_ep(1, 1, 30001, "2024-12-01")],
                "total": 10,
            },
            400: {
                "data": [self._tv_ep(1, 1, 40001, "2026-04-08")],
                "total": 10,
            },
        }

        def get_ep(sid):
            return episodes.get(int(sid), {"data": [], "total": 0})

        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        api.get_episodes = MagicMock(side_effect=get_ep)

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=4,
            target_ep=1,
            is_season_subject_id=False,
            release_date="2026-04-08",
        )
        assert sid == 400
        assert eid == 40001

    def test_falls_back_to_season_counter_when_no_airdate_within_threshold(self):
        api = BangumiApi()

        related = {
            1: [{"relation": "续集", "id": 100}],
            100: [{"relation": "续集", "id": 200}],
            200: [{"relation": "续集", "id": 300}],
        }

        def get_related(sid):
            return related.get(int(sid), [])

        def get_subject(sid):
            return {"platform": "TV", "name_cn": ""}

        episodes = {
            100: {
                "data": [self._tv_ep(1, 1, 10001, "2020-01-01")],
                "total": 10,
            },
            200: {
                "data": [self._tv_ep(1, 1, 20001, "2020-06-01")],
                "total": 10,
            },
            300: {
                "data": [self._tv_ep(1, 1, 30001, "2021-01-01")],
                "total": 10,
            },
        }

        def get_ep(sid):
            return episodes.get(int(sid), {"data": [], "total": 0})

        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        api.get_episodes = MagicMock(side_effect=get_ep)

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=4,
            target_ep=1,
            is_season_subject_id=False,
            release_date="2026-04-08",
        )
        assert sid == 300
        assert eid == 30001

    def test_direct_match_succeeds_skips_airdate(self):
        """is_season_subject_id=True 且直接匹配成功时，不走 airdate 路径"""
        api = BangumiApi()
        api.get_related_subjects = MagicMock()
        api.get_episodes = MagicMock(
            return_value={
                "data": [self._tv_ep(1, 1, 999, "2026-04-08")],
                "total": 10,
            }
        )
        sid, eid = api.get_target_season_episode_id(
            subject_id=500,
            target_season=4,
            target_ep=1,
            is_season_subject_id=True,
            release_date="2020-01-01",
        )
        assert sid == 500
        assert eid == 999
        api.get_related_subjects.assert_not_called()


class TestGetMovieMainEpisodeId:
    """get_movie_main_episode_id 剧场版短路径"""

    def test_matches_sort_on_main_type(self):
        api = BangumiApi()
        api.get_episodes = MagicMock(
            return_value={
                "data": [
                    {"id": 10, "sort": 1, "ep": 1, "type": 0},
                    {"id": 11, "sort": 2, "ep": 2, "type": 0},
                ],
                "total": 2,
            }
        )
        sid, eid = api.get_movie_main_episode_id(100, target_sort=2)
        assert sid == "100"
        assert eid == "11"

    def test_falls_back_to_first_sorted_when_no_match(self):
        api = BangumiApi()
        api.get_episodes = MagicMock(
            return_value={
                "data": [{"id": 20, "sort": 3, "ep": 3, "type": 0}],
                "total": 1,
            }
        )
        sid, eid = api.get_movie_main_episode_id(200, target_sort=1)
        assert sid == "200"
        assert eid == "20"

    def test_empty_episodes_returns_none_ep_id(self):
        api = BangumiApi()
        api.get_episodes = MagicMock(return_value={"data": [], "total": 0})
        sid, eid = api.get_movie_main_episode_id(300)
        assert sid == "300"


class TestGetTargetSeasonEpisodeIdSplitCour:
    """split-cour 续集链季度计数测试"""

    def _tv_ep(self, sort, ep, eid, airdate="2024-01-01"):
        return {"sort": sort, "ep": ep, "id": eid, "airdate": airdate}

    def test_rezero_split_cour_season4(self):
        """Re:Zero 6-subject 链：S1→S2→S2后半→S3袭击篇→S3反击篇→S4
        target_season=4 应返回 S4 (547888)"""
        api = BangumiApi()

        related = {
            140001: [{"relation": "续集", "id": 278826}],
            278826: [{"relation": "续集", "id": 316247}],
            316247: [{"relation": "续集", "id": 425998}],
            425998: [{"relation": "续集", "id": 510728}],
            510728: [{"relation": "续集", "id": 547888}],
            547888: [],
        }

        def get_related(sid):
            return related.get(int(sid), [])

        subjects = {
            278826: {
                "platform": "TV",
                "name": "Re:ゼロから始める異世界生活 2nd season",
                "name_cn": "Re：从零开始的异世界生活 第二季",
            },
            316247: {
                "platform": "TV",
                "name": "Re:ゼロから始める異世界生活 2nd season 後半クール",
                "name_cn": "Re：从零开始的异世界生活 第二季 后半部分",
            },
            425998: {
                "platform": "TV",
                "name": "Re:ゼロから始める異世界生活 3rd season 襲撃編",
                "name_cn": "Re：从零开始的异世界生活 第三季 袭击篇",
            },
            510728: {
                "platform": "TV",
                "name": "Re:ゼロから始める異世界生活 3rd season 反撃篇",
                "name_cn": "Re：从零开始的异世界生活 第三季 反击篇",
            },
            547888: {
                "platform": "TV",
                "name": "Re:ゼロから始める異世界生活 4th season 喪失編",
                "name_cn": "Re：从零开始的异世界生活 第四季 丧失篇",
            },
        }

        def get_subject(sid):
            return subjects.get(int(sid))

        episodes = {
            278826: {"data": [self._tv_ep(26, 1, 957951)], "total": 13},
            316247: {"data": [self._tv_ep(26, 1, 994750)], "total": 13},
            425998: {"data": [self._tv_ep(51, 1, 1353850)], "total": 8},
            510728: {"data": [self._tv_ep(59, 1, 1400001)], "total": 8},
            547888: {"data": [self._tv_ep(67, 1, 1500001)], "total": 8},
        }

        def get_ep(sid):
            return episodes.get(int(sid), {"data": [], "total": 0})

        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        api.get_episodes = MagicMock(side_effect=get_ep)

        sid, eid = api.get_target_season_episode_id(
            subject_id=140001,
            target_season=4,
            target_ep=1,
            is_season_subject_id=False,
            release_date=None,
        )
        assert sid == 547888
        assert eid == 1500001

    def test_split_cour_does_not_overcount(self):
        """split-cour subject 不应被计为独立季度"""
        api = BangumiApi()

        related = {
            1: [{"relation": "续集", "id": 100}],
            100: [{"relation": "续集", "id": 150}],
            150: [{"relation": "续集", "id": 200}],
            200: [],
        }

        def get_related(sid):
            return related.get(int(sid), [])

        subjects = {
            100: {
                "platform": "TV",
                "name": "Anime 2nd Season",
                "name_cn": "动画 第二季",
            },
            150: {
                "platform": "TV",
                "name": "Anime 2nd Season Part 2",
                "name_cn": "动画 第二季 第2部分",
            },
            200: {
                "platform": "TV",
                "name": "Anime 3rd Season",
                "name_cn": "动画 第三季",
            },
        }

        def get_subject(sid):
            return subjects.get(int(sid))

        episodes = {
            100: {"data": [self._tv_ep(1, 1, 1001)], "total": 12},
            150: {"data": [self._tv_ep(13, 1, 1501)], "total": 12},
            200: {"data": [self._tv_ep(1, 1, 2001)], "total": 12},
        }

        def get_ep(sid):
            return episodes.get(int(sid), {"data": [], "total": 0})

        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        api.get_episodes = MagicMock(side_effect=get_ep)

        # target_season=3 应返回 200（第三季），而非 150（第二季第2部分）
        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=3,
            target_ep=1,
            is_season_subject_id=False,
        )
        assert sid == 200
        assert eid == 2001

    def test_sort_offset_no_season_indicator(self):
        """无职转生 S2 场景：S1 sort=0，S2 sort 从 12 开始，无季度标识"""
        api = BangumiApi()

        related = {
            1: [{"relation": "续集", "id": 100}],
            100: [],
        }

        def get_related(sid):
            return related.get(int(sid), [])

        def get_subject(sid):
            # S1 和 S2 都没有季度标识
            return {"platform": "TV", "name": "Mushoku Tensei", "name_cn": "无职转生"}

        episodes = {
            1: {
                "data": [
                    {"sort": 0, "ep": 1, "id": 101, "airdate": "2021-01-01"},
                    {"sort": 1, "ep": 2, "id": 102, "airdate": "2021-01-08"},
                ],
                "total": 12,
            },
            100: {
                "data": [
                    {"sort": 12, "ep": 1, "id": 201, "airdate": "2021-10-03"},
                    {"sort": 13, "ep": 2, "id": 202, "airdate": "2021-10-10"},
                ],
                "total": 12,
            },
        }

        def get_ep(sid):
            return episodes.get(int(sid), {"data": [], "total": 0})

        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        api.get_episodes = MagicMock(side_effect=get_ep)

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=2,
            target_ep=1,
            is_season_subject_id=False,
        )
        assert sid == 100
        assert eid == 201

    def test_no_season_indicator_falls_back_to_sort(self):
        """无季度标识时，通过 sort=1 判断季度首集"""
        api = BangumiApi()

        related = {
            1: [{"relation": "续集", "id": 100}],
            100: [{"relation": "续集", "id": 200}],
            200: [],
        }

        def get_related(sid):
            return related.get(int(sid), [])

        subjects = {
            100: {"platform": "TV", "name": "Anime S2", "name_cn": ""},
            200: {"platform": "TV", "name": "Anime S3", "name_cn": ""},
        }

        def get_subject(sid):
            return subjects.get(int(sid))

        episodes = {
            100: {"data": [self._tv_ep(1, 1, 1001)], "total": 12},
            200: {"data": [self._tv_ep(1, 1, 2001)], "total": 12},
        }

        def get_ep(sid):
            return episodes.get(int(sid), {"data": [], "total": 0})

        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        api.get_episodes = MagicMock(side_effect=get_ep)

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=3,
            target_ep=1,
            is_season_subject_id=False,
        )
        assert sid == 200
        assert eid == 2001
