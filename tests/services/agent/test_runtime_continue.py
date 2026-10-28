"""``runtime.continue_run`` 恢复续跑状态机：终局分派防御测试。

覆盖：
- ``stop_reason`` 声称终止工具但 ``tool_calls`` 无匹配终止工具 → 不得把空 dict
  当作真实终局提交；应记 warning 并降级 exhausted，handle_terminal 不被调用
- 正常路径（存在匹配终止工具调用）行为不变：仍走 handle_terminal
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from app.core.database import (
    DatabaseManager,
    get_database_manager,
    set_database_manager,
)
from app.services.agent.observe import trace
from app.services.agent.run import runtime
from app.services.agent.run.scenario import ScenarioHooks


@pytest.fixture
def dbm(tmp_path: Path) -> Iterator[DatabaseManager]:
    instance = DatabaseManager(str(tmp_path / "continue.db"))
    set_database_manager(instance)
    yield instance
    if instance._connection._conn is not None:
        instance._connection._conn.close()
    set_database_manager(None)


@pytest.fixture
def log_records():
    """捕获自定义 Logger（print 实现）的日志行，产出 ``[(level, line)]``。"""
    from app.core.logging import logger as app_logger

    records: list[tuple[str, str]] = []

    def _listener(line: str, level: str) -> None:
        records.append((level, line))

    app_logger.add_listener(_listener)
    yield records
    app_logger.remove_listener(_listener)


def _write_seed(dbm: DatabaseManager, run_id: str) -> None:
    dbm.agent_runs.create_pending(run_id, "match")
    span_id = trace.start_span(run_id, "seed", 0, 0)
    dbm.agent_runs.update_step(
        span_id,
        replay_delta={"seed_messages": [{"role": "system", "content": "sys"}]},
        ended_at=int(time.time()),
    )


def _write_llm_chat(
    dbm: DatabaseManager,
    run_id: str,
    iteration: int,
    tool_calls,
    stop_reason: str,
    *,
    content: str = "",
    blocks=None,
) -> None:
    response: dict = {
        "stop_reason": stop_reason,
        "content": content,
        "tool_calls": tool_calls,
    }
    if blocks is not None:
        response["blocks"] = blocks
    span_id = trace.start_span(run_id, "llm_chat", iteration, 0)
    trace.end_span(span_id, replay_delta={"response": response})


def _write_tool_exec(
    dbm: DatabaseManager,
    run_id: str,
    iteration: int,
    sequence: int,
    tool_use_id: str,
    content: str,
) -> None:
    """写入一条 tool_execute span（含 tool_result replay_delta）。"""
    span_id = trace.start_span(run_id, "tool_execute", iteration, sequence)
    trace.end_span(
        span_id,
        replay_delta={
            "tool_result": {
                "tool_use_id": tool_use_id,
                "content": content,
                "is_error": False,
            }
        },
    )


class _FakeHooks:
    task_type = "match"

    def __init__(self, *, max_iterations: int = 10) -> None:
        self.terminal_calls: list[tuple[tuple, dict]] = []
        # 恢复路径不应触发 veto 软护栏（见 runtime.continue_run submit 分支注释）
        self.veto_calls: list[dict] = []
        # 失败事件出口：runtime 应在失败判定点回调 on_run_failed（通用层不直接通知）
        self.run_failed_events: list = []
        self.on_run_failed = self.run_failed_events.append
        self._max_iterations = max_iterations

    @staticmethod
    def register_tools(registry, ctx):
        """注册终止工具：continue_run 入口据此派生终止集合（单一来源）。"""
        from app.services.agent.execution.tools import ToolDefinition

        defn = ToolDefinition(
            name="submit_suggestion",
            description="",
            parameters={},
            handler=lambda args: {},
            access="terminal",
        )
        registry.register(defn, quiet=True)
        return [defn]

    def resolve_thinking_level(self) -> str:
        return "medium"

    def resolve_max_iterations(self, level: str) -> int:
        return self._max_iterations

    @staticmethod
    def build_stream_fn(thinking_level: str):
        """恢复续跑会构建流函数；本组测试用 fake ``_invoke_loop``，无需真实流。"""
        return lambda *args, **kwargs: None

    def veto_terminal(self, suggestion: dict) -> str | None:
        """live 路径的终止提交软护栏；恢复路径不应调用。"""
        self.veto_calls.append(suggestion)
        return "请先核对候选再提交"

    async def handle_terminal(self, run_id, result, ctx, **kwargs):
        """终局回调：新契约**不再**接收 dbm（全局单例由场景自取）。"""
        self.terminal_calls.append(((run_id, result, ctx), kwargs))
        # 模拟真实场景（llm_assist）终态落点：llm_error/max_tokens → failed
        if getattr(result, "stop_reason", None) in ("llm_error", "max_tokens"):
            get_database_manager().agent_runs.mark_failed(
                run_id,
                stop_reason=result.stop_reason,
                last_error=f"循环终止原因: {result.stop_reason}",
            )
        return "succeeded"


def _hooks_of(fake: _FakeHooks) -> ScenarioHooks:
    """显式收窄：_FakeHooks 结构上满足 runtime 所需钩子字段（测试替身）。"""
    return cast("ScenarioHooks", fake)


def test_continue_run_terminal_stop_without_tool_call_degrades_to_exhausted(
    dbm: DatabaseManager, log_records
):
    """stop_reason=终止工具名但 tool_calls 为空 → 降级 exhausted，不提交空建议。"""
    run_id = "run-term-empty"
    _write_seed(dbm, run_id)
    _write_llm_chat(dbm, run_id, 0, [], stop_reason="submit_suggestion")
    hooks = _FakeHooks()

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    run = dbm.agent_runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "succeeded"
    assert run["stop_reason"] == "exhausted"
    assert hooks.terminal_calls == [], "空建议不得传给 handle_terminal"
    warns = [line for level, line in log_records if level == "WARNING"]
    assert any("submit_suggestion" in line for line in warns)


def test_continue_run_terminal_stop_without_matching_tool_call_degrades_to_exhausted(
    dbm: DatabaseManager,
):
    """stop_reason=终止工具名但 tool_calls 不含该工具 → 同样降级 exhausted。"""
    run_id = "run-term-mismatch"
    _write_seed(dbm, run_id)
    _write_llm_chat(
        dbm,
        run_id,
        0,
        [{"id": "t1", "name": "search_bangumi", "input": {"title": "foo"}}],
        stop_reason="submit_suggestion",
    )
    hooks = _FakeHooks()

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    run = dbm.agent_runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "succeeded"
    assert run["stop_reason"] == "exhausted"
    assert hooks.terminal_calls == []


def test_continue_run_with_matching_terminal_tool_call_dispatches_normally(
    dbm: DatabaseManager,
):
    """存在匹配终止工具调用 → 正常走 handle_terminal（正常路径零回归）。"""
    run_id = "run-term-ok"
    _write_seed(dbm, run_id)
    submit_input = {"subject_id": "2", "reason": "best match"}
    _write_llm_chat(
        dbm,
        run_id,
        0,
        [{"id": "t9", "name": "submit_suggestion", "input": submit_input}],
        stop_reason="tool_use",
    )
    hooks = _FakeHooks()

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1
    args, _kwargs = hooks.terminal_calls[0]
    result = args[1]
    assert result.terminal_args == submit_input
    assert result.stop_reason == "submit_suggestion"


def test_continue_run_submit_terminal_does_not_replay_veto(
    dbm: DatabaseManager,
):
    """恢复路径的 submit 终局直接 handle_terminal，不重放 veto 软护栏。

    守卫当前**有意**设计：veto 是 live 会话的 best-effort 软护栏（防模型未核对
    即提交），crash 恢复 replay 到已记录的 submit 终局时直接落库，不重放 veto
    （会话内「已拦一次/剩余轮次」状态在崩溃中不可靠）。终局仍统一经
    normalize_stop_reason + handle_terminal 归一化落库。
    """
    run_id = "run-term-no-veto"
    _write_seed(dbm, run_id)
    submit_input = {"subject_id": "2", "reason": "best match"}
    _write_llm_chat(
        dbm,
        run_id,
        0,
        [{"id": "t9", "name": "submit_suggestion", "input": submit_input}],
        stop_reason="tool_use",
    )
    hooks = _FakeHooks()

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1
    assert hooks.veto_calls == [], "恢复路径不重放 veto 软护栏"


# ---------------------------------------------------------------------------
# stop_reason 终局归一化：与 loop.run_loop 判据对齐（空壳/max_tokens/未知有内容）
# ---------------------------------------------------------------------------


def _assert_failed_with_stop(dbm: DatabaseManager, run_id: str, stop: str) -> None:
    run = dbm.agent_runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "failed"
    assert run["stop_reason"] == stop


def test_continue_run_empty_shell_response_marks_failed_llm_error(
    dbm: DatabaseManager,
):
    """空壳响应（stop_reason=\"\" 且无 blocks/content）→ 不再空转调 LLM，落 llm_error。

    与 loop.run_loop 归一化一致：空壳不是 end_turn，也不应被当作「待补执行工具轮」
    反复续跑，而是按 llm_error 失败终态处理。
    """
    run_id = "run-empty-shell"
    _write_seed(dbm, run_id)
    _write_llm_chat(dbm, run_id, 0, [], stop_reason="", content="")
    hooks = _FakeHooks()

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1
    result = hooks.terminal_calls[0][0][1]
    assert result.stop_reason == "llm_error"
    _assert_failed_with_stop(dbm, run_id, "llm_error")


def test_continue_run_max_tokens_response_marks_failed_max_tokens(
    dbm: DatabaseManager,
):
    """max_tokens 无工具调用 → 终态 max_tokens（failed），不再续跑。"""
    run_id = "run-max-tokens"
    _write_seed(dbm, run_id)
    _write_llm_chat(dbm, run_id, 0, [], stop_reason="max_tokens", content="partial")
    hooks = _FakeHooks()

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1
    result = hooks.terminal_calls[0][0][1]
    assert result.stop_reason == "max_tokens"
    _assert_failed_with_stop(dbm, run_id, "max_tokens")


def test_continue_run_unknown_stop_reason_with_content_is_end_turn(
    dbm: DatabaseManager,
):
    """未知 stop_reason 但有内容（无工具调用）→ 按 loop.run_loop 兼容判据落 end_turn。"""
    run_id = "run-unknown-content"
    _write_seed(dbm, run_id)
    _write_llm_chat(
        dbm, run_id, 0, [], stop_reason="stop_sequence", content="我给出结论"
    )
    hooks = _FakeHooks()

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    run = dbm.agent_runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "succeeded"
    assert run["stop_reason"] == "end_turn"
    # end_turn 不经 handle_terminal（与既有直落 mark_succeeded 语义一致）
    assert hooks.terminal_calls == []


def test_continue_run_end_turn_still_marks_succeeded(
    dbm: DatabaseManager,
):
    """正常 end_turn 路径零回归：直接 mark_succeeded，不调 LLM/不 handle_terminal。"""
    run_id = "run-end-turn"
    _write_seed(dbm, run_id)
    _write_llm_chat(dbm, run_id, 0, [], stop_reason="end_turn", content="无建议")
    hooks = _FakeHooks()

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    run = dbm.agent_runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "succeeded"
    assert run["stop_reason"] == "end_turn"
    assert hooks.terminal_calls == []


# ---------------------------------------------------------------------------
# 收尾轮（预算耗尽后的 loop._final_recovery 响应）恢复分派：与 live 判据对齐
# ---------------------------------------------------------------------------


def _write_final_recovery_trace(
    dbm: DatabaseManager,
    run_id: str,
    *,
    stop_reason: str = "end_turn",
    content: str = "",
    tool_calls=None,
) -> None:
    """写入「1 轮预算工具轮 + 1 轮收尾轮」的最简 trace（`max_iterations=1`）。

    - iteration 0：工具调用 + 完整 tool_result（预算耗尽）
    - iteration 1：收尾轮 chat span（provider 原始 stop_reason 由参数给定）
    """
    _write_seed(dbm, run_id)
    _write_llm_chat(
        dbm,
        run_id,
        0,
        [{"id": "t1", "name": "search_bangumi", "input": {"kw": "x"}}],
        stop_reason="tool_use",
    )
    _write_tool_exec(dbm, run_id, 0, 0, "t1", "候选结果")
    _write_llm_chat(
        dbm,
        run_id,
        1,
        tool_calls or [],
        stop_reason=stop_reason,
        content=content,
    )


def test_continue_run_final_recovery_text_response_lands_exhausted_with_salvage(
    dbm: DatabaseManager,
):
    """收尾轮未提交（文本响应）→ 按 live 落 exhausted 并透传响应文本供兜底解析。

    live：``loop._final_recovery`` 无终止工具调用 → ``RunResult(stop_reason=
    "exhausted", last_response=收尾响应)``，场景层据 `last_response.content` 做
    `parse_suggestion` 文本兜底。
    恢复路径不得按 provider 原始 stop_reason（此处 ``end_turn``）直落 end_turn——
    否则建议兜底丢失、终态语义与 live 分叉。
    """
    run_id = "run-final-recovery-text"
    content = '{"subject_id": "12345", "reason": "名称与年份均吻合"}'
    hooks = _FakeHooks(max_iterations=1)
    _write_final_recovery_trace(dbm, run_id, stop_reason="end_turn", content=content)

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1, "收尾轮必须交场景终态处理（含文本兜底）"
    result = hooks.terminal_calls[0][0][1]
    assert result.stop_reason == "exhausted"
    assert result.last_response is not None, "响应文本必须透传（兜底解析输入）"
    assert result.last_response.content == content


def test_continue_run_final_recovery_max_tokens_lands_exhausted_not_failed(
    dbm: DatabaseManager,
):
    """收尾轮命中 max_tokens（截断文本）→ 仍按 live 落 exhausted，不得误落 failed。

    live 收尾轮判据与 provider 原始 stop_reason 无关：无终止工具调用一律
    exhausted（截断文本照常交场景兜底），与常规轮的 max_tokens→failed 分叉。
    """
    run_id = "run-final-recovery-maxtok"
    content = '{"subject_id": "9", "reason": "部分'
    hooks = _FakeHooks(max_iterations=1)
    _write_final_recovery_trace(dbm, run_id, stop_reason="max_tokens", content=content)

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1
    result = hooks.terminal_calls[0][0][1]
    assert result.stop_reason == "exhausted", (
        "收尾轮的 max_tokens 不是失败：live 仍按 exhausted 交场景兜底"
    )


def test_continue_run_final_recovery_stop_reason_alone_lands_exhausted(
    dbm: DatabaseManager,
):
    """收尾轮响应为「未知/空 stop_reason 且有内容」→ 仍落 exhausted。

    live 收尾轮不经过 ``normalize_stop_reason``：空壳/未知原因不会伪装成
    end_turn，也不会被当作 llm_error 失败——统一 exhausted 交场景兜底。
    """
    run_id = "run-final-recovery-unknown"
    content = "我建议提交 subject_id=7"
    hooks = _FakeHooks(max_iterations=1)
    _write_final_recovery_trace(
        dbm, run_id, stop_reason="stop_sequence", content=content
    )

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1
    result = hooks.terminal_calls[0][0][1]
    assert result.stop_reason == "exhausted"
    assert result.last_response is not None
    assert result.last_response.content == content


def test_continue_run_final_recovery_terminal_call_dispatches_submit(
    dbm: DatabaseManager,
):
    """收尾轮含终止工具调用 → 仍走 submit 分派（与 live 二分一致，零回归）。"""
    run_id = "run-final-recovery-submit"
    submit_input = {"subject_id": "2", "reason": "best match"}
    hooks = _FakeHooks(max_iterations=1)
    _write_final_recovery_trace(
        dbm,
        run_id,
        stop_reason="tool_use",
        tool_calls=[{"id": "t9", "name": "submit_suggestion", "input": submit_input}],
    )

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1
    result = hooks.terminal_calls[0][0][1]
    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == submit_input


def test_continue_run_final_recovery_stray_tool_call_ignored_lands_exhausted(
    dbm: DatabaseManager,
):
    """收尾轮的杂散非终止工具调用 → 一律 exhausted 且**不补执行**（与 live 一致）。

    live：``_final_recovery`` 仅识别终止工具调用，其余忽略（``executor_factory=
    None`` 不执行任何工具）；恢复路径不得把杂散调用当作「待补执行轮」。
    """
    run_id = "run-final-recovery-stray"
    hooks = _FakeHooks(max_iterations=1)
    _write_final_recovery_trace(
        dbm,
        run_id,
        stop_reason="tool_use",
        tool_calls=[{"id": "t9", "name": "search_bangumi", "input": {"kw": "y"}}],
    )

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.terminal_calls) == 1
    result = hooks.terminal_calls[0][0][1]
    assert result.stop_reason == "exhausted"


def test_continue_run_last_budget_round_end_turn_is_not_recovery(
    dbm: DatabaseManager,
):
    """预算内末轮 end_turn（remaining 仍 > 0）→ 不得误判为收尾轮。

    收尾轮判据是 ``remaining <= 0``（预算轮全部执行完）；预算内直接 end_turn 的
    轮次必须保持原有落库语义（mark_succeeded; end_turn）。
    """
    run_id = "run-budget-end-turn"
    hooks = _FakeHooks(max_iterations=1)
    _write_seed(dbm, run_id)
    _write_llm_chat(dbm, run_id, 0, [], stop_reason="end_turn", content="无建议")

    asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    run = dbm.agent_runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "succeeded"
    assert run["stop_reason"] == "end_turn"
    assert hooks.terminal_calls == [], "预算内 end_turn 不走 handle_terminal"


def test_continue_run_terminal_llm_error_emits_run_failed_event(dbm):
    """确定性 LLM 失败 → hooks.on_run_failed 收到失败事件；终态仍由 runtime 落库。"""
    from app.services.llm.client import LLMCallError

    run_id = "run-fail-event"
    _write_seed(dbm, run_id)
    hooks = _FakeHooks()

    async def _boom(*args, **kwargs):
        raise LLMCallError("deterministic boom", retryable=False)

    with patch.object(runtime, "_execute_continuation", _boom):
        asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.run_failed_events) == 1, "失败事件应恰好发出一次"
    event = hooks.run_failed_events[0]
    assert event.run_id == run_id
    assert event.task_type == "match"
    assert event.stop_reason == "llm_error"
    assert "deterministic boom" in event.last_error
    run = dbm.agent_runs.get_run(run_id)
    assert run is not None and run["status"] == "failed"


def test_continue_run_retryable_at_limit_emits_run_failed_event(dbm):
    """可重试失败达上限（MAX_ATTEMPTS）→ 事件携带 attempts；run 单点置 failed。"""
    from app.services.llm.client import LLMCallError

    run_id = "run-fail-event-limit"
    _write_seed(dbm, run_id)
    repo = dbm.agent_runs
    assert repo.atomic_claim(run_id)
    repo.increment_attempts(run_id, last_error="warmup-1")
    repo.increment_attempts(run_id, last_error="warmup-2")
    hooks = _FakeHooks()

    async def _boom(*args, **kwargs):
        raise LLMCallError("429 rate limited", retryable=True)

    with patch.object(runtime, "_execute_continuation", _boom):
        asyncio.run(runtime.continue_run(run_id, hooks=_hooks_of(hooks), ctx=None))

    assert len(hooks.run_failed_events) == 1
    event = hooks.run_failed_events[0]
    assert event.attempts == 3, "达上限事件应携带 attempts"
    run = repo.get_run(run_id)
    assert run is not None and run["status"] == "failed"


# ---------------------------------------------------------------------------
# 恢复复用：中断轮次的 llm_chat span（避免同轮出现「未闭合 + 重跑」两行）
# ---------------------------------------------------------------------------


def _fake_loop_completing_one_chat(*, seen: list, iteration: str = "any"):
    """构造 fake ``_invoke_loop``：走一遍 chat span 后直接返回 end_turn。"""
    from app.services.agent.loop import RunResult
    from app.services.llm.models import ChatResponse

    async def _fake(*, span_recorder, **kwargs):
        start = span_recorder.begin_chat_span()
        seen.append((start.iteration, start.span_id))
        trace.end_span(
            start.span_id,
            status="ok",
            replay_delta={
                "response": {
                    "stop_reason": "end_turn",
                    "content": "",
                    "tool_calls": [],
                }
            },
        )
        return RunResult(
            stop_reason="end_turn",
            last_response=ChatResponse(content="", stop_reason="end_turn"),
        )

    return _fake


def test_continue_run_reuses_interrupted_chat_span(dbm: DatabaseManager):
    """中断轮次的 running llm_chat 应复用（同 span_id 闭合），不新增重复行。"""
    run_id = "run-reuse-interrupted"
    _write_seed(dbm, run_id)
    stalled = trace.start_span(run_id, "llm_chat", 0, 0)

    seen: list[tuple[int, str]] = []
    with patch.object(
        runtime, "_invoke_loop", _fake_loop_completing_one_chat(seen=seen)
    ):
        asyncio.run(
            runtime.continue_run(run_id, hooks=_hooks_of(_FakeHooks()), ctx=None)
        )

    rows = [
        s
        for s in dbm.agent_runs.get_steps(run_id)
        if s["name"] == "llm_chat" and s["iteration"] == 0
    ]
    assert [s["span_id"] for s in rows] == [stalled], "应复用原 span，不得新增重复行"
    assert rows[0]["status"] == "ok"
    assert seen == [(0, stalled)]


def test_continue_run_reuses_only_interrupted_round(dbm: DatabaseManager):
    """已完整记录的轮次不被改写；仅中断轮次（iteration=1）被复用。"""
    run_id = "run-reuse-second-round"
    _write_seed(dbm, run_id)
    _write_llm_chat(
        dbm,
        run_id,
        0,
        [{"id": "t1", "name": "search_bangumi", "input": {"title": "x"}}],
        stop_reason="tool_use",
    )
    _write_tool_exec(dbm, run_id, 0, 1, "t1", '{"ok": true}')
    stalled = trace.start_span(run_id, "llm_chat", 1, 0)

    first_round = [
        s
        for s in dbm.agent_runs.get_steps(run_id)
        if s["name"] == "llm_chat" and s["iteration"] == 0
    ][0]

    seen: list[tuple[int, str]] = []
    with patch.object(
        runtime, "_invoke_loop", _fake_loop_completing_one_chat(seen=seen)
    ):
        asyncio.run(
            runtime.continue_run(run_id, hooks=_hooks_of(_FakeHooks()), ctx=None)
        )

    steps = dbm.agent_runs.get_steps(run_id)
    rows0 = [s for s in steps if s["name"] == "llm_chat" and s["iteration"] == 0]
    rows1 = [s for s in steps if s["name"] == "llm_chat" and s["iteration"] == 1]
    assert [s["span_id"] for s in rows0] == [first_round["span_id"]]
    assert [s["span_id"] for s in rows1] == [stalled]
    assert seen == [(1, stalled)]


def test_continue_run_creates_chat_span_without_interrupted_row(
    dbm: DatabaseManager,
):
    """无残留时不改变现有行为：正常新建 chat span。"""
    run_id = "run-no-residue"
    _write_seed(dbm, run_id)

    seen: list[tuple[int, str]] = []
    with patch.object(
        runtime, "_invoke_loop", _fake_loop_completing_one_chat(seen=seen)
    ):
        asyncio.run(
            runtime.continue_run(run_id, hooks=_hooks_of(_FakeHooks()), ctx=None)
        )

    rows = [
        s
        for s in dbm.agent_runs.get_steps(run_id)
        if s["name"] == "llm_chat" and s["iteration"] == 0
    ]
    assert len(rows) == 1
    assert rows[0]["span_id"] == seen[0][1]
    assert rows[0]["status"] == "ok"
