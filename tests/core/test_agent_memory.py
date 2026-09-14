"""AgentMemoryRepository 测试（Phase 2.0.1 写入侧）。

覆盖 BDD 场景 W1/W4/W6/W8/W9/W10/W11。
"""

import sqlite3
from unittest.mock import patch

import pytest

from app.models.memory import MemoryEntry

MAIN_COLS = "id, task_type, task_id, run_id, summary, full_text, outcome, tokens_used, created_at"


def _make_db(temp_dir, name="memory.db"):
    """创建一个指向临时文件的 DatabaseManager 并返回。"""
    db_path = temp_dir / name
    with patch("app.core.database.logger"):
        from app.core.database import DatabaseManager

        db = DatabaseManager(str(db_path))
    return db


def _entry(run_id: str, summary: str = "昨日看了芙莉莲", **overrides) -> MemoryEntry:
    defaults = {
        "task_type": "summary",
        "task_id": "summary-daily",
        "run_id": run_id,
        "summary": summary,
        "full_text": f"{summary}（全文）",
        "outcome": "success",
        "tokens_used": 120,
    }
    defaults.update(overrides)
    return MemoryEntry(**defaults)


def _log_record(db, *, title="葬送的芙莉莲", episode=10, bgm_title="葬送的芙莉莲"):
    return db.log_sync_record(
        user_name="dad",
        title=title,
        ori_title=None,
        season=1,
        episode=episode,
        subject_id="1",
        status="success",
        source="plex",
        media_type="episode",
        bgm_title=bgm_title,
    )


def _main_rows(db) -> list[tuple]:
    conn = db._get_connection()
    return conn.execute(
        f"SELECT {MAIN_COLS} FROM agent_working_memory ORDER BY id"
    ).fetchall()


def _archive_rows(db) -> list[tuple]:
    conn = db._get_connection()
    return conn.execute(
        f"SELECT {MAIN_COLS} FROM agent_working_memory_archive ORDER BY id"
    ).fetchall()


# ── 表结构（W4 数据基础）───────────────────────────────────────────────


