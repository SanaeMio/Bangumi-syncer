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
from app.utils.bangumi_archive._store import ArchiveStore

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
        """Archive 命中但章节为空列表时仍算命中（避免 API 重复调用）

        subject 在 archive 中存在但无章节记录（如剧场版只有正片），
        返回 hit=True 避免对这类条目重复调用 API。
        """
        mock_store.get_episodes.return_value = []
        mock_store.get_subject.return_value = {"id": 1, "name": "Test"}
        r = self.shortcut.try_get_episodes(1)
        assert r.hit is True
        assert r.data == []
        assert r.reason == "archive_hit"
        # 空列表时应追加 subject 存在性校验
        mock_store.get_subject.assert_called_once_with(1)

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_episodes_hit_with_data(self, mock_store: MagicMock) -> None:
        mock_store.get_episodes.return_value = [{"id": 101, "sort": 1, "type": 0}]
        r = self.shortcut.try_get_episodes(1, episode_type=0)
        assert r.hit is True
        assert len(r.data) == 1
        mock_store.get_episodes.assert_called_once_with(1, episode_type=0)
        # 章节非空时不需校验 subject（短路提前返回）
        mock_store.get_subject.assert_not_called()

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_episodes_miss_when_subject_absent(
        self, mock_store: MagicMock
    ) -> None:
        """Archive 不完整时降级到 API（防止静默返回空数据导致 sync 失败）

        场景：新增条目/冷门条目不在 archive DB 中，底层 get_episodes 返回 []。
        修复前：误报 hit=True 导致不调用 API，sync 静默失败。
        修复后：追加 get_subject 校验，subject 不存在则返回 miss 降级到 API。
        """
        mock_store.get_episodes.return_value = []
        mock_store.get_subject.return_value = None
        r = self.shortcut.try_get_episodes(999)
        assert r.hit is False
        assert r.reason == "archive_miss"
        assert r.data is None
        mock_store.get_episodes.assert_called_once_with(999, episode_type=None)
        mock_store.get_subject.assert_called_once_with(999)

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_related_subjects_hit(self, mock_store: MagicMock) -> None:
        mock_store.get_related_subjects.return_value = [
            {"id": 2, "relation": "续集", "type": 3}
        ]
        r = self.shortcut.try_get_related_subjects(1)
        assert r.hit is True
        assert r.data[0]["id"] == 2
        # 关联非空时不需校验 subject
        mock_store.get_subject.assert_not_called()

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_related_subjects_hit_empty_list(
        self, mock_store: MagicMock
    ) -> None:
        """Archive 命中但关联为空时仍算命中（subject 存在但无关联记录）"""
        mock_store.get_related_subjects.return_value = []
        mock_store.get_subject.return_value = {"id": 1, "name": "Test"}
        r = self.shortcut.try_get_related_subjects(1)
        assert r.hit is True
        assert r.data == []
        assert r.reason == "archive_hit"
        # 空列表时应追加 subject 存在性校验
        mock_store.get_subject.assert_called_once_with(1)

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_get_related_subjects_miss_when_subject_absent(
        self, mock_store: MagicMock
    ) -> None:
        """Archive 不完整时降级到 API（防止续集链查找断链）

        场景：subject 不在 archive DB 中，底层 get_related_subjects 返回 []。
        修复前：误报 hit=True 导致续集链/前传链查找断链。
        修复后：追加 get_subject 校验，subject 不存在则返回 miss 降级到 API。
        """
        mock_store.get_related_subjects.return_value = []
        mock_store.get_subject.return_value = None
        r = self.shortcut.try_get_related_subjects(999)
        assert r.hit is False
        assert r.reason == "archive_miss"
        assert r.data is None
        mock_store.get_related_subjects.assert_called_once_with(999)
        mock_store.get_subject.assert_called_once_with(999)

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

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_series_closure_hit(self, mock_store: MagicMock) -> None:
        """分支型 IP：闭包收回全部可达节点（含兄弟续集/前传）"""
        mock_store.find_series_closure.return_value = [2, 5, 3, 4]
        r = self.shortcut.try_find_series_closure(1)
        assert r.hit is True
        assert r.data == [2, 5, 3, 4]
        mock_store.get_subject.assert_not_called()

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_series_closure_no_relation(self, mock_store: MagicMock) -> None:
        """subject 存在但无关联时返回 hit=True, data=[]"""
        mock_store.find_series_closure.return_value = []
        mock_store.get_subject.return_value = {"id": 1, "name": "Test"}
        r = self.shortcut.try_find_series_closure(1)
        assert r.hit is True
        assert r.data == []

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    def test_try_find_series_closure_miss(self, mock_store: MagicMock) -> None:
        """subject 不在 Archive 中时返回 miss（降级到 API）"""
        mock_store.find_series_closure.return_value = []
        mock_store.get_subject.return_value = None
        r = self.shortcut.try_find_series_closure(999)
        assert r.hit is False
        assert r.reason == "archive_miss"


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
        assert api._cache["get_subject"][(1, True)] == archive_data

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
        api._put_cache("get_subject", (1, True), cached)

        api._archive = MagicMock()
        api.get = MagicMock()

        result = api.get_subject(1)

        assert result == cached
        api._archive.try_get_subject.assert_not_called()
        api.get.assert_not_called()

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_subject_cache_isolates_archive_and_api(
        self, _mock_http: MagicMock
    ) -> None:
        """Archive 与 API 数据不得共用缓存槽：先 Archive 命中再 use_archive=False
        时应走 API 拿到含 images 的数据（修复前会返回无 images 的 Archive 缓存）"""
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_get_subject.return_value = _mock_archive_hit(
            {"id": 1, "name": "Archive Hit"}
        )

        archive_result = api.get_subject(1)
        assert archive_result == {"id": 1, "name": "Archive Hit"}

        api_data = {
            "id": 1,
            "name": "From API",
            "images": {"large": "https://lain.bgm.tv/x.jpg"},
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_data
        api.get = MagicMock(return_value=mock_resp)

        api_result = api.get_subject(1, use_archive=False)

        assert api_result == api_data
        assert api_result["images"]["large"] == "https://lain.bgm.tv/x.jpg"
        # 两槽位互不干扰
        assert api._cache["get_subject"][(1, True)]["name"] == "Archive Hit"
        assert api._cache["get_subject"][(1, False)]["name"] == "From API"

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_get_subject_use_archive_false_skips_archive(
        self, _mock_http: MagicMock
    ) -> None:
        """use_archive=False（封面等需要 images 的场景）应跳过 Archive 直接走 API"""
        api = BangumiApi()
        api._archive = MagicMock()
        api._archive.try_get_subject.return_value = _mock_archive_hit(
            {"id": 1, "name": "Archive Hit"}
        )

        api_data = {
            "id": 1,
            "name": "From API",
            "images": {"large": "https://lain.bgm.tv/x.jpg"},
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_data
        api.get = MagicMock(return_value=mock_resp)

        result = api.get_subject(1, use_archive=False)

        assert result == api_data
        api._archive.try_get_subject.assert_not_called()
        api.get.assert_called_once_with("subjects/1")

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
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_exact_match_hit(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """精确匹配命中时返回完整 subject 列表"""
        mock_index.is_ready = True
        # 步骤 0：原始 title 在 archive 中精确命中，跳过媒体前缀变体
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_for_query_title.return_value = ([1], True)
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 1,
                "type": 2,
                "name": "Test",
                "date": "2026-01-01",
            }
        ]

        r = self.shortcut.try_search("Test")

        assert r.hit is True
        assert r.reason == "archive_hit"
        assert r.match_method == "exact"
        assert len(r.data) == 1
        assert r.data[0]["id"] == 1
        # 精确命中后不应再调模糊匹配
        mock_index.find_subject_ids_fuzzy.assert_not_called()

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_fuzzy_match_hit(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """精确未命中时降级模糊匹配"""
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = []
        # find_subject_ids_for_query_title 返回模糊结果 (is_exact=False)
        mock_index.find_subject_ids_for_query_title.return_value = ([1], False)
        mock_index.find_subject_ids_fuzzy.return_value = [(1, 90)]
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 1,
                "type": 2,
                "name": "Test",
                "date": "2026-01-01",
            }
        ]

        r = self.shortcut.try_search("Tset")

        assert r.hit is True
        assert r.data[0]["id"] == 1
        # is_exact=False 的模糊命中应标注 match_method="fuzzy"
        assert r.match_method == "fuzzy"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_fuzzy_step5_match_method_tagged(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """步骤 1 精确 ids 被 type 过滤为空 + is_exact=True 时，步骤 5 模糊兜底
        命中应标注 match_method="fuzzy"（而非默认空串）"""
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = []
        # 步骤 1：精确命中 ids 但被 type/date 过滤后为空，is_exact=True
        mock_index.find_subject_ids_for_query_title.return_value = ([1], True)
        # 第一次（步骤 1）type 过滤为空，第二次（步骤 5）命中
        mock_store.get_subjects_by_ids_with_filter.side_effect = [
            [],  # 步骤 1 命中但被过滤
            [{"id": 2, "type": 2, "name": "Test", "date": "2026-01-01"}],  # 步骤 5
        ]
        mock_index.find_subject_ids_fuzzy.return_value = [(2, 85)]

        r = self.shortcut.try_search("Tset")

        assert r.hit is True
        assert r.data[0]["id"] == 2
        # 步骤 5 模糊兜底命中应标注 fuzzy（修复前默认空串）
        assert r.match_method == "fuzzy"

    @patch("app.utils.bangumi_archive._title_index.archive_title_index")
    def test_no_match_returns_miss(self, mock_index: MagicMock) -> None:
        """精确和模糊都未命中时返回 miss"""
        mock_index.find_subject_ids_by_title.return_value = []
        mock_index.find_subject_ids_fuzzy.return_value = []

        r = self.shortcut.try_search("Nonexistent")

        assert r.hit is False
        assert r.reason == "archive_miss"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_subject_type_filter_excludes(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """subject_types 过滤排除 type 不匹配的条目"""
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_for_query_title.return_value = ([1], True)
        mock_index.find_subject_ids_fuzzy.return_value = []
        # type=3 被过滤，get_subjects_by_ids_with_filter 返回空
        mock_store.get_subjects_by_ids_with_filter.return_value = []

        r = self.shortcut.try_search("Test", subject_types=[2])

        assert r.hit is False
        assert r.reason == "archive_miss"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_date_filter_excludes(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """air_date 过滤排除日期区间外的条目"""
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_for_query_title.return_value = ([1], True)
        mock_index.find_subject_ids_fuzzy.return_value = []
        # 日期区间外被过滤，get_subjects_by_ids_with_filter 返回空
        mock_store.get_subjects_by_ids_with_filter.return_value = []

        # 区间为 [2026-02-01, 2026-03-01)，subject.date=2026-01-15 在区间外
        r = self.shortcut.try_search(
            "Test", start_date="2026-02-01", end_date="2026-03-01"
        )

        assert r.hit is False
        assert r.reason == "archive_miss"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_date_filter_includes(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """air_date 区间内的条目应命中"""
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_for_query_title.return_value = ([1], True)
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 1,
                "type": 2,
                "name": "Test",
                "date": "2026-01-15",
            }
        ]

        r = self.shortcut.try_search(
            "Test", start_date="2026-01-01", end_date="2026-02-01"
        )

        assert r.hit is True
        assert r.data[0]["id"] == 1

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_date_missing_not_filtered(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """subject.date 缺失时不应被 air_date 过滤（避免误删无日期条目）

        try_search 命中后通过 get_subjects_by_ids_with_filter 拉取并按 type/air_date
        过滤；subject.date 为空字符串时该层应保留条目（_store 侧 is_exact 保护），
        故 hit=True。注意 patch 目标必须是 _archive_shortcut 中实际引用的
        archive_title_index，否则 mock 不生效。
        """
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_for_query_title.return_value = ([1], True)
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 1,
                "type": 2,
                "name": "Test",
                "date": "",  # 空日期
            }
        ]

        r = self.shortcut.try_search(
            "Test", start_date="2026-01-01", end_date="2026-02-01"
        )

        assert r.hit is True

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_limit_respected(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """limit 参数应限制返回数量"""
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = [1, 2, 3]
        mock_index.find_subject_ids_for_query_title.return_value = (
            [1, 2, 3],
            True,
        )
        mock_index.find_subject_ids_fuzzy.return_value = []
        # limit=2 时 get_subjects_by_ids_with_filter 内部截断，返回 2 条
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 1,
                "type": 2,
                "name": "Test1",
                "date": "2026-01-01",
            },
            {
                "id": 2,
                "type": 2,
                "name": "Test2",
                "date": "2026-01-01",
            },
        ]

        r = self.shortcut.try_search("Test", limit=2)

        assert r.hit is True
        assert len(r.data) == 2

    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_exception_returns_archive_error(self, mock_index: MagicMock) -> None:
        """查询异常时返回 archive_error"""
        mock_index.find_subject_ids_by_title.side_effect = RuntimeError("db locked")

        r = self.shortcut.try_search("Test")

        assert r.hit is False
        assert r.reason == "archive_error"

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_prefix_variant_hit_sets_match_method(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """媒体前缀变体（剧场版 X）精确命中时，match_method 应为 prefix_variant"""
        mock_index.is_ready = True

        # 原始 title "X" 不在 archive 精确命中 → 步骤 0 触发前缀变体尝试；
        # 仅「剧场版 X」精确命中，其余前缀变体落空。
        def _by_title(t: str, **kwargs) -> list:
            return [1] if t == "剧场版 X" else []

        mock_index.find_subject_ids_by_title.side_effect = _by_title
        mock_index.find_subject_ids_for_query_title.return_value = ([], False)
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {"id": 1, "type": 2, "name": "剧场版 X", "date": "2026-01-01"}
        ]

        r = self.shortcut.try_search("X")

        assert r.hit is True
        assert r.reason == "archive_hit"
        assert r.match_method == "prefix_variant"
        assert r.data[0]["id"] == 1

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_media_suffix_stripped_hit_sets_match_method(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """核心标题（遮天动画版→遮天）经媒体后缀剥离命中时，match_method 应为 media_suffix_stripped"""
        mock_index.is_ready = True

        # 步骤 0：原始 title「遮天动画版」不精确命中，前缀变体也落空；
        # 步骤 1：find_subject_ids_for_query_title 返回空，不命中；
        # 步骤 4：核心标题「遮天」精确命中 → media_suffix_stripped。
        def _by_title(t: str, **kwargs) -> list:
            return [1] if t == "遮天" else []

        mock_index.find_subject_ids_by_title.side_effect = _by_title
        mock_index.find_subject_ids_for_query_title.return_value = ([], False)
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {"id": 1, "type": 2, "name": "遮天", "date": "2026-01-01"}
        ]

        r = self.shortcut.try_search("遮天动画版")

        assert r.hit is True
        assert r.reason == "archive_hit"
        assert r.match_method == "media_suffix_stripped"
        assert r.data[0]["id"] == 1


