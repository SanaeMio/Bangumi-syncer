"""轻量循环 ``app/services/agent/loop.py`` 测试（流式注入 + 终止 + 预算）。

覆盖（LLM 调用统一经生产主路径 ``stream_fn`` 注入，工具经 ``executor_factory``）：
- end_turn / 空响应（无 tool_calls）→ stop_reason=end_turn
- assistant 聚合消息先于 tool_result（协议顺序）
- submit_suggestion 捕获即 break（stop_reason=submit_suggestion），同轮其他工具不执行（终止工具优先）
- 分段并行：整批 tool_use 经执行器按原顺序执行/回填（生产执行器内部分段并行）
- 透明预算：仅当递减后 remaining>0 才追加 ``[剩余轮次：N]``；remaining==1 起手的末轮 tool_choice=terminal
- 畸形 tool_use（handler 抛 ToolError → is_error 的 ToolResultBlock）→ 循环继续不崩溃
- max_iterations 耗尽 → stop_reason=exhausted + last_response
- 预算钩子：recorder.record_budget 每个非末轮恰好调用一次且参数为 "[剩余轮次：N]"；None 时跳过；
  末轮（递减后 remaining==0）不注入、不记录（幻影消息修复）
- 防御分支：result 非 ToolResultBlock 时记 warning
- 不直接依赖 LLMClient：LLM 调用经注入的 stream_fn（可脚本化 mock）
"""

from __future__ import annotations

import asyncio

