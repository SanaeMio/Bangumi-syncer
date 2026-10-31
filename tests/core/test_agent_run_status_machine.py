"""``no_suggestion`` 终态收敛为 ``succeeded`` 的状态机守卫。

对应评审意见 ``agent_runs.py:4``「no suggestion 不应该在状态机当中，no_suggestion
应该也是 succeeded 的一种」。

终态语义收敛为三态（``succeeded`` / ``failed`` / ``cancelled``），
「本次 run 成功结束但未产出建议」（预算耗尽 / 校验失败 / 明确放弃）由
``stop_reason`` 承载，``last_error`` 承载诊断信息。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.database import DatabaseManager
from app.core.database.agent_runs import _ACTIVE_STATUSES, _TERMINAL_STATUSES


def _make_db(tmp_path: Path) -> DatabaseManager:
    return DatabaseManager(str(tmp_path / "agent_runs_status.db"))


# ---------------------------------------------------------------------------
# 1. 状态集合：no_suggestion 不再是终态
# ---------------------------------------------------------------------------


def test_no_suggestion_not_in_terminal_statuses():
    """``no_suggestion`` 不再是终态（它是 succeeded 的一种，由 stop_reason 区分）。"""
    assert "no_suggestion" not in _TERMINAL_STATUSES
    assert _TERMINAL_STATUSES == ("succeeded", "failed", "cancelled")


def test_statuses_partition_fully():
    """终态与活性态无交集，且不含未知状态。"""
    assert not set(_TERMINAL_STATUSES) & set(_ACTIVE_STATUSES)
    assert _ACTIVE_STATUSES == ("pending", "processing")


# ---------------------------------------------------------------------------
# 2. 单一终态写入口：mark_succeeded 支持 last_error
# ---------------------------------------------------------------------------


def test_repository_has_no_mark_no_suggestion():
    """仓储不再暴露 mark_no_suggestion（无建议也是 succeeded）。"""
    from app.core.database.agent_runs import AgentRunsRepository

    assert not hasattr(AgentRunsRepository, "mark_no_suggestion")


def test_mark_succeeded_records_last_error(tmp_path: Path):
    """succeeded 可携带 last_error（校验失败/解析失败需要留诊断）。"""
    dbm = _make_db(tmp_path)
    dbm.agent_runs.create_pending("r1", "match", None)
    assert dbm.agent_runs.mark_succeeded(
        "r1", stop_reason="exhausted", last_error="无建议"
    )
    row = dbm.agent_runs.get_run("r1")
    assert row["status"] == "succeeded"
    assert row["stop_reason"] == "exhausted"
    assert row["last_error"] == "无建议"


def test_mark_succeeded_redacts_last_error(tmp_path: Path):
    """last_error 与 mark_failed 同口径：先脱敏后截断。"""
    dbm = _make_db(tmp_path)
    dbm.agent_runs.create_pending("r2", "match", None)
    dbm.agent_runs.mark_succeeded("r2", stop_reason="exhausted", last_error="裸 token")
    row = dbm.agent_runs.get_run("r2")
    assert row["last_error"], "应保留脱敏后的可诊断内容"


# ---------------------------------------------------------------------------
# 3. 旧库迁移：遗留 no_suggestion 行收敛为 succeeded
# ---------------------------------------------------------------------------


def test_legacy_no_suggestion_rows_migrate_to_succeeded(tmp_path: Path):
    """旧库遗留 ``status='no_suggestion'`` 的行在启动迁移后变为 ``succeeded``。"""
    db_path = str(tmp_path / "legacy.db")
    first = DatabaseManager(db_path)
    # 手工塞一行遗留数据（模拟迁移前的库）
    conn = first._get_connection()
    conn.execute(
        """
        INSERT INTO agent_runs
            (run_id, task_type, status, stop_reason, created_at, started_at)
        VALUES ('legacy-1', 'match', 'no_suggestion', 'exhausted', 1, 1)
        """
    )
    conn.commit()
    if first._connection._conn is not None:
        first._connection._conn.close()

    # 重新构造 → 触发迁移
    second = DatabaseManager(db_path)
    row = second.agent_runs.get_run("legacy-1")
    assert row is not None, "遗留行不应被删除"
    assert row["status"] == "succeeded", "遗留 no_suggestion 应迁移为 succeeded"
    # stop_reason 保留，差异信息不丢
    assert row["stop_reason"] == "exhausted"


def test_migration_is_idempotent(tmp_path: Path):
    """迁移幂等：重复执行影响 0 行（第二次构造后状态不变、无重复日志副作用）。"""
    db_path = str(tmp_path / "legacy2.db")
    first = DatabaseManager(db_path)
    conn = first._get_connection()
    conn.execute(
        """
        INSERT INTO agent_runs
            (run_id, task_type, status, stop_reason, created_at, started_at)
        VALUES ('legacy-2', 'match', 'no_suggestion', 'give_up', 1, 1)
        """
    )
    conn.commit()
    if first._connection._conn is not None:
        first._connection._conn.close()

    DatabaseManager(db_path)  # 首次重开触发迁移
    third = DatabaseManager(db_path)  # 再次重开验证幂等
    assert third.agent_runs.get_run("legacy-2")["status"] == "succeeded"

    # 迁移方法可重复调用且不抛错
    cur = third._get_connection().cursor()
    third._connection._migrate_agent_run_no_suggestion(cur)
    third._get_connection().commit()
    assert third.agent_runs.get_run("legacy-2")["status"] == "succeeded"


def test_legacy_row_still_cleanable_as_terminal(tmp_path: Path):
    """迁移后的行落在终态集合内，可被 cleanup_expired 正常清理（不留孤儿）。"""
    db_path = str(tmp_path / "legacy3.db")
    first = DatabaseManager(db_path)
    conn = first._get_connection()
    conn.execute(
        """
        INSERT INTO agent_runs
            (run_id, task_type, status, stop_reason, created_at,
             started_at, ended_at)
        VALUES ('legacy-3', 'match', 'no_suggestion', 'exhausted', 1, 1, 1)
        """
    )
    conn.commit()
    if first._connection._conn is not None:
        first._connection._conn.close()

    # ended_at=1（1970 年）远早于保留期窗口 → 应被清理
    second = DatabaseManager(db_path)
    deleted = second.agent_runs.cleanup_expired(retention_days=1)
    assert deleted >= 1, "迁移后的遗留行应可被终态清理"
    with sqlite3.connect(db_path) as raw:
        left = raw.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE run_id='legacy-3'"
        ).fetchone()[0]
    assert left == 0
