"""chat span 契约测试：单聚合 / 唯一 finalize / 轮粒度入库 / 无记录器纯聚合。

背景（消除「同一轮流被聚合两次」）：此前 recorder 的 ``wrap_stream_fn`` 与
``loop._consume_stream`` 各自建一个 ``StreamAggregator`` 并各调一次 ``finalize``，
两份 resp 一份落库、一份供控制流（结构上无同源保证）。改造后：

- loop 每轮经 ``recorder.chat_span()`` 进入 ``async with``：``feed`` 是唯一聚合点，
  ``__aexit__`` 做唯一一次 ``finalize()`` 并写 span 终态，loop 在块外读 ``span.resp``；
- ``recorder=None`` 时由 ``PlainChatSpan`` 纯聚合（不落库），控制流语义不变。

覆盖 BDD 场景：正常轮单次聚合、控制流与落库同源、异常路径 error span、
收尾轮同样按 chat span 落库、无记录器纯聚合。

「单聚合」守卫经 :func:`runtime.run`（生产接线）驱动——包装动作归属通用层，
直接调 ``loop.run_loop`` 会绕开被守卫的接线路径。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.database import DatabaseManager, set_database_manager
from app.services.agent import loop
from app.services.agent.observe.recorder import TraceRecorder
from app.services.agent.run import runtime
from app.services.agent.run.scenario import ScenarioHooks
from app.services.llm import models as llm_models
from app.services.llm.models import Message, StreamChunk, TextBlock
from tests.services.agent.stream_script import terminal_schema

_SEED = [Message(role="user", content="hello")]


@pytest.fixture
def dbm(tmp_path: Path) -> Iterator[DatabaseManager]:
    instance = DatabaseManager(str(tmp_path / "chat_span.db"))
    set_database_manager(instance)
    yield instance
    if instance._connection._conn is not None:
        instance._connection._conn.close()
    set_database_manager(None)


@pytest.fixture
def finalize_counter(monkeypatch):
    """统计全局 ``StreamAggregator.finalize`` 调用次数（跨 recorder/loop 汇总）。

    双聚合回归时该计数会翻倍（每轮 2 次），是「单聚合」契约的直接守卫。
    """
    counter = {"n": 0}
    real_finalize = llm_models.StreamAggregator.finalize

    def counting_finalize(self):
        counter["n"] += 1
        return real_finalize(self)

    monkeypatch.setattr(llm_models.StreamAggregator, "finalize", counting_finalize)
    return counter


def _text_stream(text: str, *, stop_reason: str = "end_turn"):
    """最小文本流（文本 delta + stop），供单轮控制流测试使用。"""

    async def _stream(messages, *, tools=None, tool_choice=None):
        yield StreamChunk(type="text_delta", text=text)
        yield StreamChunk(type="stop", stop_reason=stop_reason, model="m")

    return _stream


def _tool_round_stream(tool_name: str = "search_bangumi"):
    """含单个工具调用的轮（用于预算耗尽 → 收尾轮路径）。"""

    async def _stream(messages, *, tools=None, tool_choice=None):
        yield StreamChunk(type="tool_use_start", tool_use_id="t1", tool_name=tool_name)
        yield StreamChunk(
            type="tool_use_delta",
            tool_use_id="t1",
            partial_json=json.dumps({"title": "x"}),
        )
        yield StreamChunk(type="tool_use_stop", tool_use_id="t1")
        yield StreamChunk(type="stop", stop_reason="tool_use", model="m")

    return _stream


def _run_loop(*, stream_fn, recorder):
    """以脚本化流驱动 ``loop.run_loop``（loop 级测试：异常需向调用方抛出）。"""
    return asyncio.run(
        loop.run_loop(
            tools_schemas=[terminal_schema()],
            max_iterations=1,
            seed_messages=_SEED,
            stream_fn=stream_fn,
            recorder=recorder,
        )
    )


def _run_runtime(
    run_id: str,
    *,
    stream_fn,
    recorder,
    max_iterations: int = 1,
):
    """经生产接线 ``runtime.run`` 驱动一轮（recorder 装配由通用层负责）。"""

    class _Ctx:
        seed = _SEED

    captured: dict = {}

    async def handle_terminal(
        run_id, result, ctx, *, total_tokens, notification_service
    ):
        captured["result"] = result
        return "succeeded"

    def _register_tools(registry, ctx):
        """注册终止工具（收尾轮/终止判定由注册表派生）。"""
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

    hooks: ScenarioHooks = ScenarioHooks(
        task_type="match",
        register_tools=_register_tools,
        build_stream_fn=lambda thinking_level: stream_fn,
        resolve_thinking_level=lambda: "off",
        resolve_max_iterations=lambda level: max_iterations,
        handle_terminal=handle_terminal,
    )
    status = asyncio.run(
        runtime.run(
            run_id,
            hooks=hooks,
            ctx=_Ctx(),
            thinking_level="off",
            stream_fn=stream_fn,
            span_recorder=recorder,
        )
    )
    return status, captured.get("result")


def _llm_chat_steps(dbm, run_id: str) -> list[dict]:
    return [s for s in dbm.agent_runs.get_steps(run_id) if s["name"] == "llm_chat"]


# ---------------------------------------------------------------------------
# 场景 1：正常轮——单次聚合（消除双聚合）
# ---------------------------------------------------------------------------


def test_loop_round_finalizes_stream_exactly_once(dbm, finalize_counter):
    """每轮只聚合一次：生产接线（runtime）下 ``finalize`` 恰好 1 次。"""
    run_id = "run-single-finalize"
    dbm.agent_runs.create_pending(run_id, "match")
    recorder = TraceRecorder(run_id, start_iteration=0)

    status, result = _run_runtime(
        run_id, stream_fn=_text_stream("ok"), recorder=recorder
    )

    assert status == "succeeded"
    assert result.stop_reason == "end_turn"
    assert finalize_counter["n"] == 1, (
        f"每轮应只聚合一次（单 finalize），实际 {finalize_counter['n']} 次——双聚合回归"
    )


def test_loop_resp_and_persisted_span_are_same_source(dbm):
    """loop 控制流用的 resp 与 span 落库内容同源（同一份聚合结果）。"""
    run_id = "run-same-source"
    dbm.agent_runs.create_pending(run_id, "match")
    recorder = TraceRecorder(run_id, start_iteration=0)

    _, result = _run_runtime(
        run_id, stream_fn=_text_stream("同源文本"), recorder=recorder
    )

    assert result.last_response.content == "同源文本"
    steps = _llm_chat_steps(dbm, run_id)
    assert len(steps) == 1
    response = json.loads(steps[0]["replay_delta"])["response"]
    assert response["content"] == result.last_response.content
    assert response["stop_reason"] == result.last_response.stop_reason
    assert response["blocks"] == [TextBlock(text="同源文本").model_dump()]


# ---------------------------------------------------------------------------
# 场景 2：流异常——error span、不吞异常、不 finalize
# ---------------------------------------------------------------------------


def test_stream_exception_writes_error_span_without_finalize(dbm, finalize_counter):
    """流中途异常：写 error span、原异常上抛、不发生 finalize。"""
    run_id = "run-stream-error"
    dbm.agent_runs.create_pending(run_id, "match")
    recorder = TraceRecorder(run_id, start_iteration=0)

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        yield StreamChunk(type="text_delta", text="partial")
        raise RuntimeError("stream boom")

    with pytest.raises(RuntimeError, match="stream boom"):
        _run_loop(stream_fn=stream_fn, recorder=recorder)

    assert finalize_counter["n"] == 0, "异常路径不得 finalize（不落成功响应）"
    steps = _llm_chat_steps(dbm, run_id)
    assert len(steps) == 1
    assert steps[0]["status"] == "error"


def test_chat_span_write_failure_does_not_mask_stream_exception(dbm, monkeypatch):
    """观测层 best-effort：span 落库失败不得遮蔽原始流异常。"""
    run_id = "run-span-write-fail"
    dbm.agent_runs.create_pending(run_id, "match")
    recorder = TraceRecorder(run_id, start_iteration=0)

    def exploding_end_span(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.services.agent.observe.recorder.end_span", exploding_end_span
    )

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        yield StreamChunk(type="text_delta", text="partial")
        raise ValueError("stream boom")

    with pytest.raises(ValueError, match="stream boom"):
        _run_loop(stream_fn=stream_fn, recorder=recorder)


# ---------------------------------------------------------------------------
# 场景 6：收尾轮（_final_recovery）同样按 chat span 落库
# ---------------------------------------------------------------------------


def test_final_recovery_round_finalizes_once_and_persists_span(dbm, finalize_counter):
    """预算耗尽：收尾轮同样单次聚合、落 llm_chat span，iteration 顺延。"""
    run_id = "run-recovery-span"
    dbm.agent_runs.create_pending(run_id, "match")
    recorder = TraceRecorder(run_id, start_iteration=0)
    calls = {"n": 0}

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        calls["n"] += 1
        if calls["n"] == 1:
            async for chunk in _tool_round_stream()(messages):
                yield chunk
        else:
            yield StreamChunk(type="text_delta", text="收尾结论")
            yield StreamChunk(type="stop", stop_reason="end_turn", model="m")

    _, result = _run_runtime(run_id, stream_fn=stream_fn, recorder=recorder)

    assert result.stop_reason == "exhausted"
    assert result.last_response.content == "收尾结论"
    assert calls["n"] == 2, "应为预算轮 + 收尾轮各一次流调用"
    assert finalize_counter["n"] == 2, (
        f"预算轮与收尾轮各应只聚合一次，实际 {finalize_counter['n']} 次（双聚合回归）"
    )
    steps = _llm_chat_steps(dbm, run_id)
    assert [s["iteration"] for s in steps] == [0, 1], (
        "收尾轮 span 需顺延 iteration（replay 的 remaining 判据依赖它）"
    )
    assert json.loads(steps[1]["replay_delta"])["response"]["content"] == "收尾结论"


# ---------------------------------------------------------------------------
# 场景 5：recorder=None——PlainChatSpan 纯聚合，控制流不变
# ---------------------------------------------------------------------------


def test_recorder_none_plain_span_single_finalize_and_control_flow(finalize_counter):
    """无 recorder：纯聚合仍单次 finalize，终局判定与注入 recorder 时一致。"""
    result = _run_loop(stream_fn=_text_stream("无记录器"), recorder=None)

    assert result.stop_reason == "end_turn"
    assert result.text == "无记录器"
    assert finalize_counter["n"] == 1, (
        f"无 recorder 时应恰好聚合一次，实际 {finalize_counter['n']} 次"
    )


def test_recorder_none_tool_round_control_flow_unchanged():
    """无 recorder 的含工具轮：工具按既定防御语义回填，控制流不依赖落库。"""
    result = _run_loop(stream_fn=_tool_round_stream(), recorder=None)

    # 无执行器：loop 以错误块回填闭合协议（既定防御语义），预算耗尽后走收尾轮
    assert result.stop_reason == "exhausted"
    assert result.last_response.stop_reason == "tool_use"