from app.services.agent.chat_span import PlainChatSpan
from app.services.agent.execution.tools import ToolError
from app.services.agent.loop import RunResult, run_loop
from app.services.llm.models import (
    ChatResponse,
    Message,
    StreamChunk,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from tests.services.agent.stream_script import (
    raw_handler_runner,
    scripted_executor,
    scripted_stream,
)

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _resp(
    stop_reason: str, tool_calls: list[ToolUseBlock] | None = None
) -> ChatResponse:
    """构造一条 ChatResponse，blocks 携带 tool_use blocks（如有）。"""
    blocks = list(tool_calls or [])
    return ChatResponse(content="", blocks=blocks, stop_reason=stop_reason)


def _tool_use(tid: str, name: str, input_: dict | None = None) -> ToolUseBlock:
    return ToolUseBlock(id=tid, name=name, input=input_ or {})


def _seed() -> list[Message]:
    """最小种子：system + user。"""
    return [
        Message(role="system", content="你是匹配助手"),
        Message(role="user", content="请匹配：花开伊吕波剧场版"),
    ]


# 末轮/收尾提示的独立语义锚点（刻意不引用 loop 模块常量，避免同源期望值：
# 常量文案被改坏时关键词断言仍会红）。
_FINAL_ROUND_KEYWORDS = ("最后一轮", "必须调用 submit_suggestion", "不得再调用")
_FINAL_RECOVERY_KEYWORDS = (
    "最终收尾",
    "轮次预算已耗尽",
    "请立即调用 submit_suggestion",
)


def _schema(name: str, *, access: str = "read"):
    """构造测试用 ToolSchema（wire 三字段留空，仅名字/access 有意义）。"""
    from app.services.agent.protocols import ToolSchema

    return ToolSchema(name=name, description="", parameters={}, access=access)


_TERMINAL_SCHEMA = _schema("submit_suggestion", access="terminal")


def _assert_final_round_message(content: str) -> None:
    """断言末轮强化提示语义（强制提交结论 + 禁止再检索）。"""
    assert all(kw in content for kw in _FINAL_ROUND_KEYWORDS), (
        f"末轮强化提示应含语义关键词 {_FINAL_ROUND_KEYWORDS}，实际：{content!r}"
    )


def _message_text(message: Message) -> str:
    """显式收窄消息 content 为纯文本（测试前提：末轮强化提示为字符串）。"""
    content = message.content
    assert isinstance(content, str), "末轮强化提示应为纯文本"
    return content


def _message_blocks(message: Message) -> list:
    """显式收窄消息 content 为块列表（测试前提：assistant 重建为块列表）。"""
    content = message.content
    assert isinstance(content, list), "assistant content 应为块列表"
    return content


def _assert_final_recovery_message(content: str) -> None:
    """断言收尾提示语义（预算耗尽 + 立即给出结论）。"""
    assert all(kw in content for kw in _FINAL_RECOVERY_KEYWORDS), (
        f"收尾提示应含语义关键词 {_FINAL_RECOVERY_KEYWORDS}，实际：{content!r}"
    )


# ---------------------------------------------------------------------------
# 1. end_turn 终止
# ---------------------------------------------------------------------------


async def test_run_end_turn_returns_end_turn_with_text():
    end_turn = _resp("end_turn", None)
    end_turn.content = "已分析完毕，无建议"
    stream = scripted_stream([end_turn])

    result = await run_loop(
        stream_fn=stream,
        tools_schemas=[_schema("search_bangumi"), _TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert isinstance(result, RunResult)
    assert result.stop_reason == "end_turn"
    assert result.text == "已分析完毕，无建议"
    assert result.last_response is not None
    assert stream.count == 1


# ---------------------------------------------------------------------------
# 2. 空响应 / 无 tool_calls 兜底终止
# ---------------------------------------------------------------------------


async def test_run_no_tool_calls_falls_back_to_end_turn():
    # stop_reason=tool_use 但 blocks 里没有任何 ToolUseBlock
    stream = scripted_stream([_resp("tool_use", [])])

    result = await run_loop(
        stream_fn=stream,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "end_turn"
    assert stream.count == 1


# ---------------------------------------------------------------------------
# 3. assistant 聚合消息先于 tool_result
# ---------------------------------------------------------------------------


async def test_assistant_aggregate_message_precedes_tool_results():
    # 第一轮返回两个 read 工具调用，第二轮 end_turn
    stream = scripted_stream(
        [
            _resp(
                "tool_use",
                [
                    _tool_use("t1", "search_bangumi"),
                    _tool_use("t2", "get_subject_detail"),
                ],
            ),
            _resp("end_turn", None),
        ]
    )

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    # 第二轮调用时，messages 中应已含：assistant(聚合两个 tool_use) + 两条 tool_result
    second_round_messages = stream.calls[1][0]
    roles_contents = [
        (
            m.role,
            [b.type for b in m.content] if isinstance(m.content, list) else m.content,
        )
        for m in second_round_messages
    ]

    # 找到 assistant 聚合消息（content 为两个 tool_use）的索引
    assistant_idx = next(
        i
        for i, (role, content) in enumerate(roles_contents)
        if role == "assistant" and content == ["tool_use", "tool_use"]
    )
    # 找到 tool_result 的 user 消息索引
    tool_result_indices = [
        i
        for i, (role, content) in enumerate(roles_contents)
        if role == "user"
        and isinstance(content, list)
        and any(b == "tool_result" for b in content)
    ]

    assert assistant_idx != -1
    assert tool_result_indices, "应当存在 tool_result 消息"
    # 聚合 assistant 必须早于所有 tool_result
    assert all(assistant_idx < idx for idx in tool_result_indices)


# ---------------------------------------------------------------------------
# 4. submit_suggestion 捕获即 break，同轮其他工具不执行
# ---------------------------------------------------------------------------


async def test_submit_suggestion_breaks_and_captures_without_executing_others():
    submit = _tool_use(
        "ts", "submit_suggestion", {"subject_id": "49892", "reason": "标题语义相近"}
    )
    read = _tool_use("tr", "search_bangumi", {"title": "花开伊吕波"})
    # 同轮同时含 read 与 submit：终止工具优先，read 不应执行
    stream = scripted_stream([_resp("tool_use", [read, submit])])
    executed: list[str] = []

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(
            lambda tc: executed.append(tc.id) or {"ok": tc.id}
        ),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "49892", "reason": "标题语义相近"}
    # 同轮其他工具不执行：执行器 handler 不应被调用
    assert executed == []


# ---------------------------------------------------------------------------
# 5. 分段并行：整批经执行器按原顺序执行/回填（顺序保留）
# ---------------------------------------------------------------------------


async def test_segmented_parallel_passes_full_batch_in_order():
    a = _tool_use("a", "search_bangumi")
    b = _tool_use("b", "get_subject_detail")
    c = _tool_use("c", "get_related_subjects")
    stream = scripted_stream([_resp("tool_use", [a, b, c]), _resp("end_turn", None)])
    executed: list[str] = []

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(
            lambda tc: executed.append(tc.id) or {"ok": tc.id}
        ),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    # 整批按原顺序执行；结果保序回填（生产执行器内部分段并行）
    assert executed == ["a", "b", "c"]
    blocks = _collect_tool_results(stream.calls[1][0])
    assert [b.tool_use_id for b in blocks] == ["a", "b", "c"]


async def test_segmented_parallel_preserves_mixed_read_write_order():
    # 循环对 read/write 无感知（分段是执行器内部职责），这里仅验证整批顺序透传。
    # 注意：中间工具绝不能是终止工具（否则触发终止优先 break）。
    r = _tool_use("r", "search_bangumi")
    w = _tool_use("w", "check_subject")  # 代表非终止的写/读工具，仅用于验证顺序
    r2 = _tool_use("r2", "get_subject_detail")
    stream = scripted_stream([_resp("tool_use", [r, w, r2]), _resp("end_turn", None)])
    executed: list[str] = []

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(
            lambda tc: executed.append(tc.id) or {"ok": tc.id}
        ),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert executed == ["r", "w", "r2"]


# ---------------------------------------------------------------------------
# 6. 透明预算：每轮（递减后 remaining>0）追加 [剩余轮次：N]；末轮强制 terminal
# ---------------------------------------------------------------------------


async def test_transparent_budget_appends_remaining_and_forces_terminal_on_last_round():
    # 每轮都返回工具调用，迫使循环跑满 max_iterations=2（第 3 条供收尾调用）
    stream = scripted_stream(
        [
            _resp("tool_use", [_tool_use("t", "search_bangumi")]),
            _resp("tool_use", [_tool_use("t", "search_bangumi")]),
            _resp("tool_use", [_tool_use("t", "search_bangumi")]),
        ]
    )

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "exhausted"

    # 首轮 tool_choice=None；末轮（remaining==1 起手）tool_choice=terminal
    assert stream.calls[0][1]["tool_choice"] is None
    assert stream.calls[1][1]["tool_choice"] == "submit_suggestion"

    # 第二轮请求收到的 messages 末尾应携带第一轮留下的末轮强化提示
    last_msg = stream.calls[1][0][-1]
    assert last_msg.role == "user"
    _assert_final_round_message(_message_text(last_msg))


# ---------------------------------------------------------------------------
# 7. 畸形 tool_use（is_error 的 ToolResultBlock）→ 循环继续不崩溃
# ---------------------------------------------------------------------------


async def test_malformed_tool_use_is_error_block_continues_loop():
    bad = _tool_use("bad", "unknown_tool", {"foo": "bar"})
    stream = scripted_stream([_resp("tool_use", [bad]), _resp("end_turn", None)])

    def _raise(_tc: ToolUseBlock):
        # 未知工具：handler 抛 ToolError，执行器包装为 is_error 的 ToolResultBlock
        raise ToolError("未注册的工具: unknown_tool")

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(_raise),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    # 不应崩溃，应继续到下一轮并正常 end_turn
    assert result.stop_reason == "end_turn"
    # 错误块应被作为 tool_result 追加进 messages（第二轮可见）
    second_messages = stream.calls[1][0]
    error_blocks = [
        b
        for m in second_messages
        if isinstance(m.content, list)
        for b in m.content
        if isinstance(b, ToolResultBlock) and b.is_error
    ]
    assert error_blocks, "is_error 的 ToolResultBlock 应被追加"
    assert error_blocks[0].content == (
        "工具执行失败: ToolError: 未注册的工具: unknown_tool"
    )


# ---------------------------------------------------------------------------
# 8. 耗尽：max_iterations 内始终调工具不终止 → exhausted + last_response
# ---------------------------------------------------------------------------


async def test_max_iterations_exhausted_returns_last_response():
    always_tool = _resp("tool_use", [_tool_use("t", "search_bangumi")])
    # 2 轮循环 + 1 次兜底收尾调用
    stream = scripted_stream([always_tool, always_tool, always_tool])

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "exhausted"
    assert result.last_response is not None
    assert result.last_response.stop_reason == "tool_use"
    assert stream.count == 3


# ---------------------------------------------------------------------------
# 9. 预算钩子：recorder.record_budget 每轮恰好调用一次; None 时跳过
# ---------------------------------------------------------------------------


class _FakeBudgetRecorder:
    """记录 record_budget 调用，便于断言预算消息。

    满足 loop 的 ``BudgetRecorder`` 协议：chat span 用 ``PlainChatSpan`` 纯聚合
    （与 loop 的 recorder=None 分支同实现），本替身只断言 ``record_budget``，
    工具 span 相关方法为协议占位（不记录），由子类 ``_FakeToolRecorder`` 覆写。
    """

    def __init__(self) -> None:
        self.budget_calls: list[str] = []

    def record_budget(self, budget_message: str) -> None:
        self.budget_calls.append(budget_message)

    def chat_span(self) -> PlainChatSpan:
        """返回纯聚合 chat span（不落库）：loop 的聚合语义与生产实现一致。"""
        return PlainChatSpan()

    def start_tool(self, tool_use: ToolUseBlock, *, sequence: int) -> str | None:
        """协议占位：本替身不追踪工具 span（无 veto 场景不会触发）。"""
        return None

    def end_tool(
        self,
        span_id: str,
        *,
        result: ToolResultBlock | None = None,
        error: str = "",
    ) -> None:
        """协议占位：见 :meth:`start_tool`。"""


async def test_budget_record_budget_called_per_non_final_round_with_remaining():
    """非末轮记录 "[剩余轮次：N]"，末轮记录强化文案；收尾再记录一次收尾提示。"""
    recorder = _FakeBudgetRecorder()

    always_tool = _resp("tool_use", [_tool_use("t", "search_bangumi")])
    # 3 轮循环 + 1 次兜底收尾调用
    stream = scripted_stream([always_tool, always_tool, always_tool, always_tool])

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
        recorder=recorder,
    )

    # 3 轮中前 2 轮递减后 remaining>0（2、1）→ 各记录一次；末轮递减后 remaining==0
    # 的预算消息从未发给 LLM，不得记录；循环结束后收尾提示记录一次
    assert len(recorder.budget_calls) == 3
    assert recorder.budget_calls[0] == "[剩余轮次：2]"
    _assert_final_round_message(recorder.budget_calls[1])
    _assert_final_recovery_message(recorder.budget_calls[2])
    assert "[剩余轮次：0]" not in recorder.budget_calls


async def test_budget_recorder_none_runs_exhausted_path_without_error():
    """recorder=None 时跑满 max_iterations 并进入收尾调用，不抛错、正常返回 exhausted。

    覆盖两处 ``if recorder is not None`` 守卫（循环内预算记录 + `_final_recovery` 收尾
    记录）：守卫被删/失效时对 None 调 ``record_budget`` 会抛 AttributeError。
    """
    always_tool = _resp("tool_use", [_tool_use("t", "search_bangumi")])
    # 2 轮循环 + 1 次兜底收尾调用
    stream = scripted_stream([always_tool, always_tool, always_tool])

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
        recorder=None,
    )

    assert result.stop_reason == "exhausted"
    assert result.last_response is not None


async def test_budget_appended_to_messages_even_without_recorder():
    """预算消息仍追加进 messages（与 recorder 无关的领域语义）。"""
    always_tool = _resp("tool_use", [_tool_use("t", "search_bangumi")])
    stream = scripted_stream([always_tool, always_tool, always_tool])

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
        recorder=None,
    )

    # 第二轮请求收到的 messages 末尾应携带第一轮留下的末轮强化提示
    last_msg = stream.calls[1][0][-1]
    assert last_msg.role == "user"
    _assert_final_round_message(_message_text(last_msg))


