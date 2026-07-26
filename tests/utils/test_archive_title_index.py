"""Archive 标题索引单元测试

覆盖：
- ArchiveTitleIndex 构建 / 命中 / 失效重建
- 精确匹配（name / name_cn / infobox 别名）
- 模糊匹配（rapidfuzz，typo 容错）
- active 库路径切换自动重建
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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
    """
    db_path = tmp_path / "test_archive.db"
    _create_test_db(db_path)

    index = ArchiveTitleIndex()
    # 替换 _archive 模块内的 bangumi_archive 引用
    monkeypatch.setattr(
        "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
        lambda: db_path,
    )
    return index, db_path


# ===== _normalize_key =====


class TestNormalizeKey:
    def test_lowercases(self) -> None:
        assert _normalize_key("Test Anime") == "test anime"

    def test_strips(self) -> None:
        assert _normalize_key("  Hello  ") == "hello"

    def test_empty(self) -> None:
        assert _normalize_key("") == ""

    def test_non_string(self) -> None:
        assert _normalize_key(None) == ""  # type: ignore[arg-type]


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


# ===== 失效与重建 =====


class TestInvalidateAndRebuild:
    def test_invalidate_clears_index(self, title_index_with_db) -> None:
        index, _ = title_index_with_db
        # 先构建
        assert index.find_subject_ids_by_title("Test Anime") == [1, 3]
        assert index._built_path is not None
        # 失效
        index.invalidate()
        assert index._built_path is None
        assert index._title_to_ids == {}
        # 再次查询应触发重建
        ids = index.find_subject_ids_by_title("Test Anime")
        assert sorted(ids) == [1, 3]

    def test_path_change_triggers_rebuild(
        self, title_index_with_db, tmp_path: Path, monkeypatch
    ) -> None:
        """active 库路径变化时索引应自动重建"""
        index, db_path = title_index_with_db
        # 先构建
        assert index.find_subject_ids_by_title("Test Anime") == [1, 3]
        original_built = index._built_path

        # 切换到一个新的 db（路径变化）
        new_db = tmp_path / "new_archive.db"
        _create_test_db(new_db)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: new_db,
        )

        # 路径变化后查询应触发重建
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
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: nonexistent,
        )
        assert index.find_subject_ids_by_title("Test Anime") == []
        assert index.find_subject_ids_fuzzy("Test Anime") == []


# ===== 全局单例 =====


class TestGlobalSingleton:
    def test_singleton_exists(self) -> None:
        assert archive_title_index is not None
        assert isinstance(archive_title_index, ArchiveTitleIndex)