# ---- try_search 遍历 ids 上限 ----


class TestArchiveShortcutTrySearchIdsLimit:
    """try_search 遍历 ids 上限测试

    回归用例：infobox 脏数据「台版|」归一化后命中上千条目，
    逐条 get_subject 拖慢查询至 4972ms。限制遍历上限后应快速返回。

    新实现将遍历逻辑下沉到 archive_store.get_subjects_by_ids_with_filter 内部，
    本测试验证 try_search 正确委托给该方法，
    当所有结果被过滤时返回 miss（遍历上限由 store 内部 MAX_IDS_TO_FETCH 保证）。
    """

    def setup_method(self) -> None:
        self.shortcut = ArchiveShortcut()
        self.shortcut._enabled = True

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_try_search_caps_ids_traversal(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """try_search 遍历 ids 超过上限时委托给 get_subjects_by_ids_with_filter

        新实现将遍历 + 截断逻辑下沉到 get_subjects_by_ids_with_filter 内部，
        本测试验证：try_search 委托给该方法，当所有结果被过滤时返回 miss。
        遍历上限 MAX_IDS_TO_FETCH 的截断行为由 store 层保证（见 test_bangumi_archive）。
        """
        # 构造大量同名 ids（超过上限）
        total_ids = ArchiveStore.MAX_IDS_TO_FETCH + 100
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = []
        mock_index.find_subject_ids_for_query_title.return_value = (
            list(range(total_ids)),
            True,
        )
        mock_index.find_subject_ids_fuzzy.return_value = []
        # 所有 subject 都被 type 过滤，返回空
        mock_store.get_subjects_by_ids_with_filter.return_value = []

        r = self.shortcut.try_search("脏数据")

        # 应返回 miss（所有结果都被过滤）
        assert r.hit is False
        assert r.reason == "archive_miss"
        # 验证委托给了 get_subjects_by_ids_with_filter
        mock_store.get_subjects_by_ids_with_filter.assert_called()

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_try_search_finds_result_within_limit(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """上限内找到合格结果时正常返回（不影响命中率）"""
        # 构造 50 个 ids（在上限内）
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = []
        mock_index.find_subject_ids_for_query_title.return_value = (
            list(range(50)),
            True,
        )
        mock_index.find_subject_ids_fuzzy.return_value = []
        # limit=5 时 get_subjects_by_ids_with_filter 内部截断，返回 5 条
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": i,
                "type": 2,
                "name": f"测试{i}",
                "date": "2026-01-01",
            }
            for i in range(5)
        ]

        r = self.shortcut.try_search("测试", limit=5)

        assert r.hit is True
        assert len(r.data) == 5