async def test_budget_no_phantom_message_on_final_round():
    """起手 remaining==1 的末轮模型返回非终止工具 → 不注入/记录 [剩余轮次：0]。

    末轮执行完毕后循环随即结束，该预算消息从未发给 LLM，不应进入 messages 或 trace。
    """
    recorder = _FakeBudgetRecorder()
    observed: list[list[Message]] = []
    base = scripted_stream(
        [
            _resp("tool_use", [_tool_use("t", "search_bangumi")]),
            _resp("tool_use", [_tool_use("t", "search_bangumi")]),
        ]
    )

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        # 传引用（不 copy）：循环后续 append 会反映到同一列表，便于检查终态
        observed.append(messages)
        async for chunk in base(messages, tools=tools, tool_choice=tool_choice):
            yield chunk

    result = await run_loop(
        stream_fn=stream_fn,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=1,
        seed_messages=_seed(),
        recorder=recorder,
    )

    assert result.stop_reason == "exhausted"
    # 末轮递减后 remaining==0 不记录剩余轮次；循环后收尾提示记录一次
    assert len(recorder.budget_calls) == 1
    _assert_final_recovery_message(recorder.budget_calls[0])
    phantom = [
        m.content
        for m in observed[0]
        if m.role == "user"
        and isinstance(m.content, str)
        and m.content.startswith("[剩余轮次")
    ]
    assert phantom == [], f"末轮不应生成幻影预算消息，实际 {phantom}"


# ---------------------------------------------------------------------------
# 10. 防御分支：result 非 ToolResultBlock 时记 warning
# ---------------------------------------------------------------------------


async def test_defense_branch_logs_warning_on_non_tool_result(caplog):
    """result 非 ToolResultBlock（如 TerminalCapture 泄漏）→ 跳过并记 warning。"""
    import logging

    a = _tool_use("a", "search_bangumi")
    stream = scripted_stream([_resp("tool_use", [a]), _resp("end_turn", None)])

    class _NonToolResultExecutor:
        """槽位数一致但元素非 ToolResultBlock（模拟泄漏到 aligned 槽位）。"""

        def feed(self, chunk):
            pass

        async def finalize(self):
            return [None]

    with caplog.at_level(logging.WARNING):
        await run_loop(
            stream_fn=stream,
            executor_factory=_NonToolResultExecutor,
            tools_schemas=[_TERMINAL_SCHEMA],
            max_iterations=1,
            seed_messages=_seed(),
        )

    # 应打出 warning 日志
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("tool result 缺失或非 ToolResultBlock" in r.message for r in warnings)


# ---------------------------------------------------------------------------
# 12. 终止分类：空壳响应 → llm_error；max_tokens → max_tokens
# ---------------------------------------------------------------------------


