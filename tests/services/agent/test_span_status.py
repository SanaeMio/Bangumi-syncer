"""span 生命周期状态：初始 running、闭合 ok/error、崩溃残留识别与扫描。

BDD：

- **新建 span 即 running**：未闭合（进程中断）与正常结束**可区分**（旧行为硬编码 ok）。
- **正常闭合落 ok**：``end_span`` 默认 ``status="ok"``（chat / tool / seed 回归）。
- **异常闭合落 error**：显式 ``status="error"`` 保留（LLM 异常路径回归）。
- **残留扫描**：``mark_residual_running_spans`` 把「所属 run 已终结但自身仍 running」
  的 span 标记为 ``error`` + 中断文案；处理中的 run 不误伤（running 是合法中间态）；
  幂等（二次扫描 0 行）。
- ``trace.replay`` 不读 status（running 行存在时行为与现状一致）——由既有 replay 测试覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.database import DatabaseManager, set_database_manager
from app.services.agent.observe import trace


@pytest.fixture
def dbm(tmp_path: Path):
    instance = DatabaseManager(str(tmp_path / "span_status.db"))
    set_database_manager(instance)
    yield instance
    if instance._connection._conn is not None:
        instance._connection._conn.close()
    set_database_manager(None)


def _step(dbm, run_id: str, span_id: str) -> dict:
    return next(s for s in dbm.agent_runs.get_steps(run_id) if s["span_id"] == span_id)


def test_start_span_writes_running_status(dbm):
    """新建 span 即 running：崩溃残留与正常结束可区分。"""
    dbm.agent_runs.create_pending("run-1", "match")

    span_id = trace.start_span("run-1", "llm_chat", 0, 0)

    assert _step(dbm, "run-1", span_id)["status"] == "running"


def test_end_span_defaults_status_ok(dbm):
    """正常闭合：end_span 默认落 ok。"""
    dbm.agent_runs.create_pending("run-2", "match")
    span_id = trace.start_span("run-2", "tool_execute", 0, 1)

    trace.end_span(span_id)

    assert _step(dbm, "run-2", span_id)["status"] == "ok"


def test_end_span_error_status_kept(dbm):
    """异常闭合：显式 error 保留（LLM 异常路径回归）。"""
    dbm.agent_runs.create_pending("run-3", "match")
    span_id = trace.start_span("run-3", "llm_chat", 0, 0)

    trace.end_span(span_id, status="error", error="llm call raised before response")

    row = _step(dbm, "run-3", span_id)
    assert row["status"] == "error"
    assert row["error"] == "llm call raised before response"


def test_mark_residual_running_spans_marks_terminal_run(dbm):
    """所属 run 已终结的 running span → 标记 error + 中断文案。"""
    dbm.agent_runs.create_pending("run-4", "match")
    span_id = trace.start_span("run-4", "llm_chat", 0, 0)
    dbm.agent_runs.mark_failed("run-4", stop_reason="llm_error", last_error="boom")

    changed = dbm.agent_runs.mark_residual_running_spans()

    assert changed == 1
    row = _step(dbm, "run-4", span_id)
    assert row["status"] == "error"
    assert "未闭合" in row["error"]


def test_mark_residual_running_spans_skips_processing_run(dbm):
    """处理中的 run 不被误伤（running 是合法中间态）。"""
    dbm.agent_runs.create_pending("run-5", "match")
    span_id = trace.start_span("run-5", "llm_chat", 0, 0)
    assert dbm.agent_runs.atomic_claim("run-5") is True

    changed = dbm.agent_runs.mark_residual_running_spans()

    assert changed == 0
    assert _step(dbm, "run-5", span_id)["status"] == "running"


def test_mark_residual_running_spans_idempotent(dbm):
    """幂等：二次扫描不再改动（0 行）。"""
    dbm.agent_runs.create_pending("run-6", "match")
    trace.start_span("run-6", "llm_chat", 0, 0)
    dbm.agent_runs.mark_succeeded("run-6", stop_reason="submit_suggestion")
    assert dbm.agent_runs.mark_residual_running_spans() == 1

    assert dbm.agent_runs.mark_residual_running_spans() == 0