# ---- BangumiApi.search() Archive 短路集成 ----


class TestBangumiApiSearchArchiveIntegration:
    """BangumiApi.search() 接入 Archive 短路后的行为"""

    @patch("app.utils.bangumi_api.httpx.Client")
    def test_search_archive_hit_skips_api(self, _mock_http: MagicMock) -> None:
        """阶段五：search() 移除 archive 短路后，archive 命中状态不影响 search()，
        search() 直接走 API。archive 短路已提升为 ArchiveShortcutStep（管道层）。
        """
        api = BangumiApi()
        archive_data = [{"id": 1, "name": "Test", "type": 2}]
        api._archive = MagicMock()
        # archive 命中状态不再影响 search()（短路已移到 ArchiveShortcutStep）
        api._archive.try_search.return_value = _mock_archive_hit(archive_data)

        api_data = {"data": archive_data}
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_data
        api._request_with_retry = MagicMock(return_value=mock_resp)

        result = api.search(
            title="Test", start_date="2026-01-01", end_date="2026-02-01"
        )

        assert result == archive_data
        # search() 不再调 try_search（阶段五移到 ArchiveShortcutStep）
        api._archive.try_search.assert_not_called()
        api._request_with_retry.assert_called_once()
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
        """阶段五：list_only=False 时 search() 返回 API 的 dict（archive 不再参与）"""
        api = BangumiApi()
        archive_data = [{"id": 1, "name": "Test", "type": 2}]
        api._archive = MagicMock()
        api._archive.try_search.return_value = _mock_archive_hit(archive_data)

        api_data = {"data": archive_data}
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_data
        api._request_with_retry = MagicMock(return_value=mock_resp)

        result = api.search(
            title="Test",
            start_date="2026-01-01",
            end_date="2026-02-01",
            list_only=False,
        )

        assert result == {"data": archive_data}
        api._archive.try_search.assert_not_called()
        api._request_with_retry.assert_called_once()

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