async def test_run_empty_shell_response_returns_llm_error():
    """空壳响应（stop_reason=""、无 blocks、content 为空）→ stop_reason='llm_error'（不再 end_turn）。"""
    # 模拟 LLMCallError 后被包装成的空壳（client 不再返回此物，但 loop 仍需防御）
    stream = scripted_stream([ChatResponse(content="", blocks=[], stop_reason="")])

    result = await run_loop(
        stream_fn=stream,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "llm_error"


async def test_run_max_tokens_response_returns_max_tokens():
    """无 tool_calls 且 stop_reason='max_tokens' → stop_reason='max_tokens'。"""
    stream = scripted_stream(
        [ChatResponse(content="truncated...", blocks=[], stop_reason="max_tokens")]
    )

    result = await run_loop(
        stream_fn=stream,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "max_tokens"


async def test_run_end_turn_unchanged():
    """stop_reason='end_turn' 路径不变。"""
    end_turn = _resp("end_turn", None)
    end_turn.content = "已分析完毕"
    stream = scripted_stream([end_turn])

    result = await run_loop(
        stream_fn=stream,
        tools_schemas=[_schema("search_bangumi"), _TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "end_turn"
    assert result.text == "已分析完毕"


async def test_run_tool_calls_path_unchanged():
    """有 tool_calls 的路径不受影响（stop_reason='tool_use' 含 tool_calls）。"""
    stream = scripted_stream(
        [
            _resp("tool_use", [_tool_use("t1", "search_bangumi")]),
            _resp("end_turn", None),
        ]
    )

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "end_turn"
    assert stream.count == 2


# ---------------------------------------------------------------------------
# 13. loop.py 不 import 任何 trace 符号
# ---------------------------------------------------------------------------


def test_loop_no_trace_imports():
    """loop.py 不应 import trace 相关符号（零 span 逻辑）。"""
    import ast

    with open("app/services/agent/loop.py") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "trace" not in alias.name, (
                    f"loop.py 不应 import trace 模块: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module and "trace" in node.module:
                raise AssertionError(
                    f"loop.py 不应 from ... import trace: {node.module}"
                )


# ---------------------------------------------------------------------------
# N. 思考模型兼容：assistant 聚合消息保留 Text/Thinking 块（随 tool_use 回传）
# ---------------------------------------------------------------------------


async def test_assistant_message_preserves_text_and_thinking_blocks():
    """响应含 text+thinking+tool_use 时，assistant 消息保留全部块（顺序不变）。

    思考模型（Anthropic thinking 模式 / DeepSeek pro）要求 thinking 块随
    tool_use 在后续请求回传，否则真实端点 400。
    """
    stream = scripted_stream(
        [
            ChatResponse(
                content="先搜索",
                blocks=[
                    TextBlock(text="先搜索"),
                    ThinkingBlock(thinking="我需要先搜索", signature="sig-1"),
                    _tool_use("t1", "search_bangumi"),
                ],
                stop_reason="tool_use",
            ),
            _resp("end_turn", None),
        ]
    )

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assistant_msg = next(m for m in stream.calls[1][0] if m.role == "assistant")
    content = assistant_msg.content
    assert isinstance(content, list)  # 富内容路径：assistant 消息为块列表
    types = [b.type for b in content]
    assert types == ["text", "thinking", "tool_use"]
    thinking_block = next(b for b in content if isinstance(b, ThinkingBlock))
    assert thinking_block.signature == "sig-1"


# ---------------------------------------------------------------------------
# 14. 末轮强化提示：remaining==1 的预算消息改为强化文案；非末轮不含
# ---------------------------------------------------------------------------


async def test_final_round_budget_message_is_strengthened():
    """末轮（remaining==1）注入的预算消息含「最后一轮 / submit_suggestion / 不得再检索」。"""
    always_tool = _resp("tool_use", [_tool_use("t", "search_bangumi")])
    # max_iterations=2 → 2 轮循环 + 1 次兜底收尾调用
    stream = scripted_stream([always_tool, always_tool, always_tool])

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
    )

    # max_iterations=2：第一轮递减后 remaining==1 → 注入强化提示，第二轮请求可见
    last_msg = stream.calls[1][0][-1]
    assert last_msg.role == "user"
    assert isinstance(last_msg.content, str)
    assert "最后一轮" in last_msg.content
    assert "submit_suggestion" in last_msg.content
    assert "不得再" in last_msg.content


async def test_non_final_round_budget_message_not_strengthened():
    """非末轮（remaining>1）仍是朴素文案，不含强化提示语义。"""
    always_tool = _resp("tool_use", [_tool_use("t", "search_bangumi")])
    # max_iterations=3 → 3 轮循环 + 1 次兜底收尾调用
    stream = scripted_stream([always_tool, always_tool, always_tool, always_tool])

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    # 第一轮递减后 remaining==2 → 第二轮请求末尾是朴素预算消息
    second_last = stream.calls[1][0][-1]
    assert second_last.role == "user"
    assert second_last.content == "[剩余轮次：2]"
    assert "最后一轮" not in second_last.content


# ---------------------------------------------------------------------------
# 15. 兜底收尾调用（for 循环自然结束后最多一次）
# ---------------------------------------------------------------------------


def _exhausting_stream(max_iterations: int, extra: list[ChatResponse]):
    """构造 stream_fn：前 max_iterations 轮返回非终止工具，之后按 extra 依次返回。"""
    responses = [
        _resp("tool_use", [_tool_use("t", "search_bangumi")])
        for _ in range(max_iterations)
    ] + list(extra)
    return scripted_stream(responses)


async def test_exhausted_final_recovery_submits_suggestion():
    """耗尽后收尾调用返回 submit → stop_reason=submit_suggestion 且参数正确。"""
    submit = _tool_use(
        "s", "submit_suggestion", {"subject_id": "49892", "reason": "收尾确定"}
    )
    stream = _exhausting_stream(2, [_resp("tool_use", [submit])])

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "49892", "reason": "收尾确定"}
    assert result.last_response is not None
    # last_response 为收尾响应（含 submit tool_use）
    assert [
        b.id for b in result.last_response.blocks if isinstance(b, ToolUseBlock)
    ] == ["s"]
    # 收尾请求的 messages 末尾为收尾提示
    _assert_final_recovery_message(stream.calls[2][0][-1].content)


async def test_exhausted_final_recovery_still_no_submit_returns_exhausted():
    """耗尽后收尾仍不提交 → exhausted，last_response 为收尾响应。"""
    stream = _exhausting_stream(
        2, [_resp("tool_use", [_tool_use("t", "search_bangumi")])]
    )

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "exhausted"
    assert result.last_response is not None
    assert result.last_response.stop_reason == "tool_use"
    # last_response 为收尾响应（仍为 search_bangumi 工具调用）
    assert [
        b.id for b in result.last_response.blocks if isinstance(b, ToolUseBlock)
    ] == ["t"]


async def test_exhausted_final_recovery_exception_returns_exhausted_not_crash():
    """收尾调用抛异常 → best-effort 返回 exhausted（last_response 保持循环内最后一次）。"""
    base = _exhausting_stream(
        2, [_resp("tool_use", [_tool_use("t", "search_bangumi")])]
    )
    call_count = {"n": 0}

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        call_count["n"] += 1
        if call_count["n"] <= 2:  # max_iterations=2 的循环内两轮
            async for chunk in base(messages, tools=tools, tool_choice=tool_choice):
                yield chunk
        else:
            raise RuntimeError("收尾调用失败")

    result = await run_loop(
        stream_fn=stream_fn,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "exhausted"
    assert result.last_response is not None
    assert result.last_response.stop_reason == "tool_use"
    # last_response 保持循环内最后一次响应（而非收尾异常）
    assert [
        b.id for b in result.last_response.blocks if isinstance(b, ToolUseBlock)
    ] == ["t"]


async def test_exhausted_final_recovery_tools_only_terminal_schema():
    """收尾调用仅传 terminal 工具的 schema，且 tool_choice=terminal；不执行其他工具。"""
    recovery = _resp(
        "tool_use", [_tool_use("s", "submit_suggestion", {"subject_id": "1"})]
    )
    stream = _exhausting_stream(2, [recovery])
    executed: list[str] = []
    schemas = [
        _schema("search_bangumi"),
        _schema("submit_suggestion", access="terminal"),
    ]

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(
            lambda tc: executed.append(tc.id) or {"ok": tc.id}
        ),
        tools_schemas=schemas,
        max_iterations=2,
        seed_messages=_seed(),
    )

    # stream.calls[2] 为收尾调用（wire 形态：仅三字段）
    assert stream.calls[2][1]["tools"] == [
        {"name": "submit_suggestion", "description": "", "parameters": {}}
    ]
    assert stream.calls[2][1]["tool_choice"] == "submit_suggestion"
    # 收尾不执行任何非终止工具：handler 只被循环内两轮调用
    assert len(executed) == 2


async def test_exhausted_final_recovery_called_exactly_once():
    """收尾调用恰好一次：stream_fn 总调用数 = max_iterations + 1。"""
    stream = _exhausting_stream(
        2, [_resp("tool_use", [_tool_use("t", "search_bangumi")])]
    )

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
    )

    assert stream.count == 3  # 2 轮循环 + 1 次收尾


async def test_exhausted_final_recovery_message_recorded_via_budget_channel():
    """收尾提示须经 recorder.record_budget 记录（恢复重放一致性）。"""
    recorder = _FakeBudgetRecorder()
    stream = _exhausting_stream(
        2, [_resp("tool_use", [_tool_use("t", "search_bangumi")])]
    )

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
        recorder=recorder,
    )

    assert any(
        all(kw in c for kw in _FINAL_RECOVERY_KEYWORDS) for c in recorder.budget_calls
    ), f"收尾提示须经预算通道记录，实际 {recorder.budget_calls!r}"


# ---------------------------------------------------------------------------
# 16. 终止提交软护栏（veto）：一次暂缓 + tool_result 注入 + recorder 落 span
# ---------------------------------------------------------------------------


class _FakeToolRecorder(_FakeBudgetRecorder):
    """记录 start_tool / end_tool 调用（veto 伪执行落 span 断言用）。"""

    def __init__(self) -> None:
        super().__init__()
        self.started: list[tuple[str, ToolUseBlock, int]] = []
        self.ended: list[tuple[str, ToolResultBlock | None, str]] = []

    def start_tool(self, tool_use: ToolUseBlock, *, sequence: int) -> str:
        span_id = f"span-{len(self.started)}"
        self.started.append((span_id, tool_use, sequence))
        return span_id

    def end_tool(
        self,
        span_id: str,
        *,
        result: ToolResultBlock | None = None,
        error: str = "",
    ) -> None:
        self.ended.append((span_id, result, error))


def _collect_tool_results(messages: list[Message]) -> list[ToolResultBlock]:
    return [
        b
        for m in messages
        if isinstance(m.content, list)
        for b in m.content
        if isinstance(b, ToolResultBlock)
    ]


async def test_veto_defers_first_terminal_and_injects_hint_tool_result():
    """veto 返回提示 → 首次 submit 不终止，注入 tool_result，下一轮放行。"""
    first = _tool_use(
        "ts1", "submit_suggestion", {"subject_id": "1", "reason": "暂推荐"}
    )
    second = _tool_use(
        "ts2", "submit_suggestion", {"subject_id": "2", "reason": "确定"}
    )
    stream = scripted_stream([_resp("tool_use", [first]), _resp("tool_use", [second])])
    executed: list[str] = []
    veto_inputs: list[dict] = []

    def _veto(args: dict) -> str | None:
        veto_inputs.append(args)
        return None if args.get("subject_id") == "2" else "请再核对"

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(
            lambda tc: executed.append(tc.id) or {"ok": tc.id}
        ),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
        veto_terminal=_veto,
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "2", "reason": "确定"}
    # 首次 submit 被暂缓：其他工具不执行
    assert executed == []
    # 第二轮请求携带注入的 veto tool_result（配对 terminal tool_use）
    blocks = _collect_tool_results(stream.calls[1][0])
    veto_blocks = [b for b in blocks if b.tool_use_id == "ts1"]
    assert veto_blocks, "应注入 terminal tool_use 配对的 tool_result"
    assert veto_blocks[0].content == "请再核对"
    assert veto_blocks[0].is_error is False
    # 首次 submit 被询问过（第二次因「已 veto 过」直接放行，见另一用例）
    assert [v["subject_id"] for v in veto_inputs] == ["1"]


