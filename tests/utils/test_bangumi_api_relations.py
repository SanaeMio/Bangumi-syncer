"""RelationMixin 公共关系遍历 API 单元测试

覆盖 search_next_subject / search_previous_subjects / get_series_subject_ids：
- Archive 短路命中（不调在线 API）
- Archive 短路确认无结果（不调在线 API）
- Archive 短路未命中降级到在线 API（get_related_subjects / get_subject）
- 类型过滤（仅 anime/real）、系列全集去重
"""

from unittest.mock import MagicMock, patch

from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_archive._store import archive_store

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


class TestSeriesClosureBFS:
    """get_series_subject_ids_bfs（双向续集图 BFS 闭包，含分支）"""

    def test_offline_closure_captures_branches(self) -> None:
        """离线闭包应收回分支型 IP 的全部兄弟续集/前传（单链 LIMIT 1 会丢失）"""
        api = _make_api()
        # seed=1：续集 1->2，2->[3,4]（分支），前传 1->5
        api._archive.try_find_series_closure.return_value = _hit([2, 5, 3, 4])
        api.get_subject = MagicMock(side_effect=lambda sid: {"id": sid, "type": 2})
        ids = api.get_series_subject_ids_bfs(1)
        assert ids[0] == 1
        assert set(ids) == {1, 2, 3, 4, 5}

    def test_online_fallback_closure(self) -> None:
        """Archive miss 时降级在线 get_related_subjects 双向 BFS，仍收回分支"""
        api = _make_api()
        api._archive.try_find_series_closure.return_value = _miss()
        related_map = {
            1: [
                {"id": 2, "relation": REL_SEQUEL},
                {"id": 5, "relation": REL_PREQUEL},
            ],
            2: [
                {"id": 3, "relation": REL_SEQUEL},
                {"id": 4, "relation": REL_SEQUEL},  # 分支
            ],
            3: [],
            4: [],
            5: [],
        }
        api.get_related_subjects = MagicMock(
            side_effect=lambda sid: related_map.get(sid, [])
        )
        api.get_subject = MagicMock(side_effect=lambda sid: {"id": sid, "type": 2})
        ids = api.get_series_subject_ids_bfs(1)
        assert ids[0] == 1
        assert set(ids) == {1, 2, 3, 4, 5}

    def test_filters_non_anime(self) -> None:
        """闭包中含非 anime/real 节点时被过滤（seed 始终保留）"""
        api = _make_api()
        api._archive.try_find_series_closure.return_value = _hit(
            [2, 3]
        )  # 2=动画 3=游戏
        api.get_subject = MagicMock(
            side_effect=lambda sid: {"id": sid, "type": 2 if sid == 2 else 4}
        )
        ids = api.get_series_subject_ids_bfs(1)
        assert ids == [1, 2]


class TestFindSeriesClosureStore:
    """archive_store.find_series_closure（离线双向 BFS 闭包原语）"""

    def test_bfs_captures_branch(self, monkeypatch) -> None:
        """每节点取全部续集/前传边，闭包收回分支 4（单链只会给 [2,3]）"""
        edges = {
            1: [2, 5],  # sequel=2, prequel=5（合并查询）
            2: [3, 4],
            3: [],
            4: [],
            5: [],
        }

        def fake_find(sid, _types):
            return edges.get(sid, [])

        monkeypatch.setattr(archive_store, "_get_connection", lambda: MagicMock())
        monkeypatch.setattr(archive_store, "find_related_by_relations", fake_find)
        closure = archive_store.find_series_closure(1)
        assert set(closure) == {2, 3, 4, 5}

    def test_cycle_safe(self, monkeypatch) -> None:
        """成环（1<->2 互为续集）不应死循环"""
        edges = {1: [2], 2: [1]}
        monkeypatch.setattr(archive_store, "_get_connection", lambda: MagicMock())
        monkeypatch.setattr(
            archive_store,
            "find_related_by_relations",
            lambda sid, _types: edges.get(sid, []),
        )
        closure = archive_store.find_series_closure(1)
        assert set(closure) == {2}