# ---- BangumiApi.search_old() Archive 短路集成 ----
# search_old 方法已从 BangumiApi 删除（统一走 search），原
# TestBangumiApiSearchOldArchiveIntegration 类随之移除。
# search 接入 Archive 短路的行为由上方 TestBangumiApiSearchArchiveIntegration
# 覆盖（含 archive 命中/未命中/缓存命中等场景）。


class TestShortcutResultNamedTuple:
    """ShortcutResult 作为 NamedTuple 的基本行为"""

    def test_named_tuple_fields(self) -> None:
        r = ShortcutResult(True, {"id": 1}, "archive_hit")
        assert r.hit is True
        assert r.data == {"id": 1}
        assert r.reason == "archive_hit"

    def test_unpack(self) -> None:
        # ShortcutResult 现含 4 字段（含 match_method），解包需包含默认值
        hit, data, reason, method = ShortcutResult(False, None, "archive_disabled")
        assert hit is False
        assert data is None
        assert reason == "archive_disabled"
        assert method == ""

    def test_match_method_default_empty(self) -> None:
        r = ShortcutResult(True, {"id": 1}, "archive_hit")
        assert r.match_method == ""


# ===== 标题季数/集数后缀剥离测试 =====


class TestStripSeasonEpisodeSuffix:
    """_strip_season_episode_suffix 后缀剥离测试"""

    def test_strip_sxxexx(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import (
            _strip_season_episode_suffix,
        )

        assert _strip_season_episode_suffix("完美世界 S06E279") == "完美世界"
        assert _strip_season_episode_suffix("Test S01E01") == "Test"
        assert _strip_season_episode_suffix("Test S1E1") == "Test"

    def test_strip_chinese_season(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import (
            _strip_season_episode_suffix,
        )

        assert _strip_season_episode_suffix("完美世界 第六季") == "完美世界"
        assert _strip_season_episode_suffix("凡人修仙传 第十期") == "凡人修仙传"
        assert _strip_season_episode_suffix("凡人修仙传 第十一季") == "凡人修仙传"

    def test_strip_season_english(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import (
            _strip_season_episode_suffix,
        )

        assert _strip_season_episode_suffix("Test Season 2") == "Test"
        assert _strip_season_episode_suffix("Test 2nd season") == "Test"

    def test_no_suffix_returns_original(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import (
            _strip_season_episode_suffix,
        )

        # 无可识别后缀时原样返回
        assert _strip_season_episode_suffix("完美世界") == "完美世界"
        assert _strip_season_episode_suffix("Test") == "Test"

    def test_empty_title(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import (
            _strip_season_episode_suffix,
        )

        assert _strip_season_episode_suffix("") == ""

    def test_strip_roman_numerals(self) -> None:
        """罗马数字版本号后缀剥离（II/III/IV 等）"""
        from app.utils.bangumi_api._archive_shortcut import (
            _strip_season_episode_suffix,
        )

        assert _strip_season_episode_suffix("CATMAN IV") == "CATMAN"
        assert _strip_season_episode_suffix("Perfect Days II") == "Perfect Days"
        assert _strip_season_episode_suffix("The Separation III") == "The Separation"
        # 单字符 I 不剥离（避免误剥人名/缩写）
        assert _strip_season_episode_suffix("Henry I") == "Henry I"
        # "Star Wars IV" → 剥离 IV 命中 "Star Wars"（典型场景）
        assert _strip_season_episode_suffix("Star Wars IV") == "Star Wars"
        # "Star Wars: Episode IV" 末尾 IV 会被剥离（虽然结果"Star Wars: Episode"
        # 看起来奇怪，但 try_search 步骤 3 标题分割会进一步处理：
        # 主段 "Star Wars" 精确命中，不影响最终匹配正确性）
        assert _strip_season_episode_suffix("Star Wars: Episode IV") == (
            "Star Wars: Episode"
        )

    def test_strip_version_number(self) -> None:
        """vN / Version N / Vol.N 版本号后缀剥离"""
        from app.utils.bangumi_api._archive_shortcut import (
            _strip_season_episode_suffix,
        )

        assert _strip_season_episode_suffix("Butterfly Jam v2") == "Butterfly Jam"
        assert _strip_season_episode_suffix("All stars Version 2") == "All stars"
        assert _strip_season_episode_suffix("Movie vol.1") == "Movie"
        assert _strip_season_episode_suffix("Test ver.3") == "Test"

    def test_strip_chinese_part_number(self) -> None:
        """第N部 后缀剥离（中文数字 + 阿拉伯数字）"""
        from app.utils.bangumi_api._archive_shortcut import (
            _strip_season_episode_suffix,
        )

        assert _strip_season_episode_suffix("うなぎ 第二部") == "うなぎ"
        assert _strip_season_episode_suffix("故事 第3部") == "故事"
        assert _strip_season_episode_suffix("番组 第十一部") == "番组"
        # 注意：「第三部门」末尾是"门"，不会被误剥
        assert _strip_season_episode_suffix("第三部门") == "第三部门"


class TestTrySearchSuffixStripping:
    """try_search 剥离后缀重试测试"""

    def setup_method(self) -> None:
        self.shortcut = ArchiveShortcut()
        self.shortcut._enabled = True

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_try_search_strips_suffix_on_miss(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """原始标题未命中时，剥离季后缀后用核心标题命中

        新实现：后缀剥离逻辑下沉到 find_subject_ids_for_query_title，
        本测试验证 try_search 正确消费其返回的 ids。
        """
        mock_index.is_ready = True
        # 步骤 0：原始标题不在 archive 中精确命中
        mock_index.find_subject_ids_by_title.return_value = []
        # find_subject_ids_for_query_title 内部剥离后缀后命中核心标题
        mock_index.find_subject_ids_for_query_title.return_value = (
            [244224],
            True,
        )
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 244224,
                "type": 2,
                "name": "完美世界",
                "date": "2021-01-01",
            }
        ]

        r = self.shortcut.try_search("完美世界 S06E279")

        assert r.hit is True
        assert r.reason == "archive_hit"
        assert r.data[0]["id"] == 244224

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_try_search_no_retry_on_exact_hit(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """原始标题精确命中时不触发模糊兜底"""
        mock_index.is_ready = True
        # 步骤 0：原始 title 在 archive 中精确命中（raw_ids 非空），跳过变体
        mock_index.find_subject_ids_by_title.return_value = [1]
        mock_index.find_subject_ids_for_query_title.return_value = ([1], True)
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 1,
                "type": 2,
                "name": "Test",
                "date": "2026-01-01",
            }
        ]

        r = self.shortcut.try_search("Test")

        assert r.hit is True
        # 精确命中后不应再调模糊匹配
        mock_index.find_subject_ids_fuzzy.assert_not_called()


# ---- 标题分割 / 包裹符剥离 ----


class TestSplitTitleSegments:
    """_split_title_segments 单元测试"""

    def test_split_by_fullwidth_colon(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import _split_title_segments

        # 全角冒号分隔主副标题
        segs = _split_title_segments("魔法少女小圆：叛逆的物语")
        assert segs == ["魔法少女小圆", "叛逆的物语"]

    def test_split_by_halfwidth_colon(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import _split_title_segments

        segs = _split_title_segments("Fate: Unlimited Blade Works")
        assert segs == ["Fate", "Unlimited Blade Works"]

    def test_split_by_tilde(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import _split_title_segments

        # 全角波浪号分隔
        segs = _split_title_segments("進撃の巨人〜覚醒の咆哮")
        assert segs == ["進撃の巨人", "覚醒の咆哮"]

    def test_split_by_dash(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import _split_title_segments

        segs = _split_title_segments("Re:从零开始的异世界生活 - TV版")
        # 优先按冒号拆分（更早出现）
        assert segs == ["Re", "从零开始的异世界生活 - TV版"]

    def test_no_separator_returns_single(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import _split_title_segments

        segs = _split_title_segments("完美世界")
        assert segs == ["完美世界"]

    def test_separator_at_start_ignored(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import _split_title_segments

        # 分隔符在开头（idx=0）不算主副分隔
        segs = _split_title_segments(":Re:从零开始")
        # 仍然尝试找下一个冒号 idx > 0
        # 第 1 个冒号在 idx=0，跳过；第 2 个冒号在 idx=3
        # 拆为 [":Re", "从零开始"]? 让我看实现
        # 实际：find(":") 返回 0（第一个冒号），idx > 0 不满足，继续下一个 sep
        # 下一个 sep 是 ":"，find 还是返回 0... 死循环？
        # 不，sep 列表里有 ":" 和 "："，所以 ":" 会被找到一次，返回 idx=0
        # 因为 idx > 0 不满足，跳过这个 sep，继续下一个
        # 最终所有 sep 都不满足 idx > 0，返回 [cleaned]
        assert segs == [":Re:从零开始"]

    def test_empty_title(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import _split_title_segments

        assert _split_title_segments("") == []
        assert _split_title_segments(None) == []  # type: ignore[arg-type]

    def test_short_main_segment_skipped(self) -> None:
        from app.utils.bangumi_api._archive_shortcut import _split_title_segments

        # 主段长度 < 2 时不拆分（避免单字符误匹配）
        # "A:B" → 主段 "A" 长度 1，跳过
        segs = _split_title_segments("A:B")
        # 主段长度 1，不拆分，返回原标题
        assert segs == ["A:B"]


class TestStripTitleWrappers:
    """_strip_title_wrappers 单元测试"""

    def test_strip_japanese_brackets(self) -> None:
        from app.utils.bangumi_archive._title_normalize import _strip_title_wrappers

        assert _strip_title_wrappers("「君の名は。」") == "君の名は。"
        assert _strip_title_wrappers("『進撃の巨人』") == "進撃の巨人"

    def test_strip_chinese_brackets(self) -> None:
        from app.utils.bangumi_archive._title_normalize import _strip_title_wrappers

        assert _strip_title_wrappers("【特别篇】") == "特别篇"
        assert _strip_title_wrappers("《魔道祖师》") == "魔道祖师"

    def test_strip_ascii_brackets(self) -> None:
        from app.utils.bangumi_archive._title_normalize import _strip_title_wrappers

        assert _strip_title_wrappers("[Test]") == "Test"
        assert _strip_title_wrappers("(Test)") == "Test"

    def test_no_wrapper_returns_original(self) -> None:
        from app.utils.bangumi_archive._title_normalize import _strip_title_wrappers

        assert _strip_title_wrappers("完美世界") == "完美世界"
        assert (
            _strip_title_wrappers("Re:从零开始的异世界生活")
            == "Re:从零开始的异世界生活"
        )

    def test_only_outer_pair_stripped(self) -> None:
        from app.utils.bangumi_archive._title_normalize import _strip_title_wrappers

        # 仅剥离最外层一对，内层保持原样
        assert _strip_title_wrappers("「「嵌套」」") == "「嵌套」"

    def test_empty_title(self) -> None:
        from app.utils.bangumi_archive._title_normalize import _strip_title_wrappers

        assert _strip_title_wrappers("") == ""

    def test_short_inner_skipped(self) -> None:
        from app.utils.bangumi_archive._title_normalize import _strip_title_wrappers

        # 内部内容长度 < 2 时不剥离
        assert _strip_title_wrappers("「A」") == "「A」"


class TestTrySearchTitleSplitting:
    """try_search 标题分割 / 包裹符剥离 / 媒体前缀优先级测试"""

    def setup_method(self) -> None:
        self.shortcut = ArchiveShortcut()
        self.shortcut._enabled = True

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_try_search_splits_title_on_colon(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """标题含冒号主副分隔时，用主段精确匹配"""
        # 场景：查询「魔法少女小圆：叛逆的物语」
        # 原始标题精确未命中，媒体前缀变体未命中，
        # 标题分割后主段「魔法少女小圆」精确命中
        mock_index.is_ready = True

        # 用函数式 side_effect 让 mock 根据入参返回不同结果
        def fake_find(title: str, **kwargs) -> list:
            if title == "魔法少女小圆":
                return [42]
            return []

        mock_index.find_subject_ids_by_title.side_effect = fake_find
        # 步骤 1 find_subject_ids_for_query_title 未命中合格结果
        mock_index.find_subject_ids_for_query_title.return_value = ([], False)
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 42,
                "type": 2,
                "name": "魔法少女小圆",
                "date": "2026-01-01",
            }
        ]

        r = self.shortcut.try_search("魔法少女小圆：叛逆的物语")

        assert r.hit is True
        assert r.reason == "archive_hit"
        assert r.data[0]["id"] == 42
        # 验证主段被查询过
        mock_index.find_subject_ids_by_title.assert_any_call("魔法少女小圆", year=None)

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_try_search_strips_wrappers(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """标题被书名号包裹时，剥离外层后精确匹配"""
        mock_index.is_ready = True

        def fake_find(title: str, **kwargs) -> list:
            if title == "君の名は。":
                return [99]
            return []

        mock_index.find_subject_ids_by_title.side_effect = fake_find
        # 步骤 1 find_subject_ids_for_query_title 未命中合格结果
        mock_index.find_subject_ids_for_query_title.return_value = ([], False)
        mock_index.find_subject_ids_fuzzy.return_value = []
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 99,
                "type": 2,
                "name": "君の名は。",
                "date": "2026-01-01",
            }
        ]

        r = self.shortcut.try_search("「君の名は。」")

        assert r.hit is True
        assert r.reason == "archive_hit"
        assert r.data[0]["id"] == 99
        mock_index.find_subject_ids_by_title.assert_any_call("君の名は。", year=None)

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_try_search_media_prefix_priority_over_fuzzy(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """媒体前缀变体精确匹配应优先于模糊兜底

        场景：查询「クドわふたー」精确命中同名游戏（type=4）被过滤，
        模糊兜底会命中不相关的 TV 版「クドわふいたー」（低分）。
        应优先尝试「劇場版 クドわふたー」精确命中剧场版动画（type=2）。
        """
        mock_index.is_ready = True

        def fake_find(title: str, **kwargs) -> list:
            if title == "クドわふたー":
                return [100]  # 游戏版本（type=4）
            if title == "劇場版 クドわふたー":
                return [200]  # 剧场版动画（type=2）
            return []

        mock_index.find_subject_ids_by_title.side_effect = fake_find
        # 步骤 1 命中游戏（type=4）被 type 过滤
        mock_index.find_subject_ids_for_query_title.return_value = ([100], True)
        mock_index.find_subject_ids_fuzzy.return_value = []

        def fake_get_subjects(ids, *args, **kwargs):
            # 模拟 type 过滤：types_set={2} 时 type=4 被过滤，type=2 通过
            result = []
            for sid in ids:
                if sid == 100:
                    # type=4 被过滤，不加入结果
                    continue
                elif sid == 200:
                    result.append(
                        {
                            "id": sid,
                            "type": 2,
                            "name": f"Name{sid}",
                            "date": "2026-01-01",
                        }
                    )
            return result

        mock_store.get_subjects_by_ids_with_filter.side_effect = fake_get_subjects

        r = self.shortcut.try_search("クドわふたー")

        assert r.hit is True
        assert r.data[0]["id"] == 200  # 命中剧场版而非游戏
        # 模糊兜底不应被调用（媒体前缀已命中）
        mock_index.find_subject_ids_fuzzy.assert_not_called()

    @patch("app.utils.bangumi_api._archive_shortcut.archive_store")
    @patch("app.utils.bangumi_api._archive_shortcut.archive_title_index")
    def test_try_search_fuzzy_fallback_still_works(
        self, mock_index: MagicMock, mock_store: MagicMock
    ) -> None:
        """所有精确策略都失败时，模糊兜底仍能命中"""
        mock_index.is_ready = True
        mock_index.find_subject_ids_by_title.return_value = []
        # 步骤 1 find_subject_ids_for_query_title 未命中
        mock_index.find_subject_ids_for_query_title.return_value = ([], False)
        mock_index.find_subject_ids_fuzzy.return_value = [(500, 90)]
        mock_store.get_subjects_by_ids_with_filter.return_value = [
            {
                "id": 500,
                "type": 2,
                "name": "近似标题",
                "date": "2026-01-01",
            }
        ]

        r = self.shortcut.try_search("近似标题")

        assert r.hit is True
        assert r.data[0]["id"] == 500