async def test_veto_happens_at_most_once_per_run():
    """veto 只拦一次：第二次 terminal 不再调 veto、直接终止。"""
    submit = _tool_use(
        "s", "submit_suggestion", {"subject_id": "1", "reason": "暂推荐"}
    )
    stream = scripted_stream([_resp("tool_use", [submit]), _resp("tool_use", [submit])])
    veto_calls: list[dict] = []

    def _veto(args: dict) -> str | None:
        veto_calls.append(args)
        return "暂缓"

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
        veto_terminal=_veto,
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "1", "reason": "暂推荐"}
    # veto 仅调用一次（第二次 terminal 因「已 veto 过」直接放行，不再询问）
    assert len(veto_calls) == 1
    assert stream.count == 2


async def test_veto_not_applied_on_final_round():
    """末轮（remaining==1 起手）强收尾优先：不 veto，直接终止。"""
    submit = _tool_use(
        "s", "submit_suggestion", {"subject_id": "1", "reason": "暂推荐"}
    )
    stream = scripted_stream([_resp("tool_use", [submit])])
    veto_calls: list[dict] = []

    def _veto(args: dict) -> str | None:
        veto_calls.append(args)
        return "暂缓"

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=1,
        seed_messages=_seed(),
        veto_terminal=_veto,
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "1", "reason": "暂推荐"}
    # 末轮不 veto：回调根本不被调用
    assert veto_calls == []
    assert stream.count == 1


async def test_veto_none_behaves_like_current_behavior():
    """veto_terminal=None 时行为与现状一致：submit 立即终止。"""
    submit = _tool_use(
        "s", "submit_suggestion", {"subject_id": "1", "reason": "暂推荐"}
    )
    stream = scripted_stream([_resp("tool_use", [submit])])

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
        veto_terminal=None,
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "1", "reason": "暂推荐"}
    assert stream.count == 1


async def test_veto_records_injected_tool_result_span_for_replay():
    """veto 伪执行须经 recorder 落 tool_execute span（replay 一致性）。"""
    first = _tool_use("ts1", "submit_suggestion", {"subject_id": "1"})
    second = _tool_use("ts2", "submit_suggestion", {"subject_id": "2"})
    stream = scripted_stream([_resp("tool_use", [first]), _resp("tool_use", [second])])
    recorder = _FakeToolRecorder()

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
        recorder=recorder,
        veto_terminal=lambda args: "请再核对",
    )

    # 仅 veto 伪执行落 span（terminal 分支不经执行器 handler）
    assert len(recorder.started) == 1
    _span_id, tool_use, sequence = recorder.started[0]
    assert tool_use.id == "ts1"
    # sequence 与工具在 tool_calls 中的下标一致
    assert sequence == 0
    assert len(recorder.ended) == 1
    ended_result = recorder.ended[0][1]
    assert ended_result is not None
    assert ended_result.tool_use_id == "ts1"
    assert ended_result.content == "请再核对"
    assert ended_result.is_error is False


async def test_veto_multi_tool_round_injects_paired_results_for_all_tool_uses():
    """多工具轮 veto 暂缓：同轮每条 tool_use 均须有配对 tool_result。

    terminal 工具注入 hint 文案，其余工具注入占位（不执行）；否则下一轮真实
    OpenAI/Anthropic 端点会因悬空 tool_use 报 400。
    """
    search = _tool_use("t1", "search_bangumi", {"title": "foo"})
    submit = _tool_use("t2", "submit_suggestion", {"subject_id": "1"})
    second = _tool_use("t3", "submit_suggestion", {"subject_id": "2"})
    stream = scripted_stream(
        [_resp("tool_use", [search, submit]), _resp("tool_use", [second])]
    )
    executed: list[str] = []

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(
            lambda tc: executed.append(tc.id) or {"ok": tc.id}
        ),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
        veto_terminal=lambda args: "请再核对",
    )

    assert result.stop_reason == "submit_suggestion"
    # 暂缓轮其他工具不执行
    assert executed == []
    # 第二轮请求前：assistant 同轮含两条 tool_use，二者都必须有配对 tool_result
    _assistant, results = _last_assistant_tool_use_and_results(stream.calls[1][0])
    assert _assistant == ["t1", "t2"]
    assert set(results) >= {"t1", "t2"}, (
        f"同轮每条 tool_use 须有配对 tool_result，实际 {set(results)}"
    )
    assert results["t2"].content == "请再核对"
    assert results["t2"].is_error is False
    assert results["t1"].content == "skipped: deferred by veto"
    assert results["t1"].is_error is False


