"""AgentRunsRepository.reopen_interrupted_chat_span：恢复时复用中断轮次 span。

行为契约：
- 命中 ``run_id + name='llm_chat' + iteration + status='running'`` 的最新一行时，
  重置为可重新执行的初始态（status=running、error=''、ended_at=0、
  replay_delta=''、tokens=0、latency_ms=0、model=''、started_at=now），
  返回 span_id；后续 end_span 正常闭合同一行。
- 无命中（已闭合 / 轮次不符 / 非 llm_chat）返回 None，不产生任何写入。
- 幂等：同一行再次 reopen 仍返回同一 span_id（不新增行）。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.database import DatabaseManager, set_database_manager
from app.services.agent.observe import trace


@pytest.fixture
def dbm(tmp_path: Path) -> Iterator[DatabaseManager]:
    instance = DatabaseManager(str(tmp_path / "reopen_span.db"))
    set_database_manager(instance)
    yield instance
    if instance._connection._conn is not None:
        instance._connection._conn.close()
    set_database_manager(None)


def _row(dbm: DatabaseManager, span_id: str) -> dict:
    return next(s for s in dbm.agent_runs.get_steps("r1") if s["span_id"] == span_id)


def test_reopen_interrupted_chat_span_resets_fields(dbm: DatabaseManager):
    """命中 running 的中断轮次 → 重置字段并返回同一 span_id。"""
    dbm.agent_runs.create_pending("r1", "match")
    span_id = trace.start_span("r1", "llm_chat", 0, 0)
    # 模拟中断前已写入部分脏字段（真实场景一般只有 empty running 行）
    dbm.agent_runs.update_step(
        span_id,
        error="stale",
        tokens=99,
        latency_ms=123,
        model="m",
        ended_at=123,
        replay_delta={"response": {"stop_reason": "x"}},
    )

    before = int(time.time())
    got = dbm.agent_runs.reopen_interrupted_chat_span("r1", iteration=0)
    assert got == span_id

    row = _row(dbm, span_id)
    assert row["status"] == "running"
    assert row["error"] == ""
    assert row["ended_at"] == 0
    assert row["tokens"] == 0
    assert row["latency_ms"] == 0
    assert row["model"] == ""
    assert not row["replay_delta"]
    assert row["started_at"] >= before


def test_reopen_interrupted_chat_span_is_idempotent(dbm: DatabaseManager):
    """同一行再次 reopen 仍返回同一 span_id，不新增行。"""
    dbm.agent_runs.create_pending("r1", "match")
    span_id = trace.start_span("r1", "llm_chat", 0, 0)

    assert dbm.agent_runs.reopen_interrupted_chat_span("r1", 0) == span_id
    assert dbm.agent_runs.reopen_interrupted_chat_span("r1", 0) == span_id
    rows = [s for s in dbm.agent_runs.get_steps("r1") if s["name"] == "llm_chat"]
    assert [s["span_id"] for s in rows] == [span_id]


def test_reopen_interrupted_chat_span_returns_none_when_closed(
    dbm: DatabaseManager,
):
    """已闭合的轮次不复用（返回 None，不改写）。"""
    dbm.agent_runs.create_pending("r1", "match")
    span_id = trace.start_span("r1", "llm_chat", 0, 0)
    trace.end_span(span_id, status="ok")

    assert dbm.agent_runs.reopen_interrupted_chat_span("r1", 0) is None
    assert _row(dbm, span_id)["status"] == "ok"


def test_reopen_interrupted_chat_span_ignores_other_round_or_name(
    dbm: DatabaseManager,
):
    """轮次不符或非 llm_chat 行不得被复用。"""
    dbm.agent_runs.create_pending("r1", "match")
    other_round = trace.start_span("r1", "llm_chat", 1, 0)
    tool_span = trace.start_span("r1", "tool_execute", 0, 1)

    assert dbm.agent_runs.reopen_interrupted_chat_span("r1", 0) is None
    assert _row(dbm, other_round)["status"] == "running"
    assert _row(dbm, tool_span)["status"] == "running"


def test_reopen_interrupted_chat_span_matches_error_row(dbm: DatabaseManager):
    """重试后的 error chat 行也可复用，避免同 iteration 再写一条成功行。"""
    dbm.agent_runs.create_pending("r1", "match")
    span_id = trace.start_span("r1", "llm_chat", 0, 0)
    trace.end_span(span_id, status="error", error="boom")

    got = dbm.agent_runs.reopen_interrupted_chat_span("r1", iteration=0)

    assert got == span_id
    row = _row(dbm, span_id)
    assert row["status"] == "running"
    assert row["error"] == ""
    assert row["ended_at"] == 0