class TestSchema:
    def test_memory_tables_and_fts_created(self, temp_dir, reset_singletons):
        _ = _make_db(temp_dir)

        with sqlite3.connect(str(temp_dir / "memory.db")) as raw:
            names = {
                r[0]
                for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')"
                )
            }
            assert "agent_working_memory" in names
            assert "agent_working_memory_archive" in names
            assert "agent_memory_fts" in names

            triggers = {
                r[0]
                for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            assert {"agent_memory_ai", "agent_memory_ad", "agent_memory_au"} <= triggers

    def test_memory_table_has_expected_columns(self, temp_dir, reset_singletons):
        _ = _make_db(temp_dir)

        with sqlite3.connect(str(temp_dir / "memory.db")) as raw:
            rows = raw.execute("PRAGMA table_info(agent_working_memory)").fetchall()
            cols = [r[1] for r in rows]
            assert "task_type" in cols
            assert "task_id" in cols
            assert "run_id" in cols
            assert "summary" in cols
            assert "full_text" in cols
            assert "outcome" in cols
            assert "tokens_used" in cols
            assert "created_at" in cols
            # run_id 唯一索引（UNIQUE 列约束 → sqlite 自动索引，列名经 index_info 查）
            indexes = raw.execute("PRAGMA index_list(agent_working_memory)").fetchall()
            unique_cols: set[str] = set()
            for idx in indexes:
                if idx[2] == 1:  # unique=1
                    cols = raw.execute(f"PRAGMA index_info({idx[1]})").fetchall()
                    unique_cols.update(c[2] for c in cols)
            assert "run_id" in unique_cols

    def test_facade_exposes_memory_and_sync_records(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        from app.core.database.agent_memory import AgentMemoryRepository
        from app.core.database.sync_records import SyncRecordsRepository

        assert isinstance(db.memory, AgentMemoryRepository)
        assert isinstance(db.sync_records, SyncRecordsRepository)

    def test_sync_records_consumed_run_id_index_created(
        self, temp_dir, reset_singletons
    ):
        """#8：关联表 run_id 建有索引（clear_task 清消费标记免全表扫）。"""
        _ = _make_db(temp_dir)

        with sqlite3.connect(str(temp_dir / "memory.db")) as raw:
            indexes = {
                r[0]
                for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert "idx_sync_records_consumed_run_id" in indexes


# ── W1 成功路径写入 ─────────────────────────────────────────────────────


class TestStoreAndMark:
    def test_inserts_memory_and_marks_consumed_in_same_transaction(
        self, temp_dir, reset_singletons
    ):
        """W1：store_and_mark 写入记忆 + 标记消费（同一事务原子完成）。"""
        db = _make_db(temp_dir)
        r1 = _log_record(db, episode=10)
        r2 = _log_record(db, title="鬼灭之刃", episode=5, bgm_title="鬼灭之刃")

        db.memory.store_and_mark(_entry("run-1"), [r1, r2])

        rows = _main_rows(db)
        assert len(rows) == 1
        assert rows[0][3] == "run-1"  # run_id
        assert rows[0][4] == "昨日看了芙莉莲"  # summary

        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        by_id = {r["id"]: r for r in recs}
        assert by_id[r1]["consumed_run_ids"] == {"run-1"}
        assert by_id[r2]["consumed_run_ids"] == {"run-1"}

    def test_no_record_ids_skips_marking(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-2"), [])

        assert len(_main_rows(db)) == 1
        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        assert all(r["consumed_run_ids"] == set() for r in recs)

    def test_duplicate_run_id_raises(self, temp_dir, reset_singletons):
        """run_id UNIQUE：重复 run_id 抛异常（调用方 try/except 捕获）。"""
        import sqlite3

        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-x"), [])

        with pytest.raises(sqlite3.IntegrityError):
            db.memory.store_and_mark(_entry("run-x"), [])


# ── W4 FTS5 触发器同步 ──────────────────────────────────────────────────


class TestFtsSearch:
    def test_search_fts_finds_newly_inserted_row(self, temp_dir, reset_singletons):
        """W4：插入后触发器同步 FTS，search_fts 命中。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(
            _entry("run-1", summary="昨日看了葬送的芙莉莲 S1E10"), []
        )

        hits = db.memory.search_fts(["芙莉莲"], task_type="summary")
        assert len(hits) == 1
        assert hits[0].run_id == "run-1"
        assert hits[0].summary == "昨日看了葬送的芙莉莲 S1E10"

    def test_search_fts_filters_by_task_type(self, temp_dir, reset_singletons):
        """跨任务不命中：其他 task_type 的记忆不进 summary 检索结果。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(
            _entry(
                "run-a",
                summary="server 挂了 503",
                task_type="diagnostic",
                task_id="diag-x",
            ),
            [],
        )
        db.memory.store_and_mark(
            _entry(
                "run-b",
                summary="server 挂了 503",
                task_type="summary",
                task_id="summary-daily",
            ),
            [],
        )

        hits = db.memory.search_fts(["503"], task_type="summary")
        assert [h.run_id for h in hits] == ["run-b"]

    def test_search_fts_multiple_keywords_or(self, temp_dir, reset_singletons):
        """多关键词 OR：任一标题命中即相关（今日看的任一番剧都捞回）。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-1", summary="芙莉莲 S1E10 鬼灭之刃"), [])
        db.memory.store_and_mark(_entry("run-2", summary="只看了芙莉莲"), [])

        hits = db.memory.search_fts(["芙莉莲", "鬼灭之刃"], task_type="summary")
        # OR 语义：run-2 虽只含一词，仍是"与今日任一标题相关"的记忆
        assert {h.run_id for h in hits} == {"run-1", "run-2"}

    def test_search_fts_each_keyword_hits_independently(
        self, temp_dir, reset_singletons
    ):
        """多关键词 OR 的核心场景：两条记忆各命中一个标题，均被捞回（AND 会 0 命中）。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-a", summary="只看了芙莉莲"), [])
        db.memory.store_and_mark(_entry("run-b", summary="只看了鬼灭之刃"), [])

        hits = db.memory.search_fts(["芙莉莲", "鬼灭之刃"], task_type="summary")
        assert {h.run_id for h in hits} == {"run-a", "run-b"}

    def test_search_fts_multi_word_title_phrase_match(self, temp_dir, reset_singletons):
        """#4：带空格标题按短语整体匹配——不把 'Spy x Family' 拆成 Spy/Family 独立 OR
        （精确性：只命中含完整短语的记忆，而非'含任一单词'的记忆）。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-a", summary="Spy x Family 剧场版"), [])
        db.memory.store_and_mark(_entry("run-b", summary="Family Guy 新季开播"), [])

        hits = db.memory.search_fts(["Spy x Family"], task_type="summary")
        assert [h.run_id for h in hits] == ["run-a"]

    def test_search_fts_returns_empty_for_no_match(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-1", summary="芙莉莲"), [])
        assert db.memory.search_fts(["不存在的关键词"], task_type="summary") == []


# ── W6 prune 归档 / W9 feedback 保留 ────────────────────────────────────


class TestPrune:
    def test_prune_archives_old_records(self, temp_dir, reset_singletons):
        """W6：超出 keep 的旧记录先入归档再删主表（降级而非删除）。"""
        db = _make_db(temp_dir)
        for i in range(1005):
            db.memory.store_and_mark(_entry(f"run-{i}", summary=f"第{i}次总结"), [])

        db.memory.prune("summary", "summary-daily", keep=1000)

        assert len(_main_rows(db)) == 1000
        archive = _archive_rows(db)
        assert len(archive) == 5
        # 归档保留原 created_at/run_id
        assert {r[3] for r in archive} == {f"run-{i}" for i in range(5)}
        assert all(r[8] for r in archive)  # created_at 非空

    def test_prune_archives_all_outcomes(self, temp_dir, reset_singletons):
        """prune 不筛 outcome，超窗条目一律归档（feedback 优先保留属 phase3.x，
        见 specs/agent-phase3-summary-enhanced.md）。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(
            _entry("run-old", summary="早期总结", outcome="partial"), []
        )
        for i in range(1005):
            db.memory.store_and_mark(_entry(f"run-{i}", summary=f"第{i}次总结"), [])

        db.memory.prune("summary", "summary-daily", keep=1000)

        rows = _main_rows(db)
        assert all(r[3] != "run-old" for r in rows)
        assert any(r[3] == "run-old" for r in _archive_rows(db))

    def test_prune_scoped_to_task(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-a", task_id="summary-daily"), [])
        db.memory.store_and_mark(_entry("run-b", task_id="summary-weekly"), [])

        db.memory.prune("summary", "summary-daily", keep=0)

        rows = _main_rows(db)
        assert [r[3] for r in rows] == ["run-b"]


# ── W8 归档可检索 ───────────────────────────────────────────────────────


class TestSearchArchive:
    def test_search_archive_finds_keyword(self, temp_dir, reset_singletons):
        """W8：LIKE 检索冷记忆。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-1", summary="归档前的一次总结"), [])
        db.memory.prune("summary", "summary-daily", keep=0)

        hits = db.memory.search_archive("summary", "归档前")
        assert len(hits) == 1
        assert hits[0].run_id == "run-1"

    def test_search_archive_task_type_filter(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        db.memory.store_and_mark(
            _entry(
                "run-a",
                summary="共享关键词内容",
                task_type="summary",
                task_id="summary-daily",
            ),
            [],
        )
        db.memory.store_and_mark(
            _entry(
                "run-b",
                summary="共享关键词内容",
                task_type="diagnostic",
                task_id="diag-x",
            ),
            [],
        )
        db.memory.prune("summary", "summary-daily", keep=0)
        db.memory.prune("diagnostic", "diag-x", keep=0)

        hits = db.memory.search_archive("summary", "共享关键词")
        assert [h.run_id for h in hits] == ["run-a"]


# ── W10 老库补列幂等 ────────────────────────────────────────────────────


class TestMigration:
    def test_consumed_assoc_table_created_and_idempotent(
        self, temp_dir, reset_singletons
    ):
        """W10：启动时建消费关联表（多对多）+ run_id 索引，幂等。"""
        db = _make_db(temp_dir)

        with sqlite3.connect(str(temp_dir / "memory.db")) as raw:
            tables = [
                r[0]
                for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            assert "sync_records_consumed" in tables
            # 旧单列不存在（开发阶段直接建关联表，无迁移残留）
            cols = [r[1] for r in raw.execute("PRAGMA table_info(sync_records)")]
            assert "consumed_run_id" not in cols
            # run_id 反查索引存在（P1-1：clear_task / get_related_titles 依赖）
            indexes = {
                r[0]
                for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert "idx_sync_records_consumed_run_id" in indexes

        # 再次执行迁移方法幂等（表已存在，不报错）
        conn = db._get_connection()
        db._connection._ensure_sync_records_consumed(conn.cursor())
        with sqlite3.connect(str(temp_dir / "memory.db")) as raw:
            tables = [
                r[0]
                for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            assert "sync_records_consumed" in tables


# ── W11 查询返回 consumed_run_ids ───────────────────────────────────────


class TestQueryConsumed:
    def test_get_records_in_date_range_includes_consumed_run_ids(
        self, temp_dir, reset_singletons
    ):
        """W11：summary 底层查询返回 consumed_run_ids（关联表多对多聚合）。"""
        db = _make_db(temp_dir)
        r1 = _log_record(db)
        db.memory.store_and_mark(_entry("run-1"), [r1])

        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        assert recs and "consumed_run_ids" in recs[0]
        assert recs[0]["consumed_run_ids"] == {"run-1"}

    def test_default_query_omits_consumed_and_returns_empty_set(
        self, temp_dir, reset_singletons
    ):
        """P1：默认（轻量）查询返回空集合（无 JOIN/GROUP BY，记忆关闭场景）。"""
        db = _make_db(temp_dir)
        r1 = _log_record(db)
        db.memory.store_and_mark(_entry("run-1"), [r1])

        recs = db.get_records_in_date_range("2000-01-01", "2100-01-01")
        assert recs and "consumed_run_ids" in recs[0]
        assert recs[0]["consumed_run_ids"] == set()


# ── 2.0.3 清理与重置：rename_task / clear_task（C1-C12）──────────────────


class TestGetTaskRunIds:
    """get_task_run_ids：按 (task_type, task_id) 取本任务全部 run_id 集合。

    供消费排除按任务隔离使用——判断一条记录的 consumed_run_id 是否属于
    「当前任务」而非全局（跨任务互斥解除，见 BDD 增量窗口场景）。
    """

    def test_returns_all_run_ids_of_task(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-1", task_id="summary-daily"), [])
        db.memory.store_and_mark(_entry("run-2", task_id="summary-daily"), [])

        assert db.memory.get_task_run_ids("summary", "summary-daily") == {
            "run-1",
            "run-2",
        }

    def test_excludes_other_tasks(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-a", task_id="summary-daily"), [])
        db.memory.store_and_mark(
            _entry("run-b", task_type="summary", task_id="summary-yearly"), []
        )
        db.memory.store_and_mark(
            _entry("run-c", task_type="diagnostic", task_id="diag-x"), []
        )

        assert db.memory.get_task_run_ids("summary", "summary-daily") == {"run-a"}
        assert db.memory.get_task_run_ids("summary", "summary-yearly") == {"run-b"}
        assert db.memory.get_task_run_ids("diagnostic", "diag-x") == {"run-c"}

    def test_includes_archived_runs(self, temp_dir, reset_singletons):
        """prune 下沉归档后 run_id 仍归属本任务（消费标记不随归档失效）。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-1", task_id="summary-daily"), [])
        db.memory.store_and_mark(_entry("run-2", task_id="summary-daily"), [])
        db.memory.prune("summary", "summary-daily", keep=1)  # run-1 入归档

        assert db.memory.get_task_run_ids("summary", "summary-daily") == {
            "run-1",
            "run-2",
        }

    def test_no_memory_returns_empty_set(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        assert db.memory.get_task_run_ids("summary", "summary-empty") == set()


class TestRenameTask:
    def test_rename_migrates_main_and_archive(self, temp_dir, reset_singletons):
        """C1：改名迁移主表 + 归档表的 task_id。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-1", task_id="summary-daily"), [])
        db.memory.store_and_mark(_entry("run-2", task_id="summary-daily"), [])
        db.memory.prune("summary", "summary-daily", keep=1)  # run-1 入归档，run-2 保留

        n = db.memory.rename_task("summary", "summary-daily", "summary-daily2")

        assert n == 2
        main = [r[3] for r in _main_rows(db)]
        archive = [r[3] for r in _archive_rows(db)]
        assert set(main) == {"run-2"}
        assert archive == ["run-1"]
        assert all(r[2] == "summary-daily2" for r in _main_rows(db))
        assert all(r[2] == "summary-daily2" for r in _archive_rows(db))

    def test_rename_nonexistent_task_idempotent(self, temp_dir, reset_singletons):
        """C6：改不存在的 task_id → 无操作，不报错。"""
        db = _make_db(temp_dir)
        n = db.memory.rename_task("summary", "summary-nonexist", "summary-new")
        assert n == 0

    def test_rename_keeps_consumed_marks(self, temp_dir, reset_singletons):
        """C7：消费标记只关联 run_id，改名不破坏。"""
        db = _make_db(temp_dir)
        r1 = _log_record(db)
        db.memory.store_and_mark(_entry("run-1", task_id="summary-daily"), [r1])
        db.memory.rename_task("summary", "summary-daily", "summary-daily2")

        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        assert recs[0]["consumed_run_ids"] == {"run-1"}

    def test_rename_scoped_to_task_type_and_task(self, temp_dir, reset_singletons):
        """rename 只影响目标 (task_type, task_id)。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("run-a", task_id="summary-daily"), [])
        db.memory.store_and_mark(
            _entry("run-b", task_type="diagnostic", task_id="diag-x"), []
        )

        db.memory.rename_task("summary", "summary-daily", "summary-daily2")

        rows = _main_rows(db)
        by_run = {r[3]: r[2] for r in rows}
        assert by_run["run-a"] == "summary-daily2"
        assert by_run["run-b"] == "diag-x"


class TestClearTask:
    def _seed(self, db):
        """两条主表记忆 + 一条归档记忆 + 三条消费标记。"""
        r1 = _log_record(db, episode=1)
        r2 = _log_record(db, episode=2)
        r3 = _log_record(db, episode=3)
        db.memory.store_and_mark(_entry("u1", task_id="summary-daily"), [r1])
        db.memory.store_and_mark(_entry("u2", task_id="summary-daily"), [r2])
        db.memory.store_and_mark(_entry("u3", task_id="summary-daily"), [r3])
        db.memory.prune("summary", "summary-daily", keep=1)  # u1/u2 入归档
        return [r1, r2, r3]

    def test_clear_task_removes_memory_and_marks(self, temp_dir, reset_singletons):
        """C3：同一事务清主表 + 归档 + 消费标记（含归档 run_id）。"""
        db = _make_db(temp_dir)
        r1, r2, r3 = self._seed(db)

        n = db.memory.clear_task("summary", "summary-daily")

        # u3 主表 + u1/u2 归档 = 3 删除 + 3 消费标记清空
        assert n == 6
        assert _main_rows(db) == []
        assert _archive_rows(db) == []
        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        assert all(r["consumed_run_ids"] == set() for r in recs)

    def test_clear_task_isolation(self, temp_dir, reset_singletons):
        """C9：清 A 不影响 B。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("u1", task_id="summary-daily"), [])
        db.memory.store_and_mark(_entry("v1", task_id="summary-weekly"), [])

        db.memory.clear_task("summary", "summary-daily")

        rows = _main_rows(db)
        assert [r[3] for r in rows] == ["v1"]

    def test_clear_task_includes_all_outcomes(self, temp_dir, reset_singletons):
        """C10：彻底清空语义——不筛 outcome 全部删除。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(
            _entry("u-partial", task_id="summary-daily", outcome="partial"), []
        )
        db.memory.store_and_mark(_entry("u1", task_id="summary-daily"), [])

        db.memory.clear_task("summary", "summary-daily")

        assert _main_rows(db) == []

    def test_clear_task_idempotent(self, temp_dir, reset_singletons):
        """C11：再次清空不报错，影响行数 0。"""
        db = _make_db(temp_dir)
        db.memory.clear_task("summary", "summary-daily")
        assert db.memory.clear_task("summary", "summary-daily") == 0

    def test_clear_task_then_retrieve_empty(self, temp_dir, reset_singletons):
        """C8：清空后 retrieve 返回空（新上下文从零开始）。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(_entry("u1", task_id="summary-daily"), [])
        db.memory.clear_task("summary", "summary-daily")

        assert db.memory.get_recent("summary", "summary-daily") == []

    def test_run_ids_collected_before_delete(self, temp_dir, reset_singletons):
        """C12：run_id 收集先于删表——消费标记不因映射丢失而漏清。"""
        db = _make_db(temp_dir)
        r1, r2, r3 = self._seed(db)
        # 与 C3 相同验证，此处显式断言 u1/u2/u3 三个标记（含归档 run_id）全清
        db.memory.clear_task("summary", "summary-daily")

        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        marked = [r for r in recs if r["consumed_run_ids"]]
        assert marked == []

    def test_clear_task_keeps_other_task_consumed_marks(
        self, temp_dir, reset_singletons
    ):
        """C13（按任务隔离）：清 A 任务只清 A 的消费标记，B 任务的标记保留。

        跨任务消费隔离的清理侧：每日总结清空记忆不应抹掉年度总结的消费标记
        （否则年度总结会重复消费本已总结过的记录）。
        """
        db = _make_db(temp_dir)
        r_daily = _log_record(db, episode=1)
        r_yearly = _log_record(db, episode=2)
        db.memory.store_and_mark(_entry("d1", task_id="summary-daily"), [r_daily])
        db.memory.store_and_mark(_entry("y1", task_id="summary-yearly"), [r_yearly])

        db.memory.clear_task("summary", "summary-daily")

        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        by_id = {r["id"]: r for r in recs}
        assert by_id[r_daily]["consumed_run_ids"] == set()  # daily 标记被清
        assert by_id[r_yearly]["consumed_run_ids"] == {"y1"}  # yearly 标记保留

    def test_same_record_consumed_by_two_tasks_both_kept(
        self, temp_dir, reset_singletons
    ):
        """B（多对多）：同一条记录被每日 + 年度两个任务消费 → 两个标记共存。

        这是关联表相对单列的核心差异：旧实现（单列 consumed_run_id）后写覆盖
        先写；关联表 INSERT OR IGNORE 按 (record, run) 去重，互不覆盖。
        """
        db = _make_db(temp_dir)
        r1 = _log_record(db, episode=1)
        # daily 先消费 r1，yearly 后也消费 r1
        db.memory.store_and_mark(_entry("run-daily", task_id="summary-daily"), [r1])
        db.memory.store_and_mark(_entry("run-yearly", task_id="summary-yearly"), [r1])

        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        assert recs[0]["consumed_run_ids"] == {"run-daily", "run-yearly"}

        # 清 daily → 只删 daily 的标记，yearly 保留
        db.memory.clear_task("summary", "summary-daily")
        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        assert recs[0]["consumed_run_ids"] == {"run-yearly"}


# ── 同剧关联联表（S5/S6/S7：get_related_titles）─────────────────────


class TestGetRelatedTitles:
    """按剧名反查历史总结：consumed_run_id 联表，主表+归档 UNION。"""

    def test_empty_titles_short_circuits(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        assert (
            db.memory.get_related_titles("summary", "summary-daily", [], limit=5) == []
        )
        assert (
            db.memory.get_related_titles(
                "summary", "summary-daily", ["", "  "], limit=5
            )
            == []
        )

    def test_hits_main_and_archive(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        # run-1 消费了《葬送的芙莉莲》记录（主表）
        r1 = _log_record(db, bgm_title="葬送的芙莉莲")
        db.memory.store_and_mark(_entry("run-1", summary="芙莉莲 S1E10"), [r1])
        # run-2 消费《鬼灭之刃》记录后归档（冷层）
        r2 = _log_record(db, title="鬼灭之刃", bgm_title="鬼灭之刃")
        db.memory.store_and_mark(_entry("run-2", summary="鬼灭 S3E5"), [r2])
        # prune run-2 到归档
        db.memory.prune("summary", "summary-daily", keep=0)

        hits = db.memory.get_related_titles(
            "summary", "summary-daily", ["葬送的芙莉莲", "鬼灭之刃"], limit=5
        )
        run_ids = {h.run_id for h in hits}
        assert run_ids == {"run-1", "run-2"}  # 主表 + 归档冷层都命中

    def test_same_run_multi_episodes_deduped(self, temp_dir, reset_singletons):
        """S6：同剧 12 集被同一次总结消费 → GROUP BY m.id 只返回一条。"""
        db = _make_db(temp_dir)
        ids = [
            _log_record(db, bgm_title="葬送的芙莉莲", episode=i) for i in range(1, 4)
        ]
        db.memory.store_and_mark(_entry("run-1", summary="芙莉莲三集"), ids)

        hits = db.memory.get_related_titles(
            "summary", "summary-daily", ["葬送的芙莉莲"], limit=5
        )
        assert len(hits) == 1
        assert hits[0].run_id == "run-1"

    def test_task_isolation(self, temp_dir, reset_singletons):
        """任务隔离：其他任务的记忆不命中。"""
        db = _make_db(temp_dir)
        r = _log_record(db, bgm_title="葬送的芙莉莲")
        db.memory.store_and_mark(
            _entry("run-1", summary="芙莉莲", task_id="summary-weekly"), [r]
        )

        hits = db.memory.get_related_titles(
            "summary", "summary-daily", ["葬送的芙莉莲"], limit=5
        )
        assert hits == []

    def test_order_desc_and_limit(self, temp_dir, reset_singletons):
        """日期倒序 + LIMIT 生效。"""
        db = _make_db(temp_dir)
        for i in range(3):
            r = _log_record(db, bgm_title="葬送的芙莉莲", episode=i + 1)
            db.memory.store_and_mark(_entry(f"run-{i}", summary=f"第{i}次"), [r])

        hits = db.memory.get_related_titles(
            "summary", "summary-daily", ["葬送的芙莉莲"], limit=2
        )
        assert [h.run_id for h in hits] == ["run-2", "run-1"]  # 倒序取最近 2
