"""agent_runs / agent_steps schema 演进测试（FK 级联 / epoch / 删 payload_json）。

覆盖：
- PRAGMA foreign_keys=ON 后，删除 agent_runs 行 → agent_steps 级联消失
- 时间列存储 epoch 秒整数
- agent_steps 无 payload_json 列（schema 检查）
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.database import DatabaseManager, set_database_manager


@pytest.fixture
def dbm(tmp_path: Path) -> Iterator[DatabaseManager]:
    instance = DatabaseManager(str(tmp_path / "schema.db"))
    set_database_manager(instance)
    yield instance
    if instance._connection._conn is not None:
        instance._connection._conn.close()
    set_database_manager(None)


class TestForeignKeyCascade:
    def test_delete_run_cascades_steps(self, dbm):
        """删除 agent_runs 行 → agent_steps 级联消失。"""
        conn = dbm._connection._get_connection()
        # 确认 FK 已开启
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

        run_id = "run-cascade"
        dbm.agent_runs.create_pending(run_id, "match")
        # 写一条 step
        step_id = dbm.agent_runs.add_step(
            {
                "run_id": run_id,
                "span_id": "sp1",
                "name": "llm_chat",
                "iteration": 0,
                "sequence": 0,
            }
        )
        assert step_id > 0
        assert len(dbm.agent_runs.get_steps(run_id)) == 1

        # 删 run
        conn.execute("DELETE FROM agent_runs WHERE run_id=?", (run_id,))
        # step 应级联消失
        assert len(dbm.agent_runs.get_steps(run_id)) == 0

    def test_insert_step_references_missing_run_violates_fk(self, dbm):
        """FK 开启后，引用不存在的 run_id 应违反外键约束（原始连接层面）。"""
        conn = dbm._connection._get_connection()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO agent_steps (run_id, span_id, name, iteration, sequence)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("nonexistent-run", "sp1", "llm_chat", 0, 0),
            )


class TestEpochColumns:
    def test_time_columns_are_epoch_integers(self, dbm):
        """时间列存储 epoch 秒整数（agent_runs / agent_steps）。"""
        import time

        run_id = "run-epoch"
        dbm.agent_runs.create_pending(run_id, "match")
        before = int(time.time())
        dbm.agent_runs.atomic_claim(run_id)
        dbm.agent_runs.mark_succeeded(run_id, stop_reason="end_turn")
        after = int(time.time())

        conn = dbm._connection._get_connection()
        row = conn.execute(
            "SELECT started_at, ended_at, created_at FROM agent_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        # 均为整数（epoch 秒）
        for val in row:
            assert isinstance(val, int)
        assert before <= row[0] <= after or before <= row[1] <= after
        # created_at 也是整数
        assert isinstance(row[2], int)

    def test_step_started_at_ended_at_are_epoch(self, dbm):
        """agent_steps.started_at/ended_at 写入 epoch 整数。"""
        run_id = "run-step-epoch"
        injected = 1_700_000_000
        dbm.agent_runs.create_pending(run_id, "match")
        dbm.agent_runs.add_step(
            {
                "run_id": run_id,
                "span_id": "sp-epoch",
                "name": "llm_chat",
                "iteration": 0,
                "sequence": 0,
                "started_at": injected,
                "ended_at": injected + 5,
            }
        )
        steps = dbm.agent_runs.get_steps(run_id)
        assert len(steps) == 1
        s = steps[0]
        assert s["started_at"] == injected
        assert s["ended_at"] == injected + 5


class TestSchemaNoPayloadJson:
    def test_agent_steps_has_no_payload_json_column(self, dbm):
        """agent_steps 表不含 payload_json 列。"""
        conn = dbm._connection._get_connection()
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(agent_steps)").fetchall()
        }
        assert "payload_json" not in cols

    def test_agent_steps_has_run_id_index(self, dbm):
        """agent_steps 有 run_id 索引（FK 性能）。"""
        conn = dbm._connection._get_connection()
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='agent_steps'"
        ).fetchall()
        names = {r[0] for r in idx}
        assert "idx_agent_steps_run_id" in names
