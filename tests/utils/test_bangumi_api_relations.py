"""RelationMixin 公共关系遍历 API 单元测试

覆盖 search_next_subject / search_previous_subjects / get_series_subject_ids：
- Archive 短路命中（不调在线 API）
- Archive 短路确认无结果（不调在线 API）
- Archive 短路未命中降级到在线 API（get_related_subjects / get_subject）
- 类型过滤（仅 anime/real）、系列全集去重
"""

from unittest.mock import MagicMock, patch

from app.utils.bangumi_api import BangumiApi

REL_SEQUEL = "续集"
REL_PREQUEL = "前传"


def _make_api() -> BangumiApi:
    """构造 BangumiApi 实例（mock httpx.Client，避免真实网络）并替换为 mock 短路"""
    with patch("app.utils.bangumi_api.httpx.Client"):
        api = BangumiApi()
    api._archive = MagicMock()
    return api


def _hit(data, reason: str = "archive_hit") -> MagicMock:
    m = MagicMock()
    m.hit = True
    m.data = data
    m.reason = reason
    return m


def _miss(reason: str = "archive_miss") -> MagicMock:
    m = MagicMock()
    m.hit = False
    m.data = None
    m.reason = reason
    return m


class TestSearchNextSubject:
    """search_next_subject"""

    def test_archive_hit_returns_id(self) -> None:
        """Archive 命中续集时直接返回 id，不调用在线 get_related_subjects"""
        api = _make_api()
        api._archive.try_find_next_sequel_id.return_value = _hit(42)
        api.get_related_subjects = MagicMock()
        assert api.search_next_subject(1) == 42
        api.get_related_subjects.assert_not_called()

    def test_archive_hit_none(self) -> None:
        """Archive 确认无续集时返回 None，不调用在线 API"""
        api = _make_api()
        api._archive.try_find_next_sequel_id.return_value = _hit(None)
        api.get_related_subjects = MagicMock()
        assert api.search_next_subject(1) is None
        api.get_related_subjects.assert_not_called()

    def test_archive_miss_falls_back_and_finds_sequel(self) -> None:
        """Archive 未命中时降级 get_related_subjects，命中 Sequel 返回 id"""
        api = _make_api()
        api._archive.try_find_next_sequel_id.return_value = _miss()
        api.get_related_subjects = MagicMock(
            return_value=[
                {"id": 7, "relation": "番外篇"},
                {"id": 42, "relation": REL_SEQUEL},
            ]
        )
        api.get_subject = MagicMock(return_value={"id": 42, "type": 2})
        assert api.search_next_subject(1) == 42
        api.get_related_subjects.assert_called_once_with(1)

    def test_archive_miss_skips_non_anime_sequel(self) -> None:
        """降级命中续集但类型非 anime/real（如游戏）时返回 None"""
        api = _make_api()
        api._archive.try_find_next_sequel_id.return_value = _miss()
        api.get_related_subjects = MagicMock(
            return_value=[{"id": 42, "relation": REL_SEQUEL}]
        )
        api.get_subject = MagicMock(return_value={"id": 42, "type": 4})  # 游戏
        assert api.search_next_subject(1) is None

    def test_archive_miss_no_sequel(self) -> None:
        """降级后无任何 Sequel 关联时返回 None"""
        api = _make_api()
        api._archive.try_find_next_sequel_id.return_value = _miss()
        api.get_related_subjects = MagicMock(return_value=[])
        assert api.search_next_subject(1) is None


class TestSearchPreviousSubjects:
    """search_previous_subjects（对应对手 SearchPreviousSubjects）"""

    def test_archive_hit_returns_chain(self) -> None:
        """Archive 命中前传链时直接返回，不降级 _walk_prequel_flat"""
        api = _make_api()
        api._archive.try_find_prequel_chain.return_value = _hit([10, 5])
        api._walk_prequel_flat = MagicMock()
        assert api.search_previous_subjects(1) == [10, 5]
        api._walk_prequel_flat.assert_not_called()

    def test_archive_hit_empty(self) -> None:
        api = _make_api()
        api._archive.try_find_prequel_chain.return_value = _hit([])
        assert api.search_previous_subjects(1) == []

    def test_archive_miss_falls_back_to_walk(self) -> None:
        """Archive 未命中时降级 _walk_prequel_flat"""
        api = _make_api()
        api._archive.try_find_prequel_chain.return_value = _miss()
        api._walk_prequel_flat = MagicMock(return_value=[10, 5])
        assert api.search_previous_subjects(1) == [10, 5]
        api._walk_prequel_flat.assert_called_once()


class TestWalkPrequelFlat:
    """_walk_prequel_flat 逐跳前传回溯"""

    def test_two_hops(self) -> None:
        api = _make_api()
        # 1 -> 10 -> 5
        api._prequel_one_hop = MagicMock(
            side_effect=lambda sid: {1: 10, 10: 5}.get(sid)
        )
        assert api._walk_prequel_flat(1, max_hops=10) == [10, 5]

    def test_cycle_safe(self) -> None:
        """前传链成环时不应死循环"""
        api = _make_api()
        api._prequel_one_hop = MagicMock(
            side_effect=lambda sid: {1: 10, 10: 1}.get(sid)
        )
        assert api._walk_prequel_flat(1, max_hops=10) == [10]


class TestGetSeriesSubjectIds:
    """get_series_subject_ids（对应对手 GetAllAnimeSeriesSubjectIds）"""

    def test_collect_union_dedup_filter_type(self) -> None:
        """收集续集链 ∪ 前传链，去重，仅保留 anime/real"""
        api = _make_api()
        api.search_next_subject = MagicMock(side_effect=[2, 3, None])  # 续集: 1->2->3
        api._prequel_one_hop = MagicMock(
            side_effect=lambda sid: 100 if sid == 1 else None
        )  # 前传: 1->100

        def fake_subject(sid):
            return {"id": sid, "type": 2 if sid in (1, 2, 3, 100) else 4}

        api.get_subject = MagicMock(side_effect=fake_subject)

        ids = api.get_series_subject_ids(1)
        assert ids[0] == 1  # 起始季优先
        assert set(ids) == {1, 2, 3, 100}

    def test_excludes_non_anime(self) -> None:
        """起始 subject 非 anime/real 时不加入任何结果"""
        api = _make_api()
        api.search_next_subject = MagicMock(return_value=None)
        api._prequel_one_hop = MagicMock(return_value=None)
        api.get_subject = MagicMock(return_value={"id": 1, "type": 4})  # 游戏
        assert api.get_series_subject_ids(1) == []

    def test_all_via_archive_shortcut(self) -> None:
        """续集方向全程走 Archive 短路，不回退在线 get_related_subjects"""
        api = _make_api()
        api._archive.try_find_next_sequel_id.side_effect = [
            _hit(2),
            _hit(3),
            _hit(None),
        ]
        api._prequel_one_hop = MagicMock(side_effect=[100, None])
        api.get_subject = MagicMock(return_value={"id": 0, "type": 2})
        ids = api.get_series_subject_ids(1)
        assert set(ids) == {1, 2, 3, 100}
        api._archive.try_find_next_sequel_id.assert_called()
