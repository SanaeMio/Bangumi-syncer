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

    def test_skips_airdate_path_when_season_subject_id_true(self):
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
