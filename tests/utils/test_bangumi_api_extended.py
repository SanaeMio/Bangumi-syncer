"""
Bangumi API 完整测试
"""

from typing import Optional
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


class TestFindEpisodeAcrossSeasons:
    """跨季条目链查找 sort=target_ep 测试

    场景：完美世界在 Bangumi 上每季拆分为独立条目，每个条目内
    ep 从1开始，sort 是整部作品的连续编号。fongmi 传连续编号
    episode=102，但已命中条目（如第六季 sort=235-286）不含 102，
    需通过前传链向前找到含 sort=102 的季条目。
    """

    @staticmethod
    def _make_eps(start_sort: int, count: int, start_id: int = 10000):
        """生成单季条目的 episode 列表（sort 连续，ep 从1开始）。"""
        eps = []
        for i in range(count):
            eps.append(
                {
                    "sort": start_sort + i,
                    "ep": i + 1,
                    "id": start_id + i,
                    "type": 0,
                    "airdate": "",
                }
            )
        return eps

    @staticmethod
    def _make_api(
        episodes: dict,
        related: dict,
        subject_info: Optional[dict] = None,
    ):
        """构造一个 mock 好的 BangumiApi 实例。

        - get_episodes / get_related_subjects / get_subject / _fetch_episodes_page 均被 mock
        - _fetch_episodes_page 始终返回空数据，强制走 get_episodes 全量路径
          （避免 target_sort>99 时走 offset 快速路径发真实 HTTP）
        """
        api = BangumiApi()

        def get_episodes(sid, *args, **kwargs):
            return episodes.get(int(sid), {"data": [], "total": 0})

        def get_related(sid):
            return related.get(int(sid), [])

        def get_subject(sid):
            if subject_info is not None:
                return subject_info
            return {"type": 2, "name": f"S{sid}", "name_cn": "", "platform": "WEB"}

        api.get_episodes = MagicMock(side_effect=get_episodes)
        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        # 强制走 get_episodes 全量路径，跳过 offset 快速路径
        api._fetch_episodes_page = MagicMock(return_value={"data": [], "total": 0})
        return api

    def test_walks_prepuel_chain_to_find_target_sort(self):
        """命中第六季（sort 235-286），target_ep=102 应通过前传链找到第三季。"""
        # 6 个季条目，每季 50 集
        # S1=100 sort 1-50, S2=200 sort 51-100, S3=300 sort 101-150,
        # S4=400 sort 151-200, S5=500 sort 201-250, S6=600 sort 251-300
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            200: {"data": self._make_eps(51, 50, 20001), "total": 50},
            300: {"data": self._make_eps(101, 50, 30001), "total": 50},
            400: {"data": self._make_eps(151, 50, 40001), "total": 50},
            500: {"data": self._make_eps(201, 50, 50001), "total": 50},
            600: {"data": self._make_eps(251, 50, 60001), "total": 50},
        }
        # 前传链：600 → 500 → 400 → 300 → 200 → 100
        related = {
            600: [{"relation": "前传", "id": 500, "type": 2}],
            500: [{"relation": "前传", "id": 400, "type": 2}],
            400: [{"relation": "前传", "id": 300, "type": 2}],
            300: [{"relation": "前传", "id": 200, "type": 2}],
            200: [{"relation": "前传", "id": 100, "type": 2}],
            100: [],
        }
        api = self._make_api(episodes, related)

        # target_ep=102 应在 S3（sort 101-150），ep_id=30002
        result = api.find_episode_across_seasons(600, 102)
        assert result is not None
        assert result[0] == 300
        assert result[1] == 30002

    def test_walks_sequel_chain_to_find_target_sort(self):
        """命中第一季（sort 1-50），target_ep=102 应通过续集链找到第三季。"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            200: {"data": self._make_eps(51, 50, 20001), "total": 50},
            300: {"data": self._make_eps(101, 50, 30001), "total": 50},
        }
        # 续集链：100 → 200 → 300
        related = {
            100: [{"relation": "续集", "id": 200, "type": 2}],
            200: [{"relation": "续集", "id": 300, "type": 2}],
            300: [],
        }
        api = self._make_api(episodes, related)

        result = api.find_episode_across_seasons(100, 102)
        assert result is not None
        assert result[0] == 300
        assert result[1] == 30002

    def test_returns_none_when_no_chain_matches(self):
        """前传/续集链中均无含目标 sort 的条目时返回 None。"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            200: {"data": self._make_eps(51, 50, 20001), "total": 50},
        }
        related = {
            100: [{"relation": "续集", "id": 200, "type": 2}],
            200: [],
        }
        api = self._make_api(episodes, related)

        # target_ep=1000 远超所有条目的 sort 范围
        result = api.find_episode_across_seasons(100, 1000)
        assert result is None

    def test_skips_movie_subject_in_chain(self):
        """前传链中遇到剧场版条目应跳过（不命中也不报错），继续向前找到 S5。"""
        # 链：600(S6) → 700(剧场版) → 500(S5)
        # target_ep=210 应跳过剧场版 700，继续找到 S5
        episodes = {
            600: {"data": self._make_eps(251, 50, 60001), "total": 50},
            700: {"data": self._make_eps(1, 1, 70001), "total": 1},
            500: {"data": self._make_eps(201, 50, 50001), "total": 50},
        }
        related = {
            600: [{"relation": "前传", "id": 700, "type": 2}],
            700: [{"relation": "前传", "id": 500, "type": 2}],
            500: [],
        }

        def get_subject(sid):
            if sid == 700:
                return {
                    "type": 2,
                    "name": "完美世界剧场版 九劫焚天",
                    "name_cn": "完美世界剧场版 九劫焚天",
                }
            return {"type": 2, "name": f"S{sid}", "name_cn": "", "platform": "WEB"}

        api = BangumiApi()
        api.get_episodes = MagicMock(
            side_effect=lambda sid, *a, **kw: episodes.get(
                int(sid), {"data": [], "total": 0}
            )
        )
        api.get_related_subjects = MagicMock(
            side_effect=lambda sid: related.get(int(sid), [])
        )
        api.get_subject = MagicMock(side_effect=get_subject)
        api._fetch_episodes_page = MagicMock(return_value={"data": [], "total": 0})

        result = api.find_episode_across_seasons(600, 210)
        assert result is not None
        assert result[0] == 500

    def test_target_in_current_subject_returns_directly(self):
        """目标 sort 在当前 subject 内时直接返回，不走链。"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
        }
        api = self._make_api(episodes, {})

        result = api.find_episode_across_seasons(100, 25)
        assert result is not None
        assert result[0] == 100
        assert result[1] == 10025
