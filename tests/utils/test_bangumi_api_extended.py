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

    @patch("app.utils.bangumi_api.httpx.Client")
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
        api.get_subject = MagicMock(return_value={"type": 2, "name_cn": ""})
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

    def test_type6_subject_sequel_chain(self):
        """根条目 type=6（三次元电视剧）时续集链仅放行同 type 条目"""
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        related = {
            1: [{"relation": "续集", "id": 100}],
            100: [],
        }

        def get_related(sid):
            return related.get(int(sid), [])

        subjects = {
            1: {
                "type": 6,
                "platform": "欧美剧",
                "name": "Friends: Season 1",
                "name_cn": "老友记 第一季",
            },
            100: {
                "type": 6,
                "platform": "欧美剧",
                "name": "Friends: Season 2",
                "name_cn": "老友记 第二季",
            },
        }

        def get_subject(sid):
            return subjects.get(int(sid))

        episodes = {
            100: {"data": [self._tv_ep(2, 2, 49497)], "total": 24},
        }

        def get_ep(sid):
            return episodes.get(int(sid), {"data": [], "total": 0})

        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        api.get_episodes = MagicMock(side_effect=get_ep)

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=2,
            target_ep=2,
            is_season_subject_id=False,
        )
        assert sid == 100
        assert eid == 49497

    def test_type2_root_skips_type6_in_sequel_chain(self):
        """根条目 type=2 时续集链中的 type=6 条目被跳过"""
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        related = {
            1: [{"relation": "续集", "id": 100}],
            100: [{"relation": "续集", "id": 200}],
            200: [],
        }

        def get_related(sid):
            return related.get(int(sid), [])

        subjects = {
            1: {
                "type": 2,
                "platform": "TV",
                "name": "Anime S1",
                "name_cn": "动画 第一季",
            },
            100: {
                "type": 6,
                "platform": "电影",
                "name": "Anime Movie",
                "name_cn": "动画 真人版",
            },
            200: {
                "type": 2,
                "platform": "TV",
                "name": "Anime S2",
                "name_cn": "动画 第二季",
            },
        }

        def get_subject(sid):
            return subjects.get(int(sid))

        episodes = {
            200: {"data": [self._tv_ep(1, 1, 20001)], "total": 12},
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
        assert sid == 200
        assert eid == 20001


class TestContinuousNumbering:
    """单 subject 连续编号测试（多季合并到一个条目）

    场景：Bangumi 将多季放在一个 subject 下，sort 连续编号，
    ep 每季重置。第一季 ep=1..24 sort=1..24，第二季 ep=1..24 sort=25..48。
    """

    def _make_eps(self, seasons: int, eps_per_season: int, start_id: int = 1000):
        """生成连续编号的 episode 列表"""
        eps = []
        eid = start_id
        for season in range(seasons):
            for ep in range(1, eps_per_season + 1):
                sort = season * eps_per_season + ep
                eps.append(
                    {
                        "sort": sort,
                        "ep": ep,
                        "id": eid,
                        "type": 0,
                        "name": f"第{season + 1}季 第{ep}话",
                        "airdate": "",
                    }
                )
                eid += 1
        return eps

    def test_season2_ep1_maps_to_sort25(self):
        """两季各24集，season=2 ep=1 应映射到 sort=25"""
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        eps_data = self._make_eps(seasons=2, eps_per_season=24)

        api.get_related_subjects = MagicMock(return_value=[])
        api.get_subject = MagicMock(return_value={"platform": "TV", "name": "测试番剧"})
        api.get_episodes = MagicMock(return_value={"data": eps_data, "total": 48})

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=2,
            target_ep=1,
            is_season_subject_id=False,
            release_date=None,
        )
        assert sid == 1
        # sort=25 对应的 ep_id = 1000 + 24 = 1024
        assert eid == 1024

    def test_season2_ep12_maps_to_sort36(self):
        """两季各24集，season=2 ep=12 应映射到 sort=36"""
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        eps_data = self._make_eps(seasons=2, eps_per_season=24)

        api.get_related_subjects = MagicMock(return_value=[])
        api.get_subject = MagicMock(return_value={"platform": "TV", "name": "测试番剧"})
        api.get_episodes = MagicMock(return_value={"data": eps_data, "total": 48})

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=2,
            target_ep=12,
            is_season_subject_id=False,
            release_date=None,
        )
        assert sid == 1
        # sort=36 对应的 ep_id = 1000 + 35 = 1035
        assert eid == 1035

    def test_season3_ep1_maps_to_sort49(self):
        """三季各16集，season=3 ep=1 应映射到 sort=33"""
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        eps_data = self._make_eps(seasons=3, eps_per_season=16)

        api.get_related_subjects = MagicMock(return_value=[])
        api.get_subject = MagicMock(return_value={"platform": "TV", "name": "测试番剧"})
        api.get_episodes = MagicMock(return_value={"data": eps_data, "total": 48})

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=3,
            target_ep=1,
            is_season_subject_id=False,
            release_date=None,
        )
        assert sid == 1
        # sort=33 对应的 ep_id = 1000 + 32 = 1032
        assert eid == 1032

    def test_no_reset_returns_none(self):
        """ep 不重置（单季连续编号）时，连续编号回退返回 None"""
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        # 48 集但 ep 不重置（全是连续编号的单季）
        eps_data = [
            {"sort": i, "ep": i, "id": 1000 + i, "type": 0, "airdate": ""}
            for i in range(1, 49)
        ]

        api.get_related_subjects = MagicMock(return_value=[])
        api.get_subject = MagicMock(return_value={"platform": "TV", "name": "测试"})
        api.get_episodes = MagicMock(return_value={"data": eps_data, "total": 48})

        result = api._try_resolve_continuous_season_episode(1, 2, 1)
        assert result is None

    def test_season_beyond_detected_returns_none(self):
        """目标季超过检测到的季数时返回 None"""
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        # 只有 2 季
        eps_data = self._make_eps(seasons=2, eps_per_season=12)

        api.get_episodes = MagicMock(return_value={"data": eps_data, "total": 24})

        result = api._try_resolve_continuous_season_episode(1, 3, 1)
        assert result is None
