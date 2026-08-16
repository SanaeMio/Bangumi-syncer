"""Archive 标题索引单元测试（FTS5 trigram 方案）

覆盖：
- ArchiveTitleIndex 构建 / 命中 / 失效重建
- 精确匹配（name / name_cn / infobox 别名）
- 模糊匹配（rapidfuzz，typo 容错）
- FTS5 trigram 预筛（精确 phrase + 模糊 OR）
- active 库路径切换自动重建
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.utils.bangumi_api._archive_shortcut import ArchiveShortcut
from app.utils.bangumi_archive._title_index import (
    ArchiveTitleIndex,
    _normalize_key,
    archive_title_index,
)

# ===== 测试用 SQLite 数据库构建 =====


def _create_test_db(db_path: Path) -> None:
    """创建带 subject 表的测试数据库，写入 3 条样本"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE subject (
            id INTEGER PRIMARY KEY,
            type INTEGER,
            name TEXT,
            name_cn TEXT,
            infobox TEXT,
            platform TEXT,
            summary TEXT,
            nsfw INTEGER,
            date TEXT,
            favorite INTEGER,
            series INTEGER,
            tags TEXT,
            score REAL,
            score_details TEXT,
            rank INTEGER,
            meta_tags TEXT
        )
        """
    )
    # subject 1：动画，有别名（bullet list 形式）
    conn.execute(
        "INSERT INTO subject (id, type, name, name_cn, infobox, date) VALUES (?, ?, ?, ?, ?, ?)",
        (
            1,
            2,
            "Test Anime",
            "测试动画",
            "{{Infobox|alias=TA|别名=* テストアニメ\n* 测试番}}",
            "2026-01-15",
        ),
    )
    # subject 2：动画，无别名
    conn.execute(
        "INSERT INTO subject (id, type, name, name_cn, infobox, date) VALUES (?, ?, ?, ?, ?, ?)",
        (
            2,
            2,
            "Another Show",
            "另一个节目",
            "",
            "2026-02-20",
        ),
    )
    # subject 3：动画，与 subject 1 同名（用于测试多 id 命中）
    conn.execute(
        "INSERT INTO subject (id, type, name, name_cn, infobox, date) VALUES (?, ?, ?, ?, ?, ?)",
        (
            3,
            2,
            "Test Anime",
            "测试动画电影",
            "{{Infobox|alias=TA Movie}}",
            "2026-03-01",
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def title_index_with_db(tmp_path: Path, monkeypatch):
    """构造一个绑定到临时 db 的 ArchiveTitleIndex 实例

    通过 monkeypatch 替换全局 bangumi_archive.get_active_db_path，
    避免污染全局单例和真实文件系统。

    显式调用 _ensure_built() 触发同步构建，使 is_ready=True，
    后续 find_subject_ids_* 可正常查询。
    （生产环境由 build_in_background 后台构建，查询时 is_ready=False
    会降级到 API；测试场景需要直接查询，故显式构建。）
    """
    db_path = tmp_path / "test_archive.db"
    _create_test_db(db_path)

    index = ArchiveTitleIndex()
    # 替换 _archive 模块内的 bangumi_archive 引用
    monkeypatch.setattr(
        "app.utils.bangumi_archive._archive.bangumi_archive.get_active_db_path",
        lambda: db_path,
    )
    # 显式同步构建索引（测试场景可接受阻塞，3 条数据构建极快）
    assert index._ensure_built() is True
    return index, db_path


# ===== _normalize_key =====


class TestNormalizeKey:
    def test_lowercases(self) -> None:
        # 归一化后空格被去除（标点替换为空再转小写）
        assert _normalize_key("Test Anime") == "testanime"

    def test_strips(self) -> None:
        assert _normalize_key("  Hello  ") == "hello"

    def test_empty(self) -> None:
        assert _normalize_key("") == ""

    def test_non_string(self) -> None:
        assert _normalize_key(None) == ""  # type: ignore[arg-type]

    def test_nfkc_normalization(self) -> None:
        """NFKC 标准化：全角→半角"""
        # 全角英数字 → 半角
        assert _normalize_key("Ｔｅｓｔ") == "test"
        # 全角数字 → 半角
        assert _normalize_key("０８年") == "08年"

    def test_punct_removed(self) -> None:
        """标点符号应被去除（让 'C.A.N.S' 与 'CANS' 等价）"""
        assert _normalize_key("C.A.N.S") == "cans"
        assert _normalize_key("CANS") == "cans"
        assert _normalize_key("A-B") == "ab"
        assert _normalize_key("Re: Zero") == "rezero"
        # 标点差异的查询都命中同一 key
        assert _normalize_key("Battle Spirits [Re]") == _normalize_key(
            "Battle Spirits Re"
        )


# ===== 精确匹配 =====


class TestExactMatch:
    def test_match_name(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        ids = index.find_subject_ids_by_title("Test Anime")
        assert sorted(ids) == [1, 3]

    def test_match_name_cn(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        ids = index.find_subject_ids_by_title("测试动画")
        assert ids == [1]

    def test_match_case_insensitive(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        ids = index.find_subject_ids_by_title("TEST ANIME")
        assert sorted(ids) == [1, 3]

    def test_match_with_whitespace(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        ids = index.find_subject_ids_by_title("  Test Anime  ")
        assert sorted(ids) == [1, 3]

    def test_match_alias_bullet_list(self, title_index_with_db) -> None:
        """infobox 中 bullet list 别名应可命中"""
        index, _ = title_index_with_db
        ids = index.find_subject_ids_by_title("テストアニメ")
        assert ids == [1]

    def test_match_alias_simple_string(self, title_index_with_db) -> None:
        """infobox 中单值别名应可命中"""
        index, _ = title_index_with_db
        ids = index.find_subject_ids_by_title("TA")
        assert sorted(ids) == [1]  # 只有 subject 1 的 alias 是 "TA"

    def test_no_match(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        assert index.find_subject_ids_by_title("Nonexistent") == []

    def test_empty_query(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        assert index.find_subject_ids_by_title("") == []


# ===== 模糊匹配 =====


class TestFuzzyMatch:
    def test_typo_matches(self, title_index_with_db) -> None:
        """拼写错误（typo）应通过模糊匹配命中"""
        index, _ = title_index_with_db
        results = index.find_subject_ids_fuzzy("Test Anme", threshold=80)
        assert any(sid in (1, 3) for sid, _ in results)

    def test_returns_score_descending(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        results = index.find_subject_ids_fuzzy("Test Anime", threshold=90)
        # 精确匹配应得 100 分
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_below_threshold_no_results(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        results = index.find_subject_ids_fuzzy(
            "completely-different-text", threshold=95
        )
        assert results == []

    def test_limit(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        results = index.find_subject_ids_fuzzy("Test Anime", threshold=50, limit=2)
        assert len(results) <= 2

    def test_partial_match_substring_hits(self, title_index_with_db) -> None:
        """子串包含关系应通过 partial_ratio 命中

        'Another' 是 'Another Show' 的子串，应得 100 分
        """
        index, _ = title_index_with_db
        results = index.find_subject_ids_fuzzy("Another", threshold=80)
        assert any(sid == 2 for sid, _ in results)
        # 子串包含应得 100 分
        assert any(score == 100.0 for sid, score in results if sid == 2)

    def test_token_set_ratio_word_order(self, title_index_with_db) -> None:
        """字符相同顺序不同的查询应能命中

        归一化后 'Anime Test' 与 'Test Anime' 分别为 'animetest' 和 'testanime'，
        字符集相同但顺序不同，fuzz.ratio 仍能给较高分数。
        """
        index, _ = title_index_with_db
        results = index.find_subject_ids_fuzzy("Anime Test", threshold=60)
        assert any(sid in (1, 3) for sid, _ in results)


# ===== FTS5 trigram 预筛 =====


class TestFTS5Index:
    """FTS5 trigram 预筛测试（替代原 bigram 倒排索引）"""

    def test_score_candidate_substring_short_circuit(self) -> None:
        """子串包含应直接得 100 分"""
        from app.utils.bangumi_archive._fts_query import _score_candidate

        # 'test' 是 'test anime' 的子串
        assert _score_candidate("test", "testanime") == 100.0

    def test_score_candidate_exact_match(self) -> None:
        """完全相同应得 100 分"""
        from app.utils.bangumi_archive._fts_query import _score_candidate

        assert _score_candidate("testanime", "testanime") == 100.0

    def test_collect_candidates_exact_phrase_match(self, title_index_with_db) -> None:
        """精确模式（phrase MATCH）应返回子串命中的候选"""
        index, _ = title_index_with_db
        conn = index._ensure_conn()
        assert conn is not None
        # phrase MATCH 'testanime' 命中 subject 1 和 3
        candidates = index._collect_candidates_fts(conn, "testanime", fuzzy=False)
        assert 1 in candidates
        assert 3 in candidates

    def test_collect_candidates_fuzzy_or_match(self, title_index_with_db) -> None:
        """模糊模式（trigram OR）应能命中含 typo 的查询"""
        index, _ = title_index_with_db
        conn = index._ensure_conn()
        assert conn is not None
        # 'testanme' 的 trigram 与 'testanime' 大量重叠，OR 预筛应命中
        candidates = index._collect_candidates_fts(conn, "testanme", fuzzy=True)
        assert 1 in candidates

    def test_collect_candidates_empty_for_no_hits(self, title_index_with_db) -> None:
        """完全无 trigram 命中时返回空列表"""
        index, _ = title_index_with_db
        conn = index._ensure_conn()
        assert conn is not None
        candidates = index._collect_candidates_fts(conn, "zzzzzzz", fuzzy=True)
        assert candidates == []

    def test_collect_candidates_short_query_falls_back_to_like(
        self, title_index_with_db
    ) -> None:
        """2 字符查询回退 subject 表 LIKE（含 infobox）"""
        index, _ = title_index_with_db
        conn = index._ensure_conn()
        assert conn is not None
        # 'ta' 在 infobox alias=TA 中，LIKE 应命中 subject 1
        candidates = index._collect_candidates_fts(conn, "ta", fuzzy=False)
        assert 1 in candidates


# ===== 失效与重建 =====


class TestInvalidateAndRebuild:
    def test_invalidate_clears_index(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        # fixture 已构建，is_ready=True
        assert index.find_subject_ids_by_title("Test Anime") == [1, 3]
        assert index._built_path is not None
        # 失效
        index.invalidate()
        assert index._built_path is None
        # 失效后 is_ready=False，查询返回空（不触发同步构建）
        assert index.is_ready is False
        assert index.find_subject_ids_by_title("Test Anime") == []
        # 显式重建后查询正常
        assert index._ensure_built() is True
        assert index.is_ready is True
        assert sorted(index.find_subject_ids_by_title("Test Anime")) == [1, 3]

    def test_path_change_triggers_rebuild(
        self, title_index_with_db, tmp_path: Path, monkeypatch
    ) -> None:
        """active 库路径变化后需显式重建（查询不自动触发）"""
        index, db_path = title_index_with_db
        # fixture 已构建
        assert index.find_subject_ids_by_title("Test Anime") == [1, 3]
        original_built = index._built_path

        # 切换到一个新的 db（路径变化）
        new_db = tmp_path / "new_archive.db"
        _create_test_db(new_db)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.bangumi_archive.get_active_db_path",
            lambda: new_db,
        )

        # 路径变化后 is_ready 返回 False（_built_path 不匹配，见 is_ready 实现）
        # find_subject_ids_* 在 is_ready=False 时直接返回空列表，
        # 需显式 invalidate + _ensure_built 重建连接与 FTS5 表
        index.invalidate()
        assert index._ensure_built() is True
        ids = index.find_subject_ids_by_title("Test Anime")
        assert sorted(ids) == [1, 3]
        # built_path 应已更新为新路径
        assert index._built_path == new_db
        assert index._built_path != original_built

    def test_db_not_exists_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        """active db 不存在时返回空（不抛异常）"""
        index = ArchiveTitleIndex()
        nonexistent = tmp_path / "missing.db"
        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.bangumi_archive.get_active_db_path",
            lambda: nonexistent,
        )
        # 未构建时 is_ready=False，查询返回空
        assert index.is_ready is False
        assert index.find_subject_ids_by_title("Test Anime") == []
        assert index.find_subject_ids_fuzzy("Test Anime") == []

    def test_not_ready_returns_empty_without_building(
        self, title_index_with_db
    ) -> None:
        """索引未就绪时查询返回空，且不触发同步构建

        验证 find_subject_ids_* 不会阻塞调用方：
        - 未构建时 is_ready=False
        - 查询返回空列表
        - _built_path 仍为 None（未触发构建）
        """
        index, db_path = title_index_with_db
        index.invalidate()  # 清空索引
        assert index.is_ready is False
        assert index._built_path is None

        # 查询不应触发构建
        result = index.find_subject_ids_by_title("Test Anime")
        assert result == []
        assert index._built_path is None  # 仍未构建

        result_fuzzy = index.find_subject_ids_fuzzy("Test Anime")
        assert result_fuzzy == []
        assert index._built_path is None  # 仍未构建


# ===== 全局单例 =====


class TestGlobalSingleton:
    def test_singleton_exists(self) -> None:
        assert archive_title_index is not None
        assert isinstance(archive_title_index, ArchiveTitleIndex)


# ===== 后台构建 =====


class TestBackgroundBuild:
    """构建触发：build_in_background 同步初始化，is_ready 控制降级

    FTS5 方案下 build_in_background 为同步 no-op（表在导入时已建好），
    仅触发一次连接检查让 is_ready 状态正确，不启动后台线程。
    """

    def test_build_in_background_initiates_connection(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """build_in_background 应同步初始化连接，使 is_ready=True"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        index = ArchiveTitleIndex()
        assert index.is_ready is False

        index.build_in_background()
        # FTS5 方案下不启动后台线程，同步完成
        assert index._build_thread is None
        assert index.is_ready is True
        assert sorted(index.find_subject_ids_by_title("Test Anime")) == [1, 3]

    def test_build_in_background_skips_when_already_ready(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """已就绪时 build_in_background 应直接跳过"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        index = ArchiveTitleIndex()
        assert index._ensure_built() is True
        assert index.is_ready is True

        # 已就绪时不应启动新线程
        index.build_in_background()
        assert index._build_thread is None

    def test_build_in_background_skips_when_db_missing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """active DB 不存在时 build_in_background 应直接返回"""
        nonexistent = tmp_path / "missing.db"
        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.bangumi_archive.get_active_db_path",
            lambda: nonexistent,
        )

        index = ArchiveTitleIndex()
        index.build_in_background()
        assert index._build_thread is None
        assert index.is_ready is False


# ===== try_search 懒触发 =====


class TestTrySearchLazyBuild:
    """try_search 在索引未就绪时应降级到 API 并懒触发后台构建"""

    def test_try_search_returns_miss_when_not_ready(self) -> None:
        """索引未就绪时 try_search 返回 archive_miss"""
        shortcut = ArchiveShortcut()
        shortcut._enabled = True

        with patch(
            "app.utils.bangumi_api._archive_shortcut.archive_title_index"
        ) as mock_index:
            mock_index.is_ready = False
            mock_index.build_in_background = MagicMock()

            r = shortcut.try_search("Test")

            assert r.hit is False
            assert r.reason == "archive_miss"
            # 应懒触发后台构建
            mock_index.build_in_background.assert_called_once()

    def test_try_search_skips_lazy_build_when_ready(self) -> None:
        """索引就绪时 try_search 不触发后台构建"""
        shortcut = ArchiveShortcut()
        shortcut._enabled = True

        with (
            patch(
                "app.utils.bangumi_archive._title_index.archive_title_index"
            ) as mock_index,
            patch("app.utils.bangumi_api._archive_shortcut.archive_store"),
        ):
            mock_index.is_ready = True
            mock_index.find_subject_ids_by_title.return_value = []
            mock_index.find_subject_ids_fuzzy.return_value = []
            mock_index.build_in_background = MagicMock()

            shortcut.try_search("Test")

            # 就绪时不触发后台构建
            mock_index.build_in_background.assert_not_called()
