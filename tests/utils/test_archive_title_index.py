"""Archive 标题索引单元测试

覆盖：
- ArchiveTitleIndex 构建 / 命中 / 失效重建
- 精确匹配（name / name_cn / infobox 别名）
- 模糊匹配（rapidfuzz，typo 容错）
- active 库路径切换自动重建
"""

from __future__ import annotations

import json
import sqlite3
import time
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
        "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
        lambda: db_path,
    )
    # 显式同步构建索引（测试场景可接受阻塞，3 条数据构建极快）
    assert index._ensure_built() is True
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
        # fixture 已构建，is_ready=True
        assert index.find_subject_ids_by_title("Test Anime") == [1, 3]
        assert index._built_path is not None
        # 失效
        index.invalidate()
        assert index._built_path is None
        assert index._title_to_ids == {}
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
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: new_db,
        )

        # 路径变化后 is_ready 仍为 True（内存索引未失效），但 _built_path 不匹配
        # find_subject_ids_* 不检查路径，仍返回旧索引结果
        # 需显式 invalidate + _ensure_built 重建
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
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
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


# ===== 磁盘缓存 =====


class TestDiskCache:
    """磁盘缓存：构建后写入 JSON，下次启动直接加载"""

    def test_build_writes_cache_file(self, tmp_path: Path, monkeypatch) -> None:
        """构建完成后应写入磁盘缓存文件"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        index = ArchiveTitleIndex()
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        assert index._ensure_built() is True

        cache_path = index._get_cache_path(db_path)
        assert cache_path.exists()
        # 缓存文件应包含 header + data 两行
        with open(cache_path, "rb") as f:
            header = json.loads(f.readline())
            data = json.loads(f.readline())
        assert header["format_version"] == 1
        assert header["db_mtime"] == db_path.stat().st_mtime
        assert header["db_size"] == db_path.stat().st_size
        assert "test anime" in data  # 归一化后的小写 key

    def test_load_from_disk_skips_db_build(self, tmp_path: Path, monkeypatch) -> None:
        """磁盘缓存有效时，加载应跳过 DB 构建"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        # 第一次：从 DB 构建 + 写入缓存
        index1 = ArchiveTitleIndex()
        assert index1._ensure_built() is True
        cache_path = index1._get_cache_path(db_path)
        assert cache_path.exists()

        # 第二次：新实例，应从磁盘缓存加载（不读 DB）
        index2 = ArchiveTitleIndex()
        # mock _build_internal 验证不被调用
        build_internal_called = False
        original_build = index2._build_internal

        def spy_build(*args, **kwargs):
            nonlocal build_internal_called
            build_internal_called = True
            return original_build(*args, **kwargs)

        index2._build_internal = spy_build
        assert index2._ensure_built() is True
        assert build_internal_called is False, "磁盘缓存有效时不应调用 _build_internal"
        # 查询应正常工作
        assert sorted(index2.find_subject_ids_by_title("Test Anime")) == [1, 3]

    def test_cache_invalidated_when_db_mtime_changes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """DB mtime 变化后缓存应失效，触发重建"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        # 第一次构建 + 写缓存
        index1 = ArchiveTitleIndex()
        assert index1._ensure_built() is True
        cache_path = index1._get_cache_path(db_path)
        assert cache_path.exists()

        # 修改 DB（mtime 变化）
        time.sleep(0.01)
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO subject (id, type, name) VALUES (999, 2, 'New')")
        conn.commit()
        conn.close()

        # 新实例：缓存应失效（mtime 不符），从 DB 重建
        index2 = ArchiveTitleIndex()
        assert index2._ensure_built() is True
        # 新条目应被索引
        assert 999 in index2.find_subject_ids_by_title("New")

    def test_cache_invalidated_when_format_version_mismatch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """缓存 format_version 不匹配时失效"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        # 写入一个旧版本缓存
        cache_path = db_path.parent / "bangumi_archive_a.index"
        db_stat = db_path.stat()
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "format_version": 0,  # 旧版本
                        "db_mtime": db_stat.st_mtime,
                        "db_size": db_stat.st_size,
                    }
                )
            )
            f.write("\n")
            f.write(json.dumps({"dummy": [1]}))

        index = ArchiveTitleIndex()
        # 旧版本缓存应被忽略，从 DB 重建
        assert index._ensure_built() is True
        assert sorted(index.find_subject_ids_by_title("Test Anime")) == [1, 3]


# ===== 后台构建 =====


class TestBackgroundBuild:
    """后台构建：build_in_background 启动线程，is_ready 控制降级"""

    def test_build_in_background_starts_thread(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """build_in_background 应启动后台线程"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        index = ArchiveTitleIndex()
        assert index.is_ready is False

        index.build_in_background()
        # 等待后台线程完成
        if index._build_thread is not None:
            index._build_thread.join(timeout=5)
        assert index.is_ready is True
        assert sorted(index.find_subject_ids_by_title("Test Anime")) == [1, 3]

    def test_build_in_background_skips_when_already_ready(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """已就绪时 build_in_background 应直接跳过"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        index = ArchiveTitleIndex()
        assert index._ensure_built() is True
        assert index.is_ready is True

        # 已就绪时不应启动新线程
        index.build_in_background()
        assert index._build_thread is None

    def test_build_in_background_loads_disk_cache(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """build_in_background 应优先加载磁盘缓存（同步加载，~ms 级）"""
        db_path = tmp_path / "test_archive_a.db"
        _create_test_db(db_path)
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
            lambda: db_path,
        )

        # 预先构建 + 写缓存
        index1 = ArchiveTitleIndex()
        assert index1._ensure_built() is True
        cache_path = index1._get_cache_path(db_path)
        assert cache_path.exists()

        # 新实例：build_in_background 应直接加载缓存，不启动线程
        index2 = ArchiveTitleIndex()
        index2.build_in_background()
        # 磁盘缓存加载是同步的，应立即可用
        assert index2.is_ready is True
        assert index2._build_thread is None  # 未启动后台线程

    def test_build_in_background_skips_when_db_missing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """active DB 不存在时 build_in_background 应直接返回"""
        nonexistent = tmp_path / "missing.db"
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.bangumi_archive.get_active_db_path",
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
            "app.utils.bangumi_archive._title_index.archive_title_index"
        ) as mock_index:
            mock_index.is_ready = False
            mock_index.build_in_background = MagicMock()

            r = shortcut.try_search("Test")

            assert r.hit is False
            assert r.reason == "archive_miss"
            # 应懒触发后台构建
            mock_index.build_in_background.assert_called_once()

    def test_try_search_old_returns_miss_when_not_ready(self) -> None:
        """索引未就绪时 try_search_old 返回 archive_miss"""
        shortcut = ArchiveShortcut()
        shortcut._enabled = True

        with patch(
            "app.utils.bangumi_archive._title_index.archive_title_index"
        ) as mock_index:
            mock_index.is_ready = False
            mock_index.build_in_background = MagicMock()

            r = shortcut.try_search_old("Test")

            assert r.hit is False
            assert r.reason == "archive_miss"
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
