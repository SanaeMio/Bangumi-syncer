"""
Bangumi API 完整测试
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api._archive_shortcut import ShortcutResult
from app.utils.season_title import extract_explicit_season


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

    def test_ep_missing_uses_sort_jump_detection(self):
        """ep 字段缺失（archive dump 不含 ep 列）时，
        通过 sort 跳变检测季度边界。

        场景：bangumi/Archive 官方 dump 的 episode 不含 ep 字段，
        但部分条目的 sort 在每季开始时重置为 1（如第一季 sort=1-24，
        第二季 sort=1-24 重新计数）。通过 sort 从高值跳到 1 检测季度边界。

        注意：此检测仅对 sort 重置的条目有效。
        对于 sort 连续编号（1-48）的单 subject 多季合并条目，
        无法通过 sort 检测边界，需依赖续集链查找。
        """
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        # 两季各 24 集，sort 每季重置（sort=1-24, sort=1-24），
        # ep 全为 None（模拟 archive dump 缺 ep 字段）
        eps_data = []
        eid = 1000
        for _season in range(2):
            for ep_in_season in range(1, 25):
                eps_data.append(
                    {
                        "sort": ep_in_season,  # 每季 sort 重置
                        "ep": None,  # archive dump 缺 ep 字段
                        "id": eid,
                        "type": 0,
                        "airdate": "",
                    }
                )
                eid += 1

        api.get_episodes = MagicMock(return_value={"data": eps_data, "total": 48})

        # season=2 ep=1 应通过 sort 跳变检测到边界（sort=24 → sort=1）
        # 并定位到第二季 sort=1 的章节
        result = api._try_resolve_continuous_season_episode(1, 2, 1)
        assert result is not None
        sid, eid_result = result
        assert sid == 1
        # 第二季 sort=1 对应的 ep_id = 1000 + 24 = 1024
        assert eid_result == 1024

    def test_ep_missing_single_season_returns_none(self):
        """ep 字段缺失且单季（sort 不重置）时返回 None"""
        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        # 48 集连续编号，ep 全为 None，无 sort 重置
        eps_data = [
            {"sort": i, "ep": None, "id": 1000 + i, "type": 0, "airdate": ""}
            for i in range(1, 49)
        ]

        api.get_episodes = MagicMock(return_value={"data": eps_data, "total": 48})

        # 无 sort 重置，检测不到季度边界，应返回 None
        result = api._try_resolve_continuous_season_episode(1, 2, 1)
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
        subject_info: dict | None = None,
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

    @staticmethod
    def _make_api_with_archive(
        episodes: dict,
        related: dict,
        sequel_chain: list[int] | None,
        archive_hit: bool = True,
        subject_info: dict | None = None,
    ):
        """构造一个 mock 好的 BangumiApi 实例，并启用 archive 短路。

        Args:
            sequel_chain: try_find_sequel_chain 返回的链。None 表示 archive miss。
            archive_hit: True=archive 命中；False=archive miss（降级到逐跳）
        """
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

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
        api._fetch_episodes_page = MagicMock(return_value={"data": [], "total": 0})

        # mock archive shortcut
        api._archive = MagicMock()
        api._archive.enabled = True

        # archive miss 时其他 try_* 方法也要返回 hit=False，
        # 否则 MagicMock 默认返回值会让 _find_related_id_by_relation 误判命中
        miss_result = ShortcutResult(False, None, "archive_miss")
        api._archive.try_find_related_id_by_relation = MagicMock(
            return_value=miss_result
        )
        api._archive.try_get_subject = MagicMock(return_value=miss_result)
        api._archive.try_get_episodes = MagicMock(return_value=miss_result)
        api._archive.try_get_related_subjects = MagicMock(return_value=miss_result)

        if archive_hit:
            api._archive.try_find_sequel_chain = MagicMock(
                return_value=ShortcutResult(True, sequel_chain or [], "archive_hit")
            )
        else:
            api._archive.try_find_sequel_chain = MagicMock(
                return_value=ShortcutResult(False, None, "archive_miss")
            )

        # 前传链短路默认 miss，避免 sequel 方向测试误触发（prequel 方向另有专门用例）
        api._archive.try_find_prequel_chain = MagicMock(
            return_value=ShortcutResult(False, None, "archive_miss")
        )
        return api

    def test_archive_sequel_chain_finds_target_sort(self):
        """archive 命中时使用预构图的续集链批量遍历找到目标 ep。

        场景：续集链 100 → 200 → 300，archive 返回 [200, 300]。
        target_ep=102 应在 S3（sort 101-150），通过 archive 链找到。
        """
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            200: {"data": self._make_eps(51, 50, 20001), "total": 50},
            300: {"data": self._make_eps(101, 50, 30001), "total": 50},
        }
        related = {
            100: [{"relation": "续集", "id": 200, "type": 2}],
            200: [{"relation": "续集", "id": 300, "type": 2}],
            300: [],
        }
        # archive 命中，返回完整续集链
        api = self._make_api_with_archive(
            episodes, related, sequel_chain=[200, 300], archive_hit=True
        )

        result = api.find_episode_across_seasons(100, 102)
        assert result is not None
        assert result[0] == 300
        assert result[1] == 30002

        # archive 命中后应直接返回，不走逐跳 _find_related_id_by_relation
        # 验证：get_related_subjects 不应被 _walk_chain_for_episode 调用
        # （find_episode_across_seasons 内部 get_episodes 已被调用一次用于判断方向）
        # get_related_subjects 仅在 archive miss 降级路径才会被 _find_related_id_by_relation 调用
        # archive hit 路径下不应调用 get_related_subjects
        assert not api.get_related_subjects.called

    def test_archive_sequel_chain_empty_returns_none_directly(self):
        """archive 命中但链为空时降级到逐跳 API（关联数据可能不完整）。

        场景：subject 在 archive 中存在但无续集链（chain=[]）。
        archive 关联数据可能不完整，应降级到逐跳 API 查找续集。
        """
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            200: {"data": self._make_eps(51, 50, 20001), "total": 50},
        }
        related = {
            100: [{"relation": "续集", "id": 200, "type": 2}],
            200: [],
        }
        # archive 命中但链为空
        api = self._make_api_with_archive(
            episodes, related, sequel_chain=[], archive_hit=True
        )

        result = api.find_episode_across_seasons(100, 102)
        # 100 和 200 都没有 sort=102，最终返回 None
        assert result is None

        # archive hit 但链空，降级到逐跳（调 get_related_subjects 找续集）
        assert api.get_related_subjects.called

    def test_archive_miss_falls_back_to_hop_by_hop(self):
        """archive miss 时降级到逐跳逻辑（保持原行为）。

        场景：subject 不在 archive DB 中（archive 不完整）。
        try_find_sequel_chain 返回 hit=False，应走原 _walk_chain_for_episode 逐跳逻辑。
        """
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            200: {"data": self._make_eps(51, 50, 20001), "total": 50},
            300: {"data": self._make_eps(101, 50, 30001), "total": 50},
        }
        related = {
            100: [{"relation": "续集", "id": 200, "type": 2}],
            200: [{"relation": "续集", "id": 300, "type": 2}],
            300: [],
        }
        # archive miss
        api = self._make_api_with_archive(
            episodes, related, sequel_chain=None, archive_hit=False
        )

        result = api.find_episode_across_seasons(100, 102)
        assert result is not None
        assert result[0] == 300
        assert result[1] == 30002

        # archive miss 时降级路径会调用 _find_related_id_by_relation → get_related_subjects
        assert api.get_related_subjects.called

    def test_archive_sequel_chain_skips_movie_subject(self):
        """archive 链上遇到剧场版条目应跳过，继续找下一个。

        场景：链 100 → 700(剧场版) → 300，archive 返回 [700, 300]。
        target_ep=102 应跳过剧场版 700，找到 S3 (300)。
        """
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            700: {"data": self._make_eps(1, 1, 70001), "total": 1},
            300: {"data": self._make_eps(101, 50, 30001), "total": 50},
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
        api.get_related_subjects = MagicMock(return_value=[])
        api.get_subject = MagicMock(side_effect=get_subject)
        api._fetch_episodes_page = MagicMock(return_value={"data": [], "total": 0})

        # mock archive 命中，返回链 [700, 300]
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        api._archive = MagicMock()
        api._archive.enabled = True
        api._archive.try_find_sequel_chain = MagicMock(
            return_value=ShortcutResult(True, [700, 300], "archive_hit")
        )
        # 前传方向走 search_previous_subjects → try_find_prequel_chain（archive miss 降级在线逐跳）
        api._archive.try_find_prequel_chain = MagicMock(
            return_value=ShortcutResult(False, None, "archive_miss")
        )

        result = api.find_episode_across_seasons(100, 102)
        assert result is not None
        assert result[0] == 300
        assert result[1] == 30002

    def test_archive_sequel_chain_no_target_falls_back_to_hop_by_hop(self):
        """archive 命中但链上无 target_ep 时降级到逐跳 API（P1-5 修复）。

        场景：archive 链 [200] 但 S2 的 sort 范围不含 target_ep=1000，
        且 200 无续集。archive 链不完整时必须降级到逐跳 API 兜底，
        否则真实链 [200, 300]（300 含目标）会因 archive 漏掉 300 而丢失。
        本场景 200 无续集，降级后逐跳查到 200 仍无目标，最终返回 None。
        """
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            200: {"data": self._make_eps(51, 50, 20001), "total": 50},
        }
        related = {
            100: [{"relation": "续集", "id": 200, "type": 2}],
            200: [],
        }
        # archive 命中，链上只有 [200]，但 200 不含 target_ep=1000
        api = self._make_api_with_archive(
            episodes, related, sequel_chain=[200], archive_hit=True
        )

        result = api.find_episode_across_seasons(100, 1000)
        assert result is None
        # P1-5 修复：archive 链未命中目标时应降级到逐跳 API
        assert api.get_related_subjects.called

    def test_archive_prequel_chain_finds_target_sort(self):
        """功能二（前传推断）：archive 命中走 try_find_prequel_chain 快路径批量定位。

        场景：前传链 600 → 500 → 400（archive 返回 [500, 400]），
        target_ep=102 应在 S3（subject 400, sort 101-150）。
        验证：走 archive 快路径，不调用逐跳 get_related_subjects。
        """
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        episodes = {
            600: {"data": self._make_eps(251, 50, 60001), "total": 50},
            500: {"data": self._make_eps(201, 50, 50001), "total": 50},
            400: {"data": self._make_eps(101, 50, 40001), "total": 50},
        }
        related = {
            600: [{"relation": "前传", "id": 500, "type": 2}],
            500: [{"relation": "前传", "id": 400, "type": 2}],
            400: [],
        }
        api = self._make_api_with_archive(
            episodes, related, sequel_chain=None, archive_hit=True
        )
        # 覆盖前传链短路为命中（_make_api_with_archive 默认 miss）
        api._archive.try_find_prequel_chain = MagicMock(
            return_value=ShortcutResult(True, [500, 400], "archive_hit")
        )

        result = api.find_episode_across_seasons(600, 102)
        assert result is not None
        assert result[0] == 400
        assert result[1] == 40002
        # archive 命中快路径，不应走逐跳 get_related_subjects
        assert not api.get_related_subjects.called

    def test_archive_prequel_chain_no_target_returns_none(self):
        """功能二（前传推断）：archive 命中但前传链上无 target_ep 时返回 None。

        场景：archive 前传链 [500]，target_ep=1000 远超范围。
        600 的 sort 范围 251-300，1000 > 300 走 sequel 方向。
        archive sequel_chain=None（空），降级到逐跳 API，但 600 无续集关联，返回 None。
        """
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        episodes = {
            600: {"data": self._make_eps(251, 50, 60001), "total": 50},
            500: {"data": self._make_eps(201, 50, 50001), "total": 50},
        }
        related = {
            600: [{"relation": "前传", "id": 500, "type": 2}],
            500: [],
        }
        api = self._make_api_with_archive(
            episodes, related, sequel_chain=None, archive_hit=True
        )
        api._archive.try_find_prequel_chain = MagicMock(
            return_value=ShortcutResult(True, [500], "archive_hit")
        )

        result = api.find_episode_across_seasons(600, 1000)
        # 600 无续集，500 无 sort=1000，最终返回 None
        assert result is None
        # sequel 链空降级到逐跳，调 get_related_subjects 找续集
        assert api.get_related_subjects.called

    def test_archive_sequel_chain_navigates_to_target_season(self):
        """功能一（续集累计定位）：archive 短路 try_find_next_sequel_id 逐跳找到目标季。

        场景：S1=subject1(sort1-24)、S2=subject2(sort25-48)、S3=subject3(sort49-72)，
        各季为独立 archive subject。target_season=2, ep=3 → subject2, sort=27 → ep_id 2002。
        """
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        api = BangumiApi()
        api._get_episode_sync_limits = MagicMock(return_value=(10, 9999))

        eps = {
            1: {"data": self._make_eps(1, 24, 1000), "total": 24},
            2: {"data": self._make_eps(25, 24, 2000), "total": 24},
            3: {"data": self._make_eps(49, 24, 3000), "total": 24},
        }
        api.get_episodes = MagicMock(
            side_effect=lambda sid, *a, **kw: eps.get(
                int(sid), {"data": [], "total": 0}
            )
        )
        api.get_subject = MagicMock(
            return_value={"type": 2, "name": "测试番剧", "platform": "TV"}
        )

        api._archive = MagicMock()
        api._archive.enabled = True
        api._archive.try_find_next_sequel_id = MagicMock(
            side_effect=lambda sid: ShortcutResult(
                True, {1: 2, 2: 3, 3: None}.get(int(sid)), "archive_hit"
            )
        )
        api._archive.try_get_subject = MagicMock(
            return_value=ShortcutResult(False, None, "archive_miss")
        )
        api._archive.try_get_episodes = MagicMock(
            return_value=ShortcutResult(False, None, "archive_miss")
        )
        api._archive.try_find_sequel_chain = MagicMock(
            return_value=ShortcutResult(False, None, "archive_miss")
        )
        api._archive.try_find_prequel_chain = MagicMock(
            return_value=ShortcutResult(False, None, "archive_miss")
        )

        sid, eid = api.get_target_season_episode_id(
            subject_id=1,
            target_season=2,
            target_ep=3,
            is_season_subject_id=False,
            release_date=None,
        )
        assert sid == 2
        assert eid == 2002  # sort 27 -> id 2000 + (27 - 25)


# find_episode_across_seasons 的 target_season 季定位分支测试
# （避免仅凭全局 sort 命中最先含该 sort 的错误季，如 S04E13 误命中 S1E13）


def _make_eps_by_ep(
    start_ep: int, count: int, start_id: int, start_sort: int | None = None
):
    """生成单 cour 的 episode 列表（type=0）。"""
    eps = []
    for i in range(count):
        ep = start_ep + i
        sort = start_sort + i if start_sort is not None else ep
        eps.append(
            {
                "sort": sort,
                "ep": ep,
                "id": start_id + i,
                "type": 0,
                "airdate": "",
            }
        )
    return eps


def _make_eps_by_sort(start_sort: int, count: int, start_id: int):
    """生成仅含 sort、不含 ep 字段的 episode 列表（模拟 Archive 的 episode 表）。"""
    return [
        {"sort": start_sort + i, "id": start_id + i, "type": 0, "airdate": ""}
        for i in range(count)
    ]


def build_api_with_chain(subjects, episodes, sequel_chain, prequel_chain=None):
    """构造 mock 好的 BangumiApi，用预设关联链 + 条目/章节数据驱动季定位逻辑。

    subjects: {sid: {"name","name_cn","type"}}
    episodes: {sid: [ep dict]}
    sequel_chain / prequel_chain: subject id 列表（由近到远）
    """
    api = BangumiApi()

    def get_subject(sid):
        return subjects.get(int(sid))

    def get_episodes(sid, *args, **kwargs):
        data = episodes.get(int(sid), [])
        return {"data": data, "total": len(data)}

    api.get_subject = MagicMock(side_effect=get_subject)
    api.get_episodes = MagicMock(side_effect=get_episodes)
    api._fetch_episodes_page = MagicMock(return_value={"data": [], "total": 0})
    api._archive = MagicMock()
    api._archive.try_find_sequel_chain = MagicMock(
        return_value=ShortcutResult(True, sequel_chain or [], "archive_hit")
    )
    api.search_previous_subjects = MagicMock(return_value=prequel_chain or [])
    return api


class TestExtractExplicitSeason:
    """extract_explicit_season 纯函数：从标题提取明确声明的季度编号。"""

    def test_rezero_season_titles(self):
        # 各 cour 标题均携带「第X季」，应解析出对应季编号
        assert extract_explicit_season("Re：从零开始的异世界生活") is None
        assert extract_explicit_season("Re：从零开始的异世界生活 第二季") == 2
        assert extract_explicit_season("Re：从零开始的异世界生活 第二季 后半") == 2
        assert extract_explicit_season("Re：从零开始的异世界生活 第三季 袭击篇") == 3
        assert extract_explicit_season("Re：从零开始的异世界生活 第三季 反击篇") == 3
        assert extract_explicit_season("Re：从零开始的异世界生活 第四季 失坠篇") == 4
        assert extract_explicit_season("Re：从零开始的异世界生活 第四季 夺还篇") == 4

    def test_chinese_and_arabic_season_keywords(self):
        assert extract_explicit_season("进击的巨人 第四季") == 4
        assert extract_explicit_season("进击の巨人 第四期") == 4
        assert extract_explicit_season("第12期") == 12
        assert extract_explicit_season("第2季") == 2
        assert extract_explicit_season("某番剧 第十一季") == 11

    def test_english_season_keywords(self):
        assert extract_explicit_season("Attack on Titan Season 4") == 4
        assert extract_explicit_season("Anime 2nd season") == 2
        assert extract_explicit_season("Anime Season 3") == 3

    def test_no_season_declaration(self):
        # 无季声明（第一季本体 / 总集篇 / 续章）应返回 None
        assert extract_explicit_season("虫师 续章") is None
        assert extract_explicit_season("凡人修仙传") is None
        assert extract_explicit_season("") is None


class TestExtractSeasonNumber:
    """_extract_season_number：从条目名与中文名中提取季编号

    覆盖四种明确声明形式：「第 X 季 / 第 X 期」（阿拉伯数字与中文数字）、
    「Xnd/Xrd/Xth season」、「Season X」。条目名与中文名合并后解析；两者均不含
    季声明时返回 None（可能是第一季本体，也可能是总集篇）。
    """

    @staticmethod
    def _api() -> BangumiApi:
        return BangumiApi()

    def test_english_season_keyword_supported(self):
        api = self._api()
        assert api._extract_season_number("Attack on Titan Season 4", "") == 4
        assert api._extract_season_number("Anime Season 3", "") == 3

    def test_name_cn_merged_with_name(self):
        api = self._api()
        assert api._extract_season_number("某番剧", "某番剧 第二季") == 2
        assert (
            api._extract_season_number("Re：从零开始的异世界生活 第四季 夺还篇", "")
            == 4
        )

    def test_no_season_declaration_returns_none(self):
        api = self._api()
        assert api._extract_season_number("虫师 续章", "") is None
        assert api._extract_season_number("", "") is None

    def test_consistent_with_shared_helper(self):
        """与 extract_explicit_season 对同一输入结果一致"""
        api = self._api()
        for name, name_cn in [
            ("Attack on Titan Season 4", ""),
            ("Anime 2nd season", ""),
            ("某番剧 第十一季", ""),
            ("进击の巨人 第四期", ""),
            ("凡人修仙传", ""),
        ]:
            assert api._extract_season_number(name, name_cn) == extract_explicit_season(
                f"{name} {name_cn}"
            )


class TestFindEpisodeAcrossSeasonsBySeason:
    """find_episode_across_seasons 季定位分支：Re:Zero 多 cour 跨季场景。"""

    @staticmethod
    def _rezero_subjects():
        base = "Re：从零开始的异世界生活"
        return {
            140001: {"name": base, "name_cn": base, "type": 2},
            278826: {"name": f"{base} 第二季", "name_cn": f"{base} 第二季", "type": 2},
            316247: {
                "name": f"{base} 第二季 后半",
                "name_cn": f"{base} 第二季 后半",
                "type": 2,
            },
            425998: {
                "name": f"{base} 第三季 袭击篇",
                "name_cn": f"{base} 第三季 袭击篇",
                "type": 2,
            },
            510728: {
                "name": f"{base} 第三季 反击篇",
                "name_cn": f"{base} 第三季 反击篇",
                "type": 2,
            },
            547888: {
                "name": f"{base} 第四季 失坠篇",
                "name_cn": f"{base} 第四季 失坠篇",
                "type": 2,
            },
            633836: {
                "name": f"{base} 第四季 夺还篇",
                "name_cn": f"{base} 第四季 夺还篇",
                "type": 2,
            },
        }

    @staticmethod
    def _rezero_episodes():
        # 每 cour 11 集；S1 25 集；ep13 真实 id = 626044，S4 第二 cour ep2 真实 id = 1656859
        return {
            140001: _make_eps_by_ep(1, 25, 626032, start_sort=1),
            278826: _make_eps_by_ep(1, 11, 278000),
            316247: _make_eps_by_ep(1, 11, 316000),
            425998: _make_eps_by_ep(1, 11, 425000),
            510728: _make_eps_by_ep(1, 11, 510000),
            547888: _make_eps_by_ep(1, 11, 547000),
            633836: _make_eps_by_ep(1, 11, 1656858),
        }

    @classmethod
    def _rezero_api(cls):
        return build_api_with_chain(
            cls._rezero_subjects(),
            cls._rezero_episodes(),
            sequel_chain=[278826, 316247, 425998, 510728, 547888, 633836],
            prequel_chain=[],
        )

    def test_rezero_s04e13_resolves_to_season4_cour2(self):
        """显式传入 target_season=4 时，S04E13 定位到第四季第二 cour 的 ep2（id 1656859）。"""
        api = self._rezero_api()
        result = api.find_episode_across_seasons(140001, 13, target_season=4)
        assert result is not None
        assert result[0] == 633836
        assert result[1] == 1656859

    def test_rezero_s04e13_without_season_falls_back_to_global_sort(self):
        """未传入季编号（target_season 取默认值 1）时按全局 sort 解析，S04E13 命中 sort=13 的第一季 ep13（id 626044）。

        季定位仅在 target_season>1 时启用，故不传季编号的请求仍走全局 sort。
        """
        api = self._rezero_api()
        result = api.find_episode_across_seasons(140001, 13)
        assert result is not None
        assert result[0] == 140001
        assert result[1] == 626044

    def test_rezero_s04e01_resolves_to_season4_cour1(self):
        """S04E01 应定位到第四季第一 cour（547888）的 ep1。"""
        api = self._rezero_api()
        result = api.find_episode_across_seasons(140001, 1, target_season=4)
        assert result is not None
        assert result[0] == 547888

    def test_target_season_one_skips_season_resolver(self):
        """常规：target_season<=1 时不应进入季定位快路径，直接走全局 sort。"""
        api = self._rezero_api()
        original = api._resolve_by_season_chain
        api._resolve_by_season_chain = MagicMock(wraps=original)
        result = api.find_episode_across_seasons(140001, 13, target_season=1)
        api._resolve_by_season_chain.assert_not_called()
        assert result == (140001, 626044)

    def test_single_subject_season1_uses_global_sort_unchanged(self):
        """常规：单 subject、季=1 的请求按全局 sort 解析，命中 sort 对应的章节。"""
        subjects = {100: {"name": "某番剧", "name_cn": "某番剧", "type": 2}}
        episodes = {100: _make_eps_by_ep(1, 13, 10000, start_sort=1)}
        api = build_api_with_chain(
            subjects, episodes, sequel_chain=[], prequel_chain=[]
        )
        result = api.find_episode_across_seasons(100, 5)
        assert result == (100, 10004)

    def test_resolve_by_season_chain_returns_none_when_season_absent(self):
        """边缘：关联链上不存在目标季时既不命中，也不判定为跨季连续编号。"""
        subjects = {
            100: {"name": "某番剧", "name_cn": "某番剧", "type": 2},
            200: {"name": "某番剧 第二季", "name_cn": "某番剧 第二季", "type": 2},
            300: {"name": "某番剧 第三季", "name_cn": "某番剧 第三季", "type": 2},
        }
        episodes = {
            100: _make_eps_by_ep(1, 11, 10000),
            200: _make_eps_by_ep(1, 11, 20000),
            300: _make_eps_by_ep(1, 11, 30000),
        }
        api = build_api_with_chain(
            subjects, episodes, sequel_chain=[200, 300], prequel_chain=[]
        )
        # 关联链仅到第 3 季，要求第 4 季 → 目标季无法定位
        pick, beyond_season_total = api._resolve_by_season_chain(100, 4, 13, 20)
        assert pick is None
        assert beyond_season_total is False

    def test_season_unparseable_title_returns_none(self):
        """边缘：链上标题均无法解析季编号时目标季无法定位，不回退全局 sort。

        target_season>1 时集数是季内编号，回退全局 sort 会把它当作全局序号命中
        第一季的同号集（第 2 季第 13 集命中第 1 季第 13 集）。
        """
        subjects = {
            100: {"name": "某番剧", "name_cn": "某番剧", "type": 2},
            200: {"name": "某番剧 续章", "name_cn": "某番剧 续章", "type": 2},
        }
        episodes = {
            100: _make_eps_by_ep(1, 13, 10000, start_sort=1),
            200: _make_eps_by_ep(1, 13, 20000, start_sort=1),
        }
        api = build_api_with_chain(
            subjects, episodes, sequel_chain=[200], prequel_chain=[]
        )
        # 标题无季声明，第 2 季无法定位；不得退化为 sort=13 命中第一季
        result = api.find_episode_across_seasons(100, 13, target_season=2)
        assert result is None

    def test_season_located_without_ep_field_returns_none(self):
        """边缘：目标季已定位但章节缺 ep 字段时无法按季内编号解析，不回退全局 sort。

        Archive 的 episode 表只提供全局连续 sort，不含季内 ep；此时回退全局 sort
        会把季内编号当作全局序号命中第一季同号集。
        """
        subjects = {
            100: {"name": "某番剧", "name_cn": "某番剧", "type": 2},
            200: {"name": "某番剧 第二季", "name_cn": "某番剧 第二季", "type": 2},
        }
        # 第二季 sort 全局连续 14..25，无 ep 字段
        episodes = {
            100: _make_eps_by_sort(1, 13, 10000),
            200: _make_eps_by_sort(14, 12, 20000),
        }
        api = build_api_with_chain(
            subjects, episodes, sequel_chain=[200], prequel_chain=[]
        )
        result = api.find_episode_across_seasons(100, 6, target_season=2)
        assert result is None

    def test_target_ep_beyond_season_total_keeps_global_sort_fallback(self):
        """边缘：集数超出目标季总集数时按跨季连续编号处理，回退全局 sort 匹配。"""
        subjects = {
            100: {"name": "某番剧", "name_cn": "某番剧", "type": 2},
            200: {"name": "某番剧 第二季", "name_cn": "某番剧 第二季", "type": 2},
            300: {"name": "某番剧 第三季", "name_cn": "某番剧 第三季", "type": 2},
        }
        # sort 全局连续：第 1 季 1-10、第 2 季 11-20、第 3 季 21-30；ep 每季从 1 起
        episodes = {
            100: _make_eps_by_ep(1, 10, 10000, start_sort=1),
            200: _make_eps_by_ep(1, 10, 20000, start_sort=11),
            300: _make_eps_by_ep(1, 10, 30000, start_sort=21),
        }
        api = build_api_with_chain(
            subjects, episodes, sequel_chain=[200, 300], prequel_chain=[]
        )
        # 第 2 季共 10 集，25 超出该范围 → 视为连续编号，回退全局 sort=25
        result = api.find_episode_across_seasons(100, 25, target_season=2)
        assert result == (300, 30004)

    def test_cross_cour_same_season_cumulative(self):
        """边缘：同季多 cour 累计定位。S2 分两 cour（各 12 集），ep=20 应落在第二 cour ep8。"""
        subjects = {
            100: {"name": "某番剧", "name_cn": "某番剧", "type": 2},
            200: {"name": "某番剧 第二季", "name_cn": "某番剧 第二季", "type": 2},
            210: {
                "name": "某番剧 第二季 后半",
                "name_cn": "某番剧 第二季 后半",
                "type": 2,
            },
        }
        episodes = {
            100: _make_eps_by_ep(1, 12, 10000, start_sort=1),
            200: _make_eps_by_ep(1, 12, 20000, start_sort=1),
            210: _make_eps_by_ep(1, 12, 21000, start_sort=1),
        }
        api = build_api_with_chain(
            subjects, episodes, sequel_chain=[200, 210], prequel_chain=[]
        )
        result = api.find_episode_across_seasons(100, 20, target_season=2)
        assert result is not None
        assert result[0] == 210
        # 第二 cour 内 local_ep = 20 - 12 = 8
        assert result[1] == 21007

    def test_season_not_in_chain_overall_none(self):
        """边缘：目标季不在链内且全局 sort 也找不到时，整体返回 None（不崩溃）。"""
        subjects = {100: {"name": "某番剧", "name_cn": "某番剧", "type": 2}}
        episodes = {100: _make_eps_by_ep(1, 10, 10000, start_sort=1)}
        api = build_api_with_chain(
            subjects, episodes, sequel_chain=[], prequel_chain=[]
        )
        result = api.find_episode_across_seasons(100, 13, target_season=4)
        assert result is None


class TestFindEpisodeFranchiseFallback:
    """同 IP 改编兜底（find_episode_across_seasons 的 franchise 分支）

    场景：凡人修仙传动画（type=2）与真人网剧（type=6）同名，「改编」边
    不在续集/前传链上，链式遍历 miss 后应通过同 IP 闭包/一跳找到目标集。
    """

    @staticmethod
    def _make_eps(start_sort: int, count: int, start_id: int = 10000):
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
    def _make_api(episodes: dict, related: dict):
        """构造 mock BangumiApi：archive 全 miss，链式/主题/闭包短路均不可用

        try_find_franchise_closure 默认 miss，在线一跳用 get_related_subjects。
        """
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        api = BangumiApi()

        def get_episodes(sid, *args, **kwargs):
            return episodes.get(int(sid), {"data": [], "total": 0})

        def get_related(sid):
            return related.get(int(sid), [])

        def get_subject(sid):
            sid = int(sid)
            if sid == 900:
                # 凡人修仙传真人网剧（三次元 type=6）
                return {
                    "type": 6,
                    "name": "凡人修仙传",
                    "name_cn": "",
                    "platform": "WEB",
                }
            return {"type": 2, "name": f"S{sid}", "name_cn": "", "platform": "WEB"}

        api.get_episodes = MagicMock(side_effect=get_episodes)
        api.get_related_subjects = MagicMock(side_effect=get_related)
        api.get_subject = MagicMock(side_effect=get_subject)
        api._fetch_episodes_page = MagicMock(return_value={"data": [], "total": 0})

        api._archive = MagicMock()
        api._archive.enabled = True
        miss = ShortcutResult(False, None, "archive_miss")
        api._archive.try_find_sequel_chain = MagicMock(return_value=miss)
        api._archive.try_find_prequel_chain = MagicMock(return_value=miss)
        api._archive.try_find_franchise_closure = MagicMock(return_value=miss)
        api._archive.try_find_related_id_by_relation = MagicMock(return_value=miss)
        api._archive.try_get_subject = MagicMock(return_value=miss)
        api._archive.try_get_episodes = MagicMock(return_value=miss)
        api._archive.try_get_related_subjects = MagicMock(return_value=miss)
        return api

    def test_archive_franchise_closure_finds_live_action(self):
        """Archive 闭包命中：网剧改编条目（type=6，动画链外）内找到目标集"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            900: {"data": self._make_eps(101, 60, 90001), "total": 60},
        }
        # 链式边（续集/前传）为空；改编边只在 archive 闭包中（900 网剧）
        related = {100: [], 900: []}
        api = self._make_api(episodes, related)
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        api._archive.try_find_franchise_closure.return_value = ShortcutResult(
            True, [900], "archive_hit"
        )

        result = api.find_episode_across_seasons(100, 120)
        assert result is not None
        assert result[0] == 900
        assert result[1] == 90020  # sort 120 -> id 90001 + (120 - 101)
        # 命中路径标记：archive 闭包 → franchise_archive（前端徽章用）
        assert api.last_cross_season_path == "franchise_archive"

    def test_archive_franchise_closure_empty_falls_back_online(self):
        """Archive 闭包命中但为空时，降级到在线一跳仍能找到改编条目"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            900: {"data": self._make_eps(101, 60, 90001), "total": 60},
        }
        # 改编：100 的直接邻居 900（relation=改编，type=6 网剧）
        related = {100: [{"relation": "改编", "id": 900, "type": 6}], 900: []}
        api = self._make_api(episodes, related)
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        api._archive.try_find_franchise_closure.return_value = ShortcutResult(
            True, [], "archive_hit"
        )

        result = api.find_episode_across_seasons(100, 120)
        assert result is not None
        assert result[0] == 900
        assert result[1] == 90020
        assert api.last_cross_season_path == "franchise_online"

    def test_online_one_hop_finds_live_action(self):
        """Archive miss：在线一跳改编邻居（不 BFS）找到网剧目标集"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            900: {"data": self._make_eps(101, 60, 90001), "total": 60},
        }
        related = {100: [{"relation": "改编", "id": 900, "type": 6}], 900: []}
        api = self._make_api(episodes, related)

        result = api.find_episode_across_seasons(100, 120)
        assert result is not None
        assert result[0] == 900
        assert result[1] == 90020
        assert api.last_cross_season_path == "franchise_online"

    def test_online_one_hop_skips_book_type(self):
        """一跳改编邻居是书籍（type=1）时应跳过，返回 None"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
        }
        # 小说原作 type=1（非影视），不应命中
        related = {100: [{"relation": "改编", "id": 700, "type": 1}], 700: []}

        api = self._make_api(episodes, related)
        api.get_subject = MagicMock(
            return_value={
                "type": 1,
                "name": "凡人修仙传",
                "name_cn": "",
                "platform": "",
            }
        )
        # 700 无章节数据
        result = api.find_episode_across_seasons(100, 120)
        assert result is None

    def test_online_one_hop_skips_movie_name(self):
        """一跳改编邻居标题含「剧场版」时应跳过（与链式行为一致）"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            900: {"data": self._make_eps(101, 60, 90001), "total": 60},
        }
        related = {100: [{"relation": "改编", "id": 900, "type": 6}], 900: []}
        api = self._make_api(episodes, related)
        api.get_subject = MagicMock(
            return_value={
                "type": 6,
                "name": "凡人修仙传剧场版",
                "name_cn": "",
                "platform": "WEB",
            }
        )

        result = api.find_episode_across_seasons(100, 120)
        assert result is None

    def test_online_one_hop_miss_returns_none(self):
        """一跳改编邻居无目标 sort 时返回 None（行为与链式 miss 一致）"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            900: {"data": self._make_eps(101, 20, 90001), "total": 20},
        }
        related = {100: [{"relation": "改编", "id": 900, "type": 6}], 900: []}
        api = self._make_api(episodes, related)

        # sort 云投 500 远超所有条目范围
        result = api.find_episode_across_seasons(100, 500)
        assert result is None

    def test_online_one_hop_skips_unrelated_relation(self):
        """非同 IP 关系（如「不同世界观」）不应作为改编邻居命中"""
        episodes = {
            100: {"data": self._make_eps(1, 50, 10001), "total": 50},
            900: {"data": self._make_eps(101, 60, 90001), "total": 60},
        }
        # relation=不同世界观（不在 FRANCHISE_RELATION_CN_SET）
        related = {100: [{"relation": "不同世界观", "id": 900, "type": 6}], 900: []}
        api = self._make_api(episodes, related)

        result = api.find_episode_across_seasons(100, 120)
        assert result is None
