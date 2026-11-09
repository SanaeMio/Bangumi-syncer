"""``executor_factory`` 契约守卫：执行器必须**每轮新建**，不得复用单实例。

对应评审意见「executor_factory 没有参数，为什么不直接传 executor」——结论是
**不可替代**：执行器是一轮一具的状态机（``_order`` / ``_states`` / ``_tasks`` /
``_terminal_seen`` 每轮必须重置，且无 ``reset()``）。复用单实例会导致两类静默错误：

1. ``seq=len(self._order)`` 跨轮累加 → ``agent_steps`` 按 ``(iteration, sequence, id)``
   排序错乱；
2. ``finalize()`` 返回累计 ``_order`` 的结果 → loop 向会话重复注入历史 tool_result，
   违反「每个 tool_use 恰好一个 tool_result」的会话协议。

本文件把该结论固化为可执行断言（防止将来被改成传实例）。
"""

from __future__ import annotations

from typing import Any

from app.services.agent.execution.streaming import StreamingToolExecutor
from app.services.agent.loop import run_loop
from app.services.llm.models import Message, StreamChunk, ToolUseBlock
from tests.services.agent.stream_script import (
    raw_handler_runner,
    terminal_schema,
)

_TERMINAL = "submit_suggestion"
_LOOKUP = "lookup_subject"


def _tool_call_chunks(tid: str, args_json: str) -> list[StreamChunk]:
    """一轮内单个**非终止**工具调用的完整流事件序列。"""
    return [
        StreamChunk(type="tool_use_start", tool_use_id=tid, tool_name=_LOOKUP),
        StreamChunk(type="tool_use_delta", tool_use_id=tid, partial_json=args_json),
        StreamChunk(type="tool_use_stop", tool_use_id=tid),
    ]


def _make_executor() -> StreamingToolExecutor:
    """构造一个轮次局部的执行器（handler 返回固定结果）。"""

    async def _execute(tool_use: ToolUseBlock) -> Any:
        return {"echo": tool_use.input}

    return StreamingToolExecutor(
        run_tool=raw_handler_runner(_execute),
        is_idempotent=lambda _name: True,
        is_terminal=lambda name: name == _TERMINAL,
    )


async def _scripted_stream_for_tool_rounds(
    messages: list, *, tools=None, tool_choice=None
):
    """每轮产出 1 个非终止工具调用（据此让循环继续到预算耗尽）。"""
    tid = f"t{len(messages)}"
    for chunk in _tool_call_chunks(tid, '{"q":"x"}'):
        yield chunk
    yield StreamChunk(type="stop", stop_reason="tool_use")


async def test_executor_factory_invoked_once_per_round():
    """多轮对话下工厂调用次数恰为轮数，且每次得到**不同**实例。

    兜底收尾调用显式传 ``executor_factory=None``（不执行工具），故不计入。
    """
    rounds = 4
    built: list[StreamingToolExecutor] = []

    def _factory() -> StreamingToolExecutor:
        inst = _make_executor()
        built.append(inst)
        return inst

    result = await run_loop(
        stream_fn=_scripted_stream_for_tool_rounds,
        tools_schemas=[terminal_schema()],
        max_iterations=rounds,
        seed_messages=[Message(role="user", content="hi")],
        executor_factory=_factory,
    )

    assert result.stop_reason == "exhausted", (
        f"非终止工具轮应跑到预算耗尽，实际 stop_reason={result.stop_reason!r}"
    )
    assert len(built) == rounds, (
        f"工厂应每轮调用一次：期望 {rounds} 次，实际 {len(built)} 次"
    )
    assert len({id(inst) for inst in built}) == rounds, "工厂每次必须产出新实例"


async def test_executor_factory_result_not_cached_across_rounds():
    """loop 不得缓存工厂首次返回值复用（回归防护：改传单实例即失败）。"""
    built: list[int] = []

    def _factory() -> StreamingToolExecutor:
        built.append(len(built))
        return _make_executor()

    await run_loop(
        stream_fn=_scripted_stream_for_tool_rounds,
        tools_schemas=[terminal_schema()],
        max_iterations=3,
        seed_messages=[Message(role="user", content="hi")],
        executor_factory=_factory,
    )

    # 每轮一次调用 → 序列严格递增，无缓存复用
    assert built == [0, 1, 2], f"工厂调用序列应逐轮递增，实际 {built}"