def _last_assistant_tool_use_and_results(
    messages: list[Message],
) -> tuple[list[str], dict[str, ToolResultBlock]]:
    """取最后一条 assistant 消息的 tool_use id 列表 + 全部 tool_result（按 id）。"""
    assistant_msg = next(m for m in reversed(messages) if m.role == "assistant")
    tool_use_ids = [b.id for b in assistant_msg.content if isinstance(b, ToolUseBlock)]
    results = {
        b.tool_use_id: b
        for m in messages
        if isinstance(m.content, list)
        for b in m.content
        if isinstance(b, ToolResultBlock)
    }
    return tool_use_ids, results


# ---------------------------------------------------------------------------
# 16b. stop_reason 终局归一化（loop.run_loop 与 runtime.continue_run 共用判据）
# ---------------------------------------------------------------------------


def test_normalize_stop_reason_end_turn_is_terminal():
    from app.services.agent.loop import normalize_stop_reason

    assert (
        normalize_stop_reason("end_turn", has_tool_calls=False, blocks=[], content="")
        == "end_turn"
    )


def test_normalize_stop_reason_with_tool_calls_is_not_terminal():
    from app.services.agent.loop import normalize_stop_reason

    assert (
        normalize_stop_reason("tool_use", has_tool_calls=True, blocks=[], content="")
        is None
    )


def test_normalize_stop_reason_max_tokens_without_tools_is_terminal():
    from app.services.agent.loop import normalize_stop_reason

    assert (
        normalize_stop_reason(
            "max_tokens", has_tool_calls=False, blocks=[], content="partial"
        )
        == "max_tokens"
    )


def test_normalize_stop_reason_empty_shell_is_llm_error():
    from app.services.agent.loop import normalize_stop_reason

    assert (
        normalize_stop_reason("", has_tool_calls=False, blocks=[], content="")
        == "llm_error"
    )


def test_normalize_stop_reason_unknown_with_content_is_end_turn():
    from app.services.agent.loop import normalize_stop_reason

    assert (
        normalize_stop_reason(
            "stop_sequence", has_tool_calls=False, blocks=[], content="done"
        )
        == "end_turn"
    )


# ---------------------------------------------------------------------------
# 17. 流式路径（stream_fn + executor_factory）：提前执行 / 保序回填 / 终止语义
# ---------------------------------------------------------------------------


def _stream_start(tid: str, name: str) -> StreamChunk:
    return StreamChunk(type="tool_use_start", tool_use_id=tid, tool_name=name)


def _stream_delta(tid: str, partial: str) -> StreamChunk:
    return StreamChunk(type="tool_use_delta", tool_use_id=tid, partial_json=partial)


def _stream_stop(tid: str) -> StreamChunk:
    return StreamChunk(type="tool_use_stop", tool_use_id=tid)


def _stream_end_turn() -> StreamChunk:
    return StreamChunk(type="stop", stop_reason="end_turn")


def _streaming_executor(events: list, *, gate: asyncio.Event | None = None):
    """构造 StreamingToolExecutor：read_* 幂等（提前），submit_suggestion terminal。"""

    async def execute_fn(tool_use):
        events.append(("exec", tool_use.id))
        if gate is not None:
            await gate.wait()
        return {"echo": tool_use.name}

    # 复用共享辅助的默认调度判据（read* 幂等、submit_suggestion terminal）
    return scripted_executor(execute_fn)()


async def test_scripted_executor_passes_through_tool_result_block():
    """辅助契约：handler 直传 ToolResultBlock 时按原样回填（内容/is_error 不被包装）。"""
    a = _tool_use("a", "search_bangumi")
    stream = scripted_stream([_resp("tool_use", [a]), _resp("end_turn", None)])
    exact = ToolResultBlock(tool_use_id="a", content="exact", is_error=False)

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: exact),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "end_turn"
    blocks = _collect_tool_results(stream.calls[1][0])
    assert len(blocks) == 1
    assert blocks[0].content == "exact"
    assert blocks[0].is_error is False