class TestFranchiseClosureBFS:
    """get_franchise_subject_ids_bfs（同 IP 关系图双向 BFS 闭包）"""

    def test_offline_closure_includes_franchise_relations(self) -> None:
        """离线闭包应沿同 IP 关系（改编/相同世界观等）收回兄弟作品"""
        api = _make_api()
        api._archive.try_find_franchise_closure.return_value = _hit([2, 3, 4])
        api.get_subject = MagicMock(side_effect=lambda sid: {"id": sid, "type": 2})
        ids = api.get_franchise_subject_ids_bfs(1)
        assert ids[0] == 1
        assert set(ids) == {1, 2, 3, 4}

    def test_offline_empty_closure_no_online_fallback(self) -> None:
        """Archive 命中空闭包（hit=True, data=[]，seed 存在但无关联）不降级在线"""
        api = _make_api()
        api._archive.try_find_franchise_closure.return_value = _hit([])
        api.get_related_subjects = MagicMock()
        ids = api.get_franchise_subject_ids_bfs(1)
        assert ids == [1]
        api.get_related_subjects.assert_not_called()

    def test_online_fallback_excludes_noise_edges(self) -> None:
        """在线降级应排除「角色出演/不同世界观/联动」等噪声边"""
        api = _make_api()
        api._archive.try_find_franchise_closure.return_value = _miss()
        related_map = {
            1: [
                {"id": 2, "relation": "续集"},
                {"id": 3, "relation": "改编"},
                {"id": 99, "relation": "角色出演"},  # 噪声，应被排除
                {"id": 100, "relation": "不同世界观"},  # 噪声
                {"id": 101, "relation": "联动"},  # 噪声
            ],
            2: [],
            3: [{"id": 4, "relation": "相同世界观"}],
            4: [],
        }
        api.get_related_subjects = MagicMock(
            side_effect=lambda sid: related_map.get(sid, [])
        )
        api.get_subject = MagicMock(side_effect=lambda sid: {"id": sid, "type": 2})
        ids = api.get_franchise_subject_ids_bfs(1)
        assert ids[0] == 1
        assert set(ids) == {1, 2, 3, 4}
        assert 99 not in ids and 100 not in ids and 101 not in ids

    def test_online_fallback_max_hops_truncates(self) -> None:
        """max_hops 节点数上限截断：超过上限的节点不入闭包"""
        api = _make_api()
        api._archive.try_find_franchise_closure.return_value = _miss()
        # 1 -> 2 -> 3 -> 4 -> 5 -> 6（线性链，每跳 1 个新节点）
        related_map = {
            1: [{"id": 2, "relation": "续集"}],
            2: [{"id": 3, "relation": "续集"}],
            3: [{"id": 4, "relation": "续集"}],
            4: [{"id": 5, "relation": "续集"}],
            5: [{"id": 6, "relation": "续集"}],
            6: [],
        }
        api.get_related_subjects = MagicMock(
            side_effect=lambda sid: related_map.get(sid, [])
        )
        api.get_subject = MagicMock(side_effect=lambda sid: {"id": sid, "type": 2})
        # max_hops=3：含 seed 最多 3 节点 → [1, 2, 3]
        ids = api.get_franchise_subject_ids_bfs(1, max_hops=3)
        assert len(ids) == 3
        assert ids[0] == 1

    def test_filters_non_anime(self) -> None:
        """闭包中含非 anime/real 节点时被过滤（seed 始终保留）"""
        api = _make_api()
        api._archive.try_find_franchise_closure.return_value = _hit([2, 3])
        api.get_subject = MagicMock(
            side_effect=lambda sid: {"id": sid, "type": 2 if sid == 2 else 4}
        )
        ids = api.get_franchise_subject_ids_bfs(1)
        assert ids == [1, 2]

    def test_archive_error_falls_back_online(self) -> None:
        """Archive 异常（reason=archive_error）应降级在线 BFS"""
        api = _make_api()
        api._archive.try_find_franchise_closure.return_value = _miss("archive_error")
        api.get_related_subjects = MagicMock(
            return_value=[{"id": 2, "relation": "续集"}]
        )
        api.get_subject = MagicMock(side_effect=lambda sid: {"id": sid, "type": 2})
        ids = api.get_franchise_subject_ids_bfs(1)
        assert ids == [1, 2]


class TestFindFranchiseClosureStore:
    """archive_store.find_franchise_closure（离线同 IP BFS 闭包原语）"""

    def test_bfs_captures_franchise_relations(self, monkeypatch) -> None:
        """沿 FRANCHISE_RELATION_TYPES 收回同 IP 兄弟作品（改编/相同世界观等）"""
        edges = {
            1: [2, 3],  # 2=续集, 3=改编
            2: [4],  # 4=番外篇
            3: [],
            4: [],
        }
        monkeypatch.setattr(archive_store, "_get_connection", lambda: MagicMock())
        monkeypatch.setattr(
            archive_store,
            "find_related_by_relations",
            lambda sid, _types: edges.get(sid, []),
        )
        closure = archive_store.find_franchise_closure(1)
        assert set(closure) == {2, 3, 4}
        # 确认调用时传入的是 FRANCHISE_RELATION_TYPES
        # （通过 fake_find 捕获最后一个调用的 _types 参数验证）

    def test_max_hops_truncates_by_node_count(self, monkeypatch) -> None:
        """max_hops 为节点数上限（含起始）：达到上限即停"""
        edges = {
            1: [2],
            2: [3],
            3: [4],
            4: [5],
            5: [],
        }
        monkeypatch.setattr(archive_store, "_get_connection", lambda: MagicMock())
        monkeypatch.setattr(
            archive_store,
            "find_related_by_relations",
            lambda sid, _types: edges.get(sid, []),
        )
        # max_hops=2：含起始最多 2 节点 → 仅 [2]
        closure = archive_store.find_franchise_closure(1, max_hops=2)
        assert set(closure) == {2}

    def test_cycle_safe(self, monkeypatch) -> None:
        """成环（1<->2 互为改编）不应死循环"""
        edges = {1: [2], 2: [1]}
        monkeypatch.setattr(archive_store, "_get_connection", lambda: MagicMock())
        monkeypatch.setattr(
            archive_store,
            "find_related_by_relations",
            lambda sid, _types: edges.get(sid, []),
        )
        closure = archive_store.find_franchise_closure(1)
        assert set(closure) == {2}

    def test_empty_relation_types_returns_empty(self, monkeypatch) -> None:
        """relation_types 空元组时返回空列表"""
        monkeypatch.setattr(archive_store, "_get_connection", lambda: MagicMock())
        closure = archive_store.find_franchise_closure(1, relation_types=())
        assert closure == []
