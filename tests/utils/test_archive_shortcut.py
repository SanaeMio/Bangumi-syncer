"""Archive 短路协调器单元测试

覆盖：
- ArchiveShortcut 各 try_* 方法的命中/未命中/禁用/异常分支
- BangumiApi 读方法接入短路后的上下位替代行为
  - 禁用时等价于原行为（API 被调用）
  - 命中时直接返回 Archive 数据（API 不被调用）
  - 未命中时降级到 API
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api._archive_shortcut import (
    ArchiveShortcut,
    ShortcutResult,
    archive_shortcut,
)

# ===== ArchiveShortcut 单元测试 =====


class TestArchiveShortcutDisabled:
    """Archive 未启用时所有短路方法应返回 archive_disabled"""

    def setup_method(self) -> None:
        self.shortcut = ArchiveShortcut()
        self.shortcut._enabled = False

    def test_try_get_subject_disabled(self) -> None:
        r = self.shortcut.try_get_subject(1)
        assert r.hit is False
        assert r.data is None
        assert r.reason == "archive_disabled"

    def test_try_get_episodes_disabled(self) -> None:
        r = self.shortcut.try_get_episodes(1)
        assert r.hit is False
        assert r.reason == "archive_disabled"

    def test_try_get_related_subjects_disabled(self) -> None:
        r = self.shortcut.try_get_related_subjects(1)
        assert r.hit is False
        assert r.reason == "archive_disabled"

    def test_try_find_next_sequel_id_disabled(self) -> None:
        r = self.shortcut.try_find_next_sequel_id(1)
        assert r.hit is False
        assert r.reason == "archive_disabled"

    def test_try_find_related_id_by_relation_disabled(self) -> None:
        r = self.shortcut.try_find_related_id_by_relation(1, "续集")
        assert r.hit is False
        assert r.reason == "archive_disabled"


class TestArchiveShortcutHit:
    """Archive 启用且命中时的行为"""

    def setup_method(self) -> None:
        self.shortcut = ArchiveShortcut()
        self.shortcut._enabled = True

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_subject_hit(self, mock_store: MagicMock) -> None:
        mock_store.get_subject.return_value = {"id": 1, "name": "Test"}
        r = self.shortcut.try_get_subject(1)
        assert r.hit is True
        assert r.data == {"id": 1, "name": "Test"}
        assert r.reason == "archive_hit"
        mock_store.get_subject.assert_called_once_with(1)

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_subject_miss(self, mock_store: MagicMock) -> None:
        mock_store.get_subject.return_value = None
        r = self.shortcut.try_get_subject(999)
        assert r.hit is False
        assert r.reason == "archive_miss"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_episodes_hit_empty_list(self, mock_store: MagicMock) -> None:
        """Archive 命中但章节为空列表时仍算命中（避免 API 重复调用）"""
        mock_store.get_episodes.return_value = []
        r = self.shortcut.try_get_episodes(1)
        assert r.hit is True
        assert r.data == []
        assert r.reason == "archive_hit"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_episodes_hit_with_data(self, mock_store: MagicMock) -> None:
        mock_store.get_episodes.return_value = [{"id": 101, "sort": 1, "type": 0}]
        r = self.shortcut.try_get_episodes(1, episode_type=0)
        assert r.hit is True
        assert len(r.data) == 1
        mock_store.get_episodes.assert_called_once_with(1, episode_type=0)

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_related_subjects_hit(self, mock_store: MagicMock) -> None:
        mock_store.get_related_subjects.return_value = [
            {"id": 2, "relation": "续集", "type": 3}
        ]
        r = self.shortcut.try_get_related_subjects(1)
        assert r.hit is True
        assert r.data[0]["id"] == 2

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_next_sequel_id_hit(self, mock_store: MagicMock) -> None:
        mock_store.find_related_by_relation.return_value = [42]
        r = self.shortcut.try_find_next_sequel_id(1)
        assert r.hit is True
        assert r.data == 42
        # 命中后不应再查 subject（短路提前返回）
        mock_store.get_subject.assert_not_called()

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_next_sequel_id_no_sequel(self, mock_store: MagicMock) -> None:
        """subject 存在但无续集关联时返回 hit=True, data=None"""
        mock_store.find_related_by_relation.return_value = []
        mock_store.get_subject.return_value = {"id": 1, "name": "Test"}
        r = self.shortcut.try_find_next_sequel_id(1)
        assert r.hit is True
        assert r.data is None

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_next_sequel_id_miss(self, mock_store: MagicMock) -> None:
        """subject 不在 Archive 中时返回 miss（降级到 API）"""
        mock_store.find_related_by_relation.return_value = []
        mock_store.get_subject.return_value = None
        r = self.shortcut.try_find_next_sequel_id(999)
        assert r.hit is False
        assert r.reason == "archive_miss"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_related_id_by_relation_hit(self, mock_store: MagicMock) -> None:
        mock_store.find_related_by_relation.return_value = [42]
        r = self.shortcut.try_find_related_id_by_relation(1, "前传")
        assert r.hit is True
        assert r.data == 42

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_related_id_by_relation_unknown_relation(
        self, mock_store: MagicMock
    ) -> None:
        """未知关联中文名时降级到 API"""
        r = self.shortcut.try_find_related_id_by_relation(1, "不存在的关联")
        assert r.hit is False
        assert r.reason == "archive_miss"
        mock_store.find_related_by_relation.assert_not_called()


class TestArchiveShortcutError:
    """Archive 查询异常时应降级到 API"""

    def setup_method(self) -> None:
        self.shortcut = ArchiveShortcut()
        self.shortcut._enabled = True

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_subject_error(self, mock_store: MagicMock) -> None:
        mock_store.get_subject.side_effect = RuntimeError("db locked")
        r = self.shortcut.try_get_subject(1)
        assert r.hit is False
        assert r.reason == "archive_error"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_episodes_error(self, mock_store: MagicMock) -> None:
        mock_store.get_episodes.side_effect = RuntimeError("db locked")
        r = self.shortcut.try_get_episodes(1)
        assert r.hit is False
        assert r.reason == "archive_error"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_next_sequel_id_error(self, mock_store: MagicMock) -> None:
        mock_store.find_related_by_relation.side_effect = RuntimeError("db locked")
        r = self.shortcut.try_find_next_sequel_id(1)
        assert r.hit is False
        assert r.reason == "archive_error"


class TestArchiveShortcutReloadConfig:
    """reload_config 从 config_manager 重新加载 enabled 状态"""

    @patch("app.utils.bangumi_api._archive_shortcut.config_manager")
    def test_reload_enables(self, mock_cfg: MagicMock) -> None:
        # 构造时返回 False（未启用）
        mock_cfg.get.return_value = False
        shortcut = ArchiveShortcut()
        assert shortcut._enabled is False
        # 配置变更后 reload_config 应重新加载为 True
        mock_cfg.get.return_value = True
        shortcut.reload_config()
        assert shortcut._enabled is True

    @patch("app.utils.bangumi_api._archive_shortcut.config_manager")
    def test_reload_disables(self, mock_cfg: MagicMock) -> None:
        mock_cfg.get.return_value = True
        shortcut = ArchiveShortcut()
        shortcut.reload_config()
        assert shortcut._enabled is True
        # 修改配置返回值
        mock_cfg.get.return_value = False
        shortcut.reload_config()
        assert shortcut._enabled is False


# ===== BangumiApi 接入短路集成测试 =====


def _make_api_with_mocked_http():
    """构造 BangumiApi 实例（mock httpx.Client，避免真实网络）"""
    with patch("app.utils.bangumi_api.httpx.Client"):
        return BangumiApi()


def _mock_archive_hit(data):
    """构造一个 hit=True 的 ShortcutResult mock"""
    m = MagicMock()
    m.hit = True
    m.data = data
    m.reason = "archive_hit"
    return m


def _mock_archive_miss(reason="archive_miss"):
    """构造一个 hit=False 的 ShortcutResult mock"""
    m = MagicMock()
    m.hit = False
    m.data = None
    m.reason = reason
    return m


class TestBangumiApiArchiveIntegration:
    """BangumiApi 读方法接入 Archive 短路后的行为"""

    def test_api_has_archive_attribute(self) -> None:
        api = _make_api_with_mocked_http()
        assert hasattr(api, "_archive")
        assert api._archive is archive_shortcut

    # ---- get_subject ----

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_subject_archive_hit_skips_api(self, _mock_http: MagicMock) -> None:
        """Archive 命中时不应调用 API"""
        api = BangumiApi()
        archive_data = {"id": 1, "name": "Archive Hit"}
        api._archive = MagicMock()
        api._archive.try_get_subject.return_value = _mock_archive_hit(archive_data)

        # mock self.get 验证未被调用
        api.get = MagicMock()

        result = api.get_subject(1)

        assert result == archive_data
        api._archive.try_get_subject.assert_called_once_with(1)
        api.get.assert_not_called()
        # 命中应写入缓存
        assert api._cache["get_subject"][1] == archive_data

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_subject_archive_miss_falls_back_to_api(
        self, _mock_http: MagicMock
    ) -> None:
        """Archive 未命中时应降级到 API"""
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_get_subject.return_value = _mock_archive_miss()

        api_data = {"id": 1, "name": "From API"}
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_data
        api.get = MagicMock(return_value=mock_resp)

        result = api.get_subject(1)

        assert result == api_data
        api.get.assert_called_once_with("subjects/1")

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_subject_cache_hit_skips_archive_and_api(
        self, _mock_http: MagicMock
    ) -> None:
        """缓存命中时应跳过 Archive 和 API（缓存优先）"""
        api = BangumiApi()
        cached = {"id": 1, "name": "Cached"}
        api._put_cache("get_subject", 1, cached)

        api._archive = MagicMock()
        api.get = MagicMock()

        result = api.get_subject(1)

        assert result == cached
        api._archive.try_get_subject.assert_not_called()
        api.get.assert_not_called()

    # ---- get_related_subjects ----

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_related_subjects_archive_hit(self, _mock_http: MagicMock) -> None:
        api = BangumiApi()
        archive_data = [{"id": 2, "relation": "续集"}]
        api._archive = MagicMock()
        api._archive.try_get_related_subjects.return_value = _mock_archive_hit(
            archive_data
        )
        api.get = MagicMock()

        result = api.get_related_subjects(1)

        assert result == archive_data
        api.get.assert_not_called()

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_related_subjects_archive_miss(self, _mock_http: MagicMock) -> None:
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_get_related_subjects.return_value = _mock_archive_miss()

        api_data = [{"id": 2, "relation": "续集"}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_data
        api.get = MagicMock(return_value=mock_resp)

        result = api.get_related_subjects(1)

        assert result == api_data
        api.get.assert_called_once_with("subjects/1/subjects")

    # ---- get_episodes ----

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_episodes_archive_hit_wraps_to_dict(
        self, _mock_http: MagicMock
    ) -> None:
        """Archive 命中时 list 应包装为 {data, total} 与 API 返回结构对齐"""
        api = BangumiApi()
        archive_data = [{"id": 101, "sort": 1}, {"id": 102, "sort": 2}]
        api._archive = MagicMock()
        api._archive.try_get_episodes.return_value = _mock_archive_hit(archive_data)
        api._fetch_episodes_page = MagicMock()

        result = api.get_episodes(1, _type=0, fetch_all=False)

        assert result == {"data": archive_data, "total": 2}
        # 命中后不应再调 API 分页
        api._fetch_episodes_page.assert_not_called()
        api._archive.try_get_episodes.assert_called_once_with(1, episode_type=0)

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_episodes_archive_hit_empty_list(self, _mock_http: MagicMock) -> None:
        """Archive 命中但章节为空时返回空 data（不调 API）"""
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_get_episodes.return_value = _mock_archive_hit([])
        api._fetch_episodes_page = MagicMock()

        result = api.get_episodes(1)

        assert result == {"data": [], "total": 0}
        api._fetch_episodes_page.assert_not_called()

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_episodes_archive_miss_falls_back_to_api(
        self, _mock_http: MagicMock
    ) -> None:
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_get_episodes.return_value = _mock_archive_miss()

        api_page = {"data": [{"id": 101}], "total": 1}
        api._fetch_episodes_page = MagicMock(return_value=api_page)

        result = api.get_episodes(1, fetch_all=False)

        assert result == api_page
        api._fetch_episodes_page.assert_called_once()

    # ---- _find_next_sequel_id ----

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_find_next_sequel_id_archive_hit_int(self, _mock_http: MagicMock) -> None:
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_find_next_sequel_id.return_value = _mock_archive_hit(42)
        api.get_related_subjects = MagicMock()

        assert api._find_next_sequel_id(1) == 42
        api.get_related_subjects.assert_not_called()

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_find_next_sequel_id_archive_hit_none(self, _mock_http: MagicMock) -> None:
        """Archive 确认无续集时返回 None，不调 API"""
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_find_next_sequel_id.return_value = _mock_archive_hit(None)
        api.get_related_subjects = MagicMock()

        assert api._find_next_sequel_id(1) is None
        api.get_related_subjects.assert_not_called()

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_find_next_sequel_id_archive_miss(self, _mock_http: MagicMock) -> None:
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_find_next_sequel_id.return_value = _mock_archive_miss()
        api.get_related_subjects = MagicMock(return_value=[])

        assert api._find_next_sequel_id(1) is None
        api.get_related_subjects.assert_called_once_with(1)

    # ---- _find_related_id_by_relation ----

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_find_related_id_by_relation_archive_hit(
        self, _mock_http: MagicMock
    ) -> None:
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_find_related_id_by_relation.return_value = _mock_archive_hit(
            42
        )
        api.get_related_subjects = MagicMock()

        assert api._find_related_id_by_relation(1, "前传") == 42
        api.get_related_subjects.assert_not_called()
        api._archive.try_find_related_id_by_relation.assert_called_once_with(1, "前传")

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_find_related_id_by_relation_archive_miss(
        self, _mock_http: MagicMock
    ) -> None:
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_find_related_id_by_relation.return_value = _mock_archive_miss()
        api.get_related_subjects = MagicMock(return_value=[])

        assert api._find_related_id_by_relation(1, "前传") is None
        api.get_related_subjects.assert_called_once_with(1)


# ---- try_search ----


class TestArchiveShortcutTrySearch:
    """ArchiveShortcut.try_search 行为测试"""

    def setup_method(self) -> None:
        self.shortcut = ArchiveShortcut()
        self.shortcut._enabled = True

    def test_disabled_returns_archive_disabled(self) -> None:
        """禁用时返回 archive_disabled"""
        self.shortcut._enabled = False
        r = self.shortcut.try_search("Test")
        assert r.hit is False
        assert r.reason == "archive_disabled"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_exact_match_hit(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """精确匹配命中时返回完整 subject 列表"""
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subject.return_value = {
            "id": 1,
            "type": 2,
            "name": "Test",
            "date": "2026-01-01",
        }

        r = self.shortcut.try_search("Test")

        assert r.hit is True
        assert r.reason == "archive_hit"
        assert len(r.data) == 1
        assert r.data[0]["id"] == 1
        # 精确命中后不应再调模糊匹配
        mock_index.find_subject_ids_fuzzy.assert_not_called()

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_fuzzy_match_hit(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """精确未命中时降级模糊匹配"""
        mock_index.find_subject_ids_by_title.return_value = []
        mock_index.find_subject_ids_fuzzy.return_value = [(1, 90)]
        mock_store.get_subject.return_value = {
            "id": 1,
            "type": 2,
            "name": "Test",
            "date": "2026-01-01",
        }

        r = self.shortcut.try_search("Tset")

        assert r.hit is True
        assert r.data[0]["id"] == 1

    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_no_match_returns_miss(self, mock_index: MagicMock) -> None:
        """精确和模糊都未命中时返回 miss"""
        mock_index.find_subject_ids_by_title.return_value = []
        mock_index.find_subject_ids_fuzzy.return_value = []

        r = self.shortcut.try_search("Nonexistent")

        assert r.hit is False
        assert r.reason == "archive_miss"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_subject_type_filter_excludes(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """subject_types 过滤排除 type 不匹配的条目"""
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subject.return_value = {
            "id": 1,
            "type": 3,  # 不是 anime=2
            "name": "Test",
            "date": "2026-01-01",
        }

        r = self.shortcut.try_search("Test", subject_types=[2])

        assert r.hit is False
        assert r.reason == "archive_miss"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_date_filter_excludes(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """air_date 过滤排除日期区间外的条目"""
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subject.return_value = {
            "id": 1,
            "type": 2,
            "name": "Test",
            "date": "2026-01-15",
        }

        # 区间为 [2026-02-01, 2026-03-01)，subject.date=2026-01-15 在区间外
        r = self.shortcut.try_search(
            "Test", start_date="2026-02-01", end_date="2026-03-01"
        )

        assert r.hit is False
        assert r.reason == "archive_miss"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_date_filter_includes(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """air_date 区间内的条目应命中"""
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subject.return_value = {
            "id": 1,
            "type": 2,
            "name": "Test",
            "date": "2026-01-15",
        }

        r = self.shortcut.try_search(
            "Test", start_date="2026-01-01", end_date="2026-02-01"
        )

        assert r.hit is True
        assert r.data[0]["id"] == 1

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_date_missing_not_filtered(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """subject.date 缺失时不应被 air_date 过滤（避免误删无日期条目）"""
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subject.return_value = {
            "id": 1,
            "type": 2,
            "name": "Test",
            "date": "",  # 空日期
        }

        r = self.shortcut.try_search(
            "Test", start_date="2026-01-01", end_date="2026-02-01"
        )

        assert r.hit is True

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_limit_respected(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """limit 参数应限制返回数量"""
        mock_index.find_subject_ids_by_title.return_value = [1, 2, 3]
        mock_index.find_subject_ids_fuzzy.return_value = []

        def fake_get_subject(sid):
            return {
                "id": sid,
                "type": 2,
                "name": f"Test{sid}",
                "date": "2026-01-01",
            }

        mock_store.get_subject.side_effect = fake_get_subject

        r = self.shortcut.try_search("Test", limit=2)

        assert r.hit is True
        assert len(r.data) == 2

    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_exception_returns_archive_error(self, mock_index: MagicMock) -> None:
        """查询异常时返回 archive_error"""
        mock_index.find_subject_ids_by_title.side_effect = RuntimeError("db locked")

        r = self.shortcut.try_search("Test")

        assert r.hit is False
        assert r.reason == "archive_error"


# ---- BangumiApi.search() Archive 短路集成 ----


class TestBangumiApiSearchArchiveIntegration:
    """BangumiApi.search() 接入 Archive 短路后的行为"""

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_search_archive_hit_skips_api(self, _mock_http: MagicMock) -> None:
        """Archive 命中时不应调用 API"""
        api = BangumiApi()
        archive_data = [{"id": 1, "name": "Test", "type": 2}]
        api._archive = MagicMock()
        api._archive.try_search.return_value = _mock_archive_hit(archive_data)
        api._request_with_retry = MagicMock()

        result = api.search(
            title="Test", start_date="2026-01-01", end_date="2026-02-01"
        )

        assert result == archive_data
        api._archive.try_search.assert_called_once()
        api._request_with_retry.assert_not_called()
        # 命中应写入缓存
        cache_key = (
            "Test",
            "2026-01-01",
            "2026-02-01",
            5,
            True,
            (2,),
        )
        assert api._cache["search"][cache_key] == archive_data

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_search_archive_hit_list_only_false(self, _mock_http: MagicMock) -> None:
        """list_only=False 时 Archive 命中应包装为 dict"""
        api = BangumiApi()
        archive_data = [{"id": 1, "name": "Test", "type": 2}]
        api._archive = MagicMock()
        api._archive.try_search.return_value = _mock_archive_hit(archive_data)
        api._request_with_retry = MagicMock()

        result = api.search(
            title="Test",
            start_date="2026-01-01",
            end_date="2026-02-01",
            list_only=False,
        )

        assert result == {"data": archive_data}

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_search_archive_miss_falls_back_to_api(self, _mock_http: MagicMock) -> None:
        """Archive 未命中时应降级到 API"""
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_search.return_value = _mock_archive_miss()

        api_data = {"data": [{"id": 1, "name": "From API"}]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_data
        api._request_with_retry = MagicMock(return_value=mock_resp)

        result = api.search(
            title="Test", start_date="2026-01-01", end_date="2026-02-01"
        )

        assert result == [{"id": 1, "name": "From API"}]
        api._request_with_retry.assert_called_once()

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_search_cache_hit_skips_archive_and_api(
        self, _mock_http: MagicMock
    ) -> None:
        """缓存命中时应跳过 Archive 和 API"""
        api = BangumiApi()
        cached = [{"id": 1, "name": "Cached"}]
        cache_key = ("Test", "2026-01-01", "2026-02-01", 5, True, (2,))
        api._put_cache("search", cache_key, cached)

        api._archive = MagicMock()
        api._request_with_retry = MagicMock()

        result = api.search(
            title="Test", start_date="2026-01-01", end_date="2026-02-01"
        )

        assert result == cached
        api._archive.try_search.assert_not_called()
        api._request_with_retry.assert_not_called()


class TestShortcutResultNamedTuple:
    """ShortcutResult 作为 NamedTuple 的基本行为"""

    def test_named_tuple_fields(self) -> None:
        r = ShortcutResult(True, {"id": 1}, "archive_hit")
        assert r.hit is True
        assert r.data == {"id": 1}
        assert r.reason == "archive_hit"

    def test_unpack(self) -> None:
        hit, data, reason = ShortcutResult(False, None, "archive_disabled")
        assert hit is False
        assert data is None
        assert reason == "archive_disabled"