async def test_stream_path_starts_idempotent_tool_before_stream_ends():
    """幂等工具在停点到达即执行（与后续事件生成重叠）：exec 早于后续生成标记。"""
    import asyncio

    events: list = []

    async def _round1():
        yield _stream_start("a", "read_a")
        yield _stream_delta("a", '{"title": "x"}')
        yield _stream_stop("a")
        await asyncio.sleep(0)  # 让提前任务获得调度
        events.append(("stream", "after_a_stop"))
        yield _stream_start("b", "read_b")
        yield _stream_delta("b", "{}")
        yield _stream_stop("b")
        yield StreamChunk(type="stop", stop_reason="tool_use")

    state = {"round": 0}

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        state["round"] += 1
        if state["round"] == 1:
            async for c in _round1():
                yield c
        else:
            yield _stream_end_turn()

    def factory():
        return _streaming_executor(events)

    result = await run_loop(
        stream_fn=stream_fn,
        executor_factory=factory,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "end_turn"
    # a 的提前执行发生在 b 事件生成之前
    assert events.index(("exec", "a")) < events.index(("stream", "after_a_stop"))
    assert ("exec", "b") in events


async def test_stream_path_backfills_ordered_tool_results_and_blocks():
    """流式路径：tool_result 保序回填；assistant 消息保留 thinking/tool_use blocks。"""
    calls: list[list[Message]] = []

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        calls.append(list(messages))
        if len(calls) == 1:
            yield StreamChunk(type="thinking_delta", thinking="先搜索", signature="s1")
            yield StreamChunk(type="text_delta", text="检索中")
            yield _stream_start("a", "read_a")
            yield _stream_delta("a", "{}")
            yield _stream_stop("a")
            yield _stream_start("b", "read_b")
            yield _stream_delta("b", "{}")
            yield _stream_stop("b")
            yield StreamChunk(type="stop", stop_reason="tool_use")
        else:
            yield _stream_end_turn()

    def factory():
        return _streaming_executor([])

    result = await run_loop(
        stream_fn=stream_fn,
        executor_factory=factory,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "end_turn"
    assistant = next(m for m in calls[1] if m.role == "assistant")
    assistant_content = assistant.content
    assert isinstance(assistant_content, list)  # 富内容路径：assistant 消息为块列表
    assert [b.type for b in assistant_content] == [
        "thinking",
        "text",
        "tool_use",
        "tool_use",
    ]

    blocks = _collect_tool_results(calls[1])
    assert [b.tool_use_id for b in blocks] == ["a", "b"], "tool_result 应按原始顺序回填"
    assert all(not b.is_error for b in blocks)


async def test_stream_path_terminal_captured_and_other_tools_not_executed():
    """终止工具捕获即 break；terminal 不执行 handler，同轮其他工具不执行。"""
    events: list = []

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        yield _stream_start("s", "submit_suggestion")
        yield _stream_delta("s", '{"subject_id": "1", "reason": "ok"}')
        yield _stream_stop("s")
        yield _stream_start("r", "read_r")
        yield _stream_delta("r", "{}")
        yield _stream_stop("r")
        yield StreamChunk(type="stop", stop_reason="tool_use")

    def factory():
        return _streaming_executor(events)

    result = await run_loop(
        stream_fn=stream_fn,
        executor_factory=factory,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "1", "reason": "ok"}
    assert events == [], "terminal 轮不得执行任何工具（含后续 read）"


async def test_stream_path_malformed_json_error_and_loop_continues():
    """畸形参数 → error result 回填，循环继续（不崩溃）到下一轮 end_turn。"""
    calls: list[list[Message]] = []

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        calls.append(list(messages))
        if len(calls) == 1:
            yield _stream_start("bad", "read_bad")
            yield _stream_delta("bad", "{not json")
            yield _stream_stop("bad")
            yield StreamChunk(type="stop", stop_reason="tool_use")
        else:
            yield _stream_end_turn()

    def factory():
        return _streaming_executor([])

    result = await run_loop(
        stream_fn=stream_fn,
        executor_factory=factory,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "end_turn"
    blocks = _collect_tool_results(calls[1])
    assert len(blocks) == 1 and blocks[0].is_error is True
    assert "JSON" in blocks[0].content


async def test_stream_path_pads_missing_slots_to_close_protocol(caplog):
    """执行器槽位不足且顺序破坏 → 补齐：每条 tool_use 都有配对 tool_result。

    流式执行器 ``finalize`` 因协议破坏返回少于 tool_calls 的槽位时，若直接 ``zip``
    截断，多出的 tool_use 会缺少 tool_result 闭合，下一轮真实端点可能 400。
    此处只返回 b 的结果（a 缺失且顺序破坏），断言 a 以 slot mismatch 错误块回填。
    """
    import logging

    calls: list[list[Message]] = []

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        calls.append(list(messages))
        if len(calls) == 1:
            yield _stream_start("a", "read_a")
            yield _stream_delta("a", "{}")
            yield _stream_stop("a")
            yield _stream_start("b", "read_b")
            yield _stream_delta("b", "{}")
            yield _stream_stop("b")
            yield StreamChunk(type="stop", stop_reason="tool_use")
        else:
            yield _stream_end_turn()

    class _ShortFinalizeExecutor:
        def feed(self, chunk):
            pass

        async def finalize(self):
            # 少一个槽位 + 顺序破坏：只给 b 的结果（a 的槽位缺失）
            return [ToolResultBlock(tool_use_id="b", content="res-b", is_error=False)]

    result = await run_loop(
        stream_fn=stream_fn,
        executor_factory=_ShortFinalizeExecutor,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "end_turn"
    assistant = next(m for m in calls[1] if m.role == "assistant")
    tool_use_ids = [b.id for b in _message_blocks(assistant) if b.type == "tool_use"]
    assert tool_use_ids == ["a", "b"]

    blocks = _collect_tool_results(calls[1])
    by_id = {b.tool_use_id: b for b in blocks}
    assert set(by_id) == {"a", "b"}, "每条 tool_use 都应有配对 tool_result"
    assert by_id["b"].content == "res-b"
    assert by_id["a"].is_error is True
    assert "slot mismatch" in by_id["a"].content

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("槽位数" in r.getMessage() for r in warnings)


async def test_stream_path_budget_and_recovery_use_stream():
    """耗尽后收尾也走流式：收尾返回 terminal → submit_suggestion。"""
    state = {"round": 0}

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        state["round"] += 1
        if state["round"] <= 2:
            # 循环内两轮均返回非终止工具（迫使耗尽）
            yield _stream_start("a", "read_a")
            yield _stream_delta("a", "{}")
            yield _stream_stop("a")
            yield StreamChunk(type="stop", stop_reason="tool_use")
        else:
            # 收尾轮：submit
            yield _stream_start("s", "submit_suggestion")
            yield _stream_delta("s", '{"subject_id": "9", "reason": "收尾"}')
            yield _stream_stop("s")
            yield StreamChunk(type="stop", stop_reason="tool_use")

    def factory():
        return _streaming_executor([])

    result = await run_loop(
        stream_fn=stream_fn,
        executor_factory=factory,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=2,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "9", "reason": "收尾"}


async def test_run_requires_stream_fn():
    """不提供 stream_fn → ValueError（显式契约）。"""
    import pytest

    with pytest.raises(ValueError, match="stream_fn"):
        await run_loop(
            tools_schemas=[_TERMINAL_SCHEMA],
            max_iterations=1,
            seed_messages=_seed(),
        )


# ---------------------------------------------------------------------------
# 流中途异常：提前执行任务必须收敛，异常向上传播
# ---------------------------------------------------------------------------


async def test_stream_exception_converges_early_tasks_and_propagates():
    """stream_fn 在幂等停点后抛异常 → 异常传播；提前任务被 finalize 收敛、无泄漏。"""
    import pytest

    from app.services.agent.execution.streaming import StreamingToolExecutor

    events: list = []
    created: list[StreamingToolExecutor] = []
    finalized = {"called": False}

    async def execute_fn(tool_use):
        events.append(("exec", tool_use.id))
        return tool_use.id

    class _SpyExecutor(StreamingToolExecutor):
        async def finalize(self):
            finalized["called"] = True
            return await super().finalize()

    def factory():
        ex = _SpyExecutor(
            run_tool=raw_handler_runner(execute_fn),
            is_idempotent=lambda name: name.startswith("read"),
            is_terminal=lambda name: name == "submit_suggestion",
        )
        created.append(ex)
        return ex

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        yield _stream_start("a", "read_a")
        yield _stream_delta("a", "{}")
        yield _stream_stop("a")  # 提前启动 a
        await asyncio.sleep(0)  # 让提前任务获得调度
        raise RuntimeError("stream boom")

    with pytest.raises(RuntimeError, match="stream boom"):
        await run_loop(
            stream_fn=stream_fn,
            executor_factory=factory,
            tools_schemas=[_TERMINAL_SCHEMA],
            max_iterations=2,
            seed_messages=_seed(),
        )

    # 异常路径必须 await finalize 收敛提前任务（而非让其在后台变孤儿）
    assert finalized["called"] is True
    assert ("exec", "a") in events, "提前任务应已执行"
    ex = created[0]
    assert all(t.done() for t in ex._tasks), "提前任务应已收敛，无 pending 泄漏"
    assert ex._states["a"].result is not None, "提前任务结果应已回填"


# ---------------------------------------------------------------------------
# 流中途异常的收敛块健壮性：BaseException 兜底 + 取消安全
# ---------------------------------------------------------------------------


class _FinalizeBaseError(BaseException):
    """模拟 finalize 抛出的取消类/致命异常（非 Exception 子类）。"""


async def test_stream_exception_not_masked_when_finalize_raises_base_exception():
    """finalize 抛 BaseException（非 Exception）→ 仍向外抛原始流异常，不被掩盖。"""
    import pytest

    class _FatalFinalizeExecutor:
        def feed(self, chunk):
            pass

        async def finalize(self):
            raise _FinalizeBaseError("finalize cancelled")

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        yield _stream_start("a", "read_a")
        yield _stream_delta("a", "{}")
        yield _stream_stop("a")
        raise RuntimeError("stream boom")

    with pytest.raises(RuntimeError, match="stream boom"):
        await run_loop(
            stream_fn=stream_fn,
            executor_factory=_FatalFinalizeExecutor,
            tools_schemas=[_TERMINAL_SCHEMA],
            max_iterations=2,
            seed_messages=_seed(),
        )


async def test_cancel_during_convergence_keeps_finalize_running_in_background(
    caplog,
):
    """收敛期间外层被取消 → 原异常传播，shield 保护的 finalize 完成，且告警可辨识取消。

    ``CancelledError`` 的 ``str(e)`` 为空，故告警必须打印异常类型名（CancelledError），
    否则日志尾部只剩冒号空白，排障无法区分「被取消」与「finalize 真失败」。

    ``loop.py`` 使用 stdlib ``logging.getLogger``，故此处用 ``caplog`` 捕获。
    """
    import logging

    import pytest

    finalize_started = asyncio.Event()
    finalize_done = asyncio.Event()
    release = asyncio.Event()

    class _SlowFinalizeExecutor:
        def feed(self, chunk):
            pass

        async def finalize(self):
            finalize_started.set()
            await release.wait()  # 模拟收敛动作耗时，期间外层被取消
            finalize_done.set()
            return []

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        yield _stream_start("a", "read_a")
        yield _stream_delta("a", "{}")
        yield _stream_stop("a")
        raise RuntimeError("stream boom")

    task = asyncio.create_task(
        run_loop(
            stream_fn=stream_fn,
            executor_factory=_SlowFinalizeExecutor,
            tools_schemas=[_TERMINAL_SCHEMA],
            max_iterations=2,
            seed_messages=_seed(),
        )
    )
    await asyncio.wait_for(finalize_started.wait(), timeout=1)
    task.cancel()

    # 原异常（而非取消）向外传播
    with pytest.raises(RuntimeError, match="stream boom"):
        await task

    # shield 保证收敛动作不被取消打断：放行后 finalize 在后台完成
    release.set()
    await asyncio.wait_for(finalize_done.wait(), timeout=1)
    assert finalize_done.is_set()

    # 取消竞态下告警必须打印异常类型名，不能是空的 str(CancelledError)
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "收敛提前执行任务未完成" in r.getMessage()
    ]
    assert warnings, "收敛失败不应静默吞掉，必须记 warning"
    assert any("CancelledError" in msg for msg in warnings), (
        "取消竞态告警应打印异常类型名 CancelledError（str(CancelledError) 为空），"
        f"实际：{warnings}"
    )


# ---------------------------------------------------------------------------
# 18. seed_messages 类型契约：Sequence 宽化 + list() 快照隔离
# ---------------------------------------------------------------------------


async def test_run_accepts_tuple_seed_messages():
    """契约锁定：``seed_messages`` 标注 ``Sequence``，tuple 亦可传入且不被改写。

    类型标注不影响运行时，本测试改前改后均绿，仅锁定「宽化为 Sequence」的契约。
    """
    seed = tuple(_seed())

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        yield _stream_end_turn()

    result = await run_loop(
        stream_fn=stream_fn,
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=seed,
    )

    assert result.stop_reason == "end_turn"
    # 调用方 tuple 未被改写（长度不变）
    assert len(seed) == 2


async def test_run_does_not_mutate_caller_seed_list():
    """快照语义：loop 内部追加消息（assistant/tool_result/预算）不得污染调用方 seed。

    构造一轮工具调用 + 一轮终结：内部会向 messages append 多条消息。若入口未做
    ``list()`` 浅拷贝，调用方 list 将被追加而长度变化。
    """
    seed = _seed()
    original_len = len(seed)
    stream = scripted_stream(
        [
            _resp("tool_use", [_tool_use("t1", "search_bangumi")]),
            _resp("end_turn", None),
        ]
    )

    result = await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=seed,
    )

    assert result.stop_reason == "end_turn"
    # 调用方 list 长度不变（内部快照隔离）
    assert len(seed) == original_len


async def test_terminal_branch_prefers_executor_capture_args():
    """终止分支：执行器产出的 capture 优先作为 RunResult 载荷（无 capture 时才回退参数）。"""
    from app.services.agent.protocols import TerminalCapture

    class _CaptureExecutor:
        def feed(self, chunk):  # noqa: ANN001 - 测试替身
            pass

        async def finalize(self):
            return [
                TerminalCapture(name="submit_suggestion", args={"subject_id": "42"})
            ]

    terminal = _resp(
        "tool_use", [_tool_use("s", "submit_suggestion", {"subject_id": "1"})]
    )
    stream = scripted_stream([terminal])

    result = await run_loop(
        stream_fn=stream,
        executor_factory=lambda: _CaptureExecutor(),
        tools_schemas=[
            _schema("submit_suggestion", access="terminal"),
            _TERMINAL_SCHEMA,
        ],
        max_iterations=2,
        seed_messages=_seed(),
    )

    assert result.stop_reason == "submit_suggestion"
    assert result.terminal_args == {"subject_id": "42"}, (
        "存在 capture 时应以 capture.args 为准（消除「产而不用」）"
    )


async def test_assistant_history_messages_always_carry_tool_use_blocks():
    """会话历史中的 assistant 消息必含 tool_use 块（协议约束的行为化守卫）。

    live loop 仅在轮次含 ToolUseBlock（terminal_reason is None）时 append
    assistant 消息；若破坏，纯文本 assistant 会以 provider 兜底 join 改写语义
    被回传。本测试跑真实多轮循环，检查后续轮次可见的历史 assistant 消息。
    """
    stream = scripted_stream(
        [
            _resp("tool_use", [_tool_use("t1", "search_bangumi")]),
            _resp("end_turn", None),
        ]
    )

    await run_loop(
        stream_fn=stream,
        executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        tools_schemas=[_schema("search_bangumi"), _TERMINAL_SCHEMA],
        max_iterations=3,
        seed_messages=_seed(),
    )

    second_round_messages = stream.calls[1][0]
    assistant_messages = [m for m in second_round_messages if m.role == "assistant"]
    assert assistant_messages, "第二轮历史应包含第一轮 assistant 消息"
    for message in assistant_messages:
        blocks = message.content if isinstance(message.content, list) else []
        assert any(getattr(b, "type", "") == "tool_use" for b in blocks), (
            f"历史 assistant 消息必须携带 tool_use 块（协议约束）: {message!r}"
        )
