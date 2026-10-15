"""replay(run_id) 断点重放测试（新签名：无 seed_builder / max_iterations）。

覆盖：
- 完整 run 重建与原执行逐字节等价（含 seed 前缀，无 seed_builder）
- 断点续跑：missing 仅缺失项；已成功不重跑；不完整轮不计 executed_iterations
- 终局响应分派；行缺失/空 delta 从该轮 break 不抛异常
- replay_delta 加密读回透明
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.database import DatabaseManager, set_database_manager
from app.services.agent.execution.streaming import StreamingToolExecutor
from app.services.agent.execution.tools import BatchResults
from app.services.agent.observe import trace
from app.services.agent.observe.delta import BudgetMessageDelta, encode_replay_delta
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
    terminal_schema,
)


def _blocks(message: Message) -> list:
    """显式收窄消息 content 为块列表（测试前提：replay 重建为块列表）"""
    content = message.content
    assert isinstance(content, list), "消息 content 应为块列表"
    return content


@pytest.fixture
def dbm(tmp_path: Path) -> Iterator[DatabaseManager]:
    instance = DatabaseManager(str(tmp_path / "replay.db"))
    set_database_manager(instance)
    yield instance
    if instance._connection._conn is not None:
        instance._connection._conn.close()
    set_database_manager(None)


@pytest.fixture
def crypto_on():
    with patch(
        "app.core.config_secret_crypto._master_secret",
        return_value="test-master-secret-key-for-encryption",
    ):
        yield


@pytest.fixture
def log_records():
    """捕获自定义 Logger（非 stdlib logging，caplog 无法捕获）的日志行。

    应用使用 ``app.core.logging.logger``（print 实现），其监听器不受级别阈值
    限制，故此方式可捕获 DEBUG/INFO/WARNING 全部级别。产出 ``[(level, line)]``。
    """
    from app.core.logging import logger as app_logger

    records: list[tuple[str, str]] = []

    def _listener(line: str, level: str) -> None:
        records.append((level, line))

    app_logger.add_listener(_listener)
    yield records
    app_logger.remove_listener(_listener)


def _chat_rd(stop_reason, content, tool_calls):
    return {
        "response": {
            "stop_reason": stop_reason,
            "content": content,
            "tool_calls": tool_calls,
        }
    }


def _tool_rd(tool_use_id, content, is_error=False):
    return {
        "tool_result": {
            "tool_use_id": tool_use_id,
            "content": content,
            "is_error": is_error,
        }
    }


def _ensure_run(dbm, run_id):
    dbm.agent_runs.create_pending(run_id, "match")


def _write_seed(dbm, run_id, seed_messages):
    """写入 seed 行（等价于 TraceRecorder.write_seed_row）。"""
    _ensure_run(dbm, run_id)
    span_id = trace.start_span(run_id, "seed", 0, 0)
    seed_delta = [m.model_dump() for m in seed_messages]
    dbm.agent_runs.update_step(
        span_id, replay_delta={"seed_messages": seed_delta}, ended_at=_now_epoch()
    )


def _now_epoch():
    import time

    return int(time.time())


def _write_llm_chat(
    dbm, run_id, iteration, tool_calls, stop_reason="tool_use", content="", tokens=0
):
    """写入一轮 llm_chat span（``tokens`` 写专用列）。"""
    _ensure_run(dbm, run_id)
    span_id = trace.start_span(run_id, "llm_chat", iteration, 0)
    trace.end_span(
        span_id, tokens=tokens, replay_delta=_chat_rd(stop_reason, content, tool_calls)
    )
    return span_id


def _write_llm_chat_with_blocks(
    dbm,
    run_id,
    iteration,
    tool_calls,
    blocks,
    stop_reason="tool_use",
    content="",
    tokens=0,
):
    """写入一轮含全量 ``blocks`` 字段的 llm_chat span（修复后 recorder 的形态）。"""
    _ensure_run(dbm, run_id)
    span_id = trace.start_span(run_id, "llm_chat", iteration, 0)
    trace.end_span(
        span_id,
        tokens=tokens,
        replay_delta={
            "response": {
                "stop_reason": stop_reason,
                "content": content,
                "tool_calls": tool_calls,
                "blocks": blocks,
            }
        },
    )
    return span_id


def _write_tool_exec(dbm, run_id, iteration, sequence, tool_use_id, content):
    """写入一条 tool_execute span。"""
    _ensure_run(dbm, run_id)
    span_id = trace.start_span(run_id, "tool_execute", iteration, sequence)
    trace.end_span(span_id, replay_delta=_tool_rd(tool_use_id, content))


def _write_budget(dbm, run_id, iteration, sequence, content):
    """写入一条独立 budget 行（与生产 recorder.record_budget 同构）。"""
    _ensure_run(dbm, run_id)
    span_id = trace.start_span(run_id, "budget", iteration, sequence)
    trace.end_span(
        span_id,
        replay_delta=encode_replay_delta(BudgetMessageDelta(budget_message=content)),
    )


def _scripted_stream(script):
    """把 chat 风格脚本函数适配为流式 ``StreamFn``（含工具停点与 usage/stop 事件）。

    每轮透传 ``(messages, tools, tool_choice)`` 调用 ``script`` 取得
    ``ChatResponse`` 并展开为事件流；无块但有 content 时补文本块；
    ``script`` 异常原样透传，返回 ``None``（脚本耗尽）时显式报错。
    """

    async def _stream(messages, *, tools=None, tool_choice=None):
        resp = await script(messages, tools=tools, tool_choice=tool_choice)
        if resp is None:
            raise RuntimeError("_scripted_stream 脚本耗尽：未返回 ChatResponse")
        blocks = list(resp.blocks)
        if not blocks and resp.content:
            blocks = [TextBlock(text=resp.content)]
        for block in blocks:
            if isinstance(block, TextBlock):
                yield StreamChunk(type="text_delta", text=block.text)
            elif isinstance(block, ThinkingBlock):
                yield StreamChunk(
                    type="thinking_delta",
                    thinking=block.thinking,
                    signature=block.signature or "",
                )
            elif isinstance(block, ToolUseBlock):
                yield StreamChunk(
                    type="tool_use_start",
                    tool_use_id=block.id,
                    tool_name=block.name,
                )
                yield StreamChunk(
                    type="tool_use_delta",
                    tool_use_id=block.id,
                    partial_json=json.dumps(block.input, ensure_ascii=False),
                )
                yield StreamChunk(type="tool_use_stop", tool_use_id=block.id)
        if resp.usage is not None:
            yield StreamChunk(type="usage", usage=resp.usage)
        yield StreamChunk(type="stop", stop_reason=resp.stop_reason, model=resp.model)

    return _stream


def _executor_factory(recorder, tools_fn):
    """构造按轮新建的流式执行器：全部工具延迟到 ``finalize`` 复用 ``tools_fn``。

    ``is_idempotent=False`` 使停点不提前执行，整批走 ``batch_execute_fn``——
    与旧轮级整批执行路径的语义、span 记录完全等价；
    ``submit_suggestion`` 标记为 terminal，由 loop 捕获、执行器不执行。
    """

    async def single(tool_use):
        """单工具执行：取批量结果的 ordered 首槽（等价单条路径）。"""
        results = await tools_fn([tool_use])
        return results.ordered[0][1]

    def factory():
        return StreamingToolExecutor(
            run_tool=raw_handler_runner(single),
            is_idempotent=lambda name: False,
            is_terminal=lambda name: name == "submit_suggestion",
            on_recorder=recorder,
            batch_execute_fn=lambda tool_calls: tools_fn(tool_calls),
        )

    return factory


class TestReplayReconstructsFullRun:
    def test_replay_with_seed_prefix(self, dbm):
        """replay 从 seed 行还原种子消息作为 messages 前缀。"""
        seed = [
            Message(role="system", content="sys"),
            Message(role="user", content="ctx"),
        ]
        _write_seed(dbm, "run-seed", seed)
        _write_llm_chat(dbm, "run-seed", 0, [{"id": "t1", "name": "x", "input": {}}])
        _write_tool_exec(dbm, "run-seed", 0, 1, "t1", "res1")

        result = trace.replay("run-seed")
        assert len(result.messages) >= 2
        assert result.messages[0] == seed[0]
        assert result.messages[1] == seed[1]

    def test_replay_byte_equivalent_to_loop_run(self, dbm):
        """replay 重建的 messages 与原执行路径逐字节一致。"""
        seed = [
            Message(role="system", content="sys"),
            Message(role="user", content="ctx"),
        ]
        _write_seed(dbm, "run-eq", seed)
        # 轮 0：2 工具
        _write_llm_chat(
            dbm,
            "run-eq",
            0,
            [
                {"id": "t1", "name": "search_bangumi", "input": {"title": "foo"}},
                {
                    "id": "t2",
                    "name": "get_subject_detail",
                    "input": {"subject_id": "2"},
                },
            ],
        )
        _write_tool_exec(dbm, "run-eq", 0, 1, "t1", "res1")
        _write_tool_exec(dbm, "run-eq", 0, 2, "t2", "res2")
        _write_budget(dbm, "run-eq", 0, 3, "[剩余轮次：1]")
        # 轮 1：终局
        _write_llm_chat(dbm, "run-eq", 1, [], stop_reason="end_turn", content="done")

        result = trace.replay("run-eq")
        expected = [
            Message(role="system", content="sys"),
            Message(role="user", content="ctx"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="t1", name="search_bangumi", input={"title": "foo"}
                    ),
                    ToolUseBlock(
                        id="t2", name="get_subject_detail", input={"subject_id": "2"}
                    ),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="res1", is_error=False)
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t2", content="res2", is_error=False)
                ],
            ),
            Message(role="user", content="[剩余轮次：1]"),
        ]
        assert result.messages == expected
        assert result.last_response is not None
        assert result.last_response.stop_reason == "end_turn"
        assert result.executed_iterations == 1
        assert result.missing_tool_calls == []

    def test_replay_returns_replay_result(self, dbm):
        """replay 返回 ReplayResult（新签名，无 unrecoverable_iteration）。"""
        _write_seed(dbm, "run-rr", [])
        _write_llm_chat(dbm, "run-rr", 0, [], stop_reason="end_turn", content="hi")
        result = trace.replay("run-rr")
        assert isinstance(result, trace.ReplayResult)
        # 无 unrecoverable_iteration 字段
        assert not hasattr(result, "unrecoverable_iteration")


class TestReplayRebuildsFullBlocks:
    """修复 round1 遗留 #1：replay 重建 assistant 消息消费全量 blocks（含 thinking）。

    思考模型的 thinking 块必须随 tool_use 一并回传，否则断点续跑的下一轮请求会 400。
    """

    def test_rebuilds_mixed_blocks_in_order(self, dbm):
        """含 thinking/text/tool_use 的响应 → content 顺序与类型逐条保留。"""
        _write_seed(dbm, "run-blocks", [])
        blocks = [
            ThinkingBlock(thinking="let me think", signature="sig-1").model_dump(),
            TextBlock(text="here is my plan").model_dump(),
            ToolUseBlock(id="t1", name="x", input={"a": 1}).model_dump(),
        ]
        _write_llm_chat_with_blocks(
            dbm,
            "run-blocks",
            0,
            [{"id": "t1", "name": "x", "input": {"a": 1}}],
            blocks,
        )
        _write_tool_exec(dbm, "run-blocks", 0, 1, "t1", "r1")

        result = trace.replay("run-blocks")

        assistant = next(m for m in result.messages if m.role == "assistant")
        assert [type(b) for b in assistant.content] == [
            ThinkingBlock,
            TextBlock,
            ToolUseBlock,
        ]
        restored = _blocks(assistant)
        assert restored[0].thinking == "let me think"
        assert restored[0].signature == "sig-1"
        assert restored[1].text == "here is my plan"
        assert restored[2].id == "t1"

    def test_rebuilt_assistant_matches_live_blocks_expression(self, dbm):
        """replay 重建与 live ``list(resp.blocks)`` 逐条一致（含 thinking）。"""
        _write_seed(dbm, "run-live-eq", [])
        resp = ChatResponse(
            content="plan",
            blocks=[
                ThinkingBlock(thinking="hmm", signature="s1"),
                TextBlock(text="plan"),
                ToolUseBlock(id="t1", name="search_bangumi", input={"title": "foo"}),
            ],
            stop_reason="tool_use",
            model="m",
        )
        _write_llm_chat_with_blocks(
            dbm,
            "run-live-eq",
            0,
            [b.model_dump() for b in resp.blocks if isinstance(b, ToolUseBlock)],
            [b.model_dump() for b in resp.blocks],
            stop_reason=resp.stop_reason,
            content=resp.content,
        )
        _write_tool_exec(dbm, "run-live-eq", 0, 1, "t1", "res1")

        result = trace.replay("run-live-eq")

        assistant = next(m for m in result.messages if m.role == "assistant")
        # live loop 的构造表达式：Message(role="assistant", content=list(resp.blocks))
        assert assistant == Message(role="assistant", content=list(resp.blocks))

    def test_replay_matches_live_loop_messages_with_thinking(self, dbm):
        """端到端一致性：真实 loop.run_loop 的逐轮上下文与 replay.messages 逐条相等。"""
        import asyncio

        from app.services.agent import loop
        from app.services.agent.observe.recorder import TraceRecorder

        _ensure_run(dbm, "run-loop-eq")
        seed = [
            Message(role="system", content="sys"),
            Message(role="user", content="ctx"),
        ]
        round0 = ChatResponse(
            content="plan",
            blocks=[
                ThinkingBlock(thinking="hmm", signature="s1"),
                TextBlock(text="plan"),
                ToolUseBlock(id="t1", name="search_bangumi", input={"title": "foo"}),
            ],
            stop_reason="tool_use",
            model="m",
        )
        round1 = ChatResponse(
            content="done",
            blocks=[
                ThinkingBlock(thinking="final", signature="s2"),
                TextBlock(text="done"),
            ],
            stop_reason="end_turn",
            model="m",
        )
        live_contexts: list[list] = []
        counter = {"n": 0}

        recorder = TraceRecorder("run-loop-eq", start_iteration=0)
        recorder.write_seed_row(seed)

        async def chat(messages, *, tools=None, tool_choice=None):
            live_contexts.append(list(messages))
            counter["n"] += 1
            return round0 if counter["n"] == 1 else round1

        async def tools_fn(tool_calls):
            ordered = []
            for i, tc in enumerate(tool_calls):
                span_id = recorder.start_tool(tc, sequence=i + 1)
                result = ToolResultBlock(tool_use_id=tc.id, content=f"res-{tc.id}")
                if span_id is not None:
                    recorder.end_tool(span_id, result=result)
                ordered.append((tc.id, result))
            return BatchResults(ordered)

        asyncio.run(
            loop.run_loop(
                stream_fn=_scripted_stream(chat),
                executor_factory=_executor_factory(recorder, tools_fn),
                tools_schemas=[terminal_schema()],
                max_iterations=2,
                seed_messages=seed,
                recorder=recorder,
            )
        )

        result = trace.replay("run-loop-eq")

        # live 第二轮请求前的完整上下文 == replay 重建的 messages
        assert live_contexts[1] == result.messages
        # 且确实携带了 thinking 块（修复核心）
        assistant = next(m for m in result.messages if m.role == "assistant")
        assert any(isinstance(b, ThinkingBlock) for b in assistant.content)

    def test_replay_matches_live_loop_messages_with_veto(self, dbm):
        """端到端一致性：veto 注入的 tool_result 在 replay 重建中与 live 一致。"""
        import asyncio

        from app.services.agent import loop
        from app.services.agent.observe.recorder import TraceRecorder

        _ensure_run(dbm, "run-veto-eq")
        seed = [
            Message(role="system", content="sys"),
            Message(role="user", content="ctx"),
        ]
        uncertain = ChatResponse(
            content="",
            blocks=[
                ToolUseBlock(
                    id="s1",
                    name="submit_suggestion",
                    input={"subject_id": "1", "reason": "暂推荐，存在不确定性"},
                )
            ],
            stop_reason="tool_use",
            model="m",
        )
        final = ChatResponse(content="done", blocks=[], stop_reason="end_turn")
        live_contexts: list[list] = []
        counter = {"n": 0}

        recorder = TraceRecorder("run-veto-eq", start_iteration=0)
        recorder.write_seed_row(seed)

        async def chat(messages, *, tools=None, tool_choice=None):
            live_contexts.append(list(messages))
            counter["n"] += 1
            return uncertain if counter["n"] == 1 else final

        async def tools_fn(tool_calls):
            return BatchResults([])

        asyncio.run(
            loop.run_loop(
                stream_fn=_scripted_stream(chat),
                executor_factory=_executor_factory(recorder, tools_fn),
                tools_schemas=[terminal_schema()],
                max_iterations=3,
                seed_messages=seed,
                recorder=recorder,
                veto_terminal=lambda args: "请再核对",
            )
        )

        result = trace.replay("run-veto-eq")

        # live 第二轮请求前的完整上下文 == replay 重建的 messages（含 veto tool_result）
        assert live_contexts[1] == result.messages
        veto_blocks = [
            b
            for m in result.messages
            if isinstance(m.content, list)
            for b in m.content
            if isinstance(b, ToolResultBlock) and b.tool_use_id == "s1"
        ]
        assert veto_blocks, "veto 注入的 tool_result 应可从 replay 重建"
        assert veto_blocks[0].content == "请再核对"

    def test_falls_back_to_tool_calls_when_blocks_absent(self, dbm):
        """旧数据无 ``blocks`` 字段 → 回退 tool_calls 重建（与修复前一致）。"""
        _write_seed(dbm, "run-old", [])
        _write_llm_chat(
            dbm,
            "run-old",
            0,
            [{"id": "t1", "name": "search_bangumi", "input": {"title": "foo"}}],
        )
        _write_tool_exec(dbm, "run-old", 0, 1, "t1", "res1")

        result = trace.replay("run-old")

        assistant = next(m for m in result.messages if m.role == "assistant")
        assert assistant == Message(
            role="assistant",
            content=[
                ToolUseBlock(id="t1", name="search_bangumi", input={"title": "foo"})
            ],
        )

    def test_falls_back_to_tool_calls_when_blocks_empty(self, dbm):
        """``blocks`` 为空列表 → 回退 tool_calls 重建（不产生空 assistant 内容）。"""
        _write_seed(dbm, "run-empty-blocks", [])
        _write_llm_chat_with_blocks(
            dbm,
            "run-empty-blocks",
            0,
            [{"id": "t1", "name": "x", "input": {}}],
            [],
        )
        _write_tool_exec(dbm, "run-empty-blocks", 0, 1, "t1", "res1")

        result = trace.replay("run-empty-blocks")

        assistant = next(m for m in result.messages if m.role == "assistant")
        assert [type(b) for b in assistant.content] == [ToolUseBlock]

    def test_blocks_parse_failure_logs_warning_and_falls_back(self, dbm, log_records):
        """``blocks`` 结构非法 → 记 warning 且回退 tool_calls（不抛断）。"""
        _write_seed(dbm, "run-bad-blocks", [])
        _write_llm_chat_with_blocks(
            dbm,
            "run-bad-blocks",
            0,
            [{"id": "t1", "name": "x", "input": {}}],
            [{"type": "unknown_block", "foo": "bar"}],
        )
        _write_tool_exec(dbm, "run-bad-blocks", 0, 1, "t1", "res1")

        result = trace.replay("run-bad-blocks")

        assistant = next(m for m in result.messages if m.role == "assistant")
        assert [type(b) for b in assistant.content] == [ToolUseBlock]
        warns = [line for level, line in log_records if level == "WARNING"]
        assert any("blocks" in line for line in warns)

    def test_partial_invalid_blocks_preserves_valid_thinking(self, dbm, log_records):
        """部分元素非法 → 逐元素丢弃坏块、保留合法块（含 thinking），不整体回退。"""
        _write_seed(dbm, "run-partial-bad", [])
        _write_llm_chat_with_blocks(
            dbm,
            "run-partial-bad",
            0,
            [{"id": "t1", "name": "x", "input": {}}],
            [
                ThinkingBlock(thinking="keepme", signature="s1").model_dump(),
                {"type": "unknown_block", "foo": "bar"},
                TextBlock(text="plan").model_dump(),
                ToolUseBlock(id="t1", name="x", input={}).model_dump(),
            ],
        )
        _write_tool_exec(dbm, "run-partial-bad", 0, 1, "t1", "res1")

        result = trace.replay("run-partial-bad")

        assistant = next(m for m in result.messages if m.role == "assistant")
        # 保留 thinking/text/tool_use（原顺序），丢弃中间非法元素
        assert [type(b) for b in assistant.content] == [
            ThinkingBlock,
            TextBlock,
            ToolUseBlock,
        ]
        assert _blocks(assistant)[0].thinking == "keepme"
        warns = [line for level, line in log_records if level == "WARNING"]
        # warning 含丢弃数量与类型
        assert any("丢弃" in line and "unknown_block" in line for line in warns)

    def test_restore_blocks_all_invalid_returns_none(self, log_records):
        """全部元素非法 → 返回 None（交由既有 tool_calls 回退）。"""
        assert (
            trace._restore_response_blocks(
                trace.ReplayResponse.model_validate(
                    {"blocks": [{"type": "bad-1"}, {"type": "bad-2"}]}
                )
            )
            is None
        )

    def test_restore_blocks_non_list_returns_none(self, log_records):
        """输入非列表 → 返回 None。"""
        assert (
            trace._restore_response_blocks(
                trace.ReplayResponse.model_validate({"blocks": {"not": "a list"}})
            )
            is None
        )

    def test_derives_tool_calls_from_blocks_when_field_missing(self, dbm, log_records):
        """``tool_calls`` 字段缺失但 blocks 含 tool_use → 从 blocks 推导（不丢工具调用）。

        replay_delta 局部损坏（tool_calls 丢失）时，若不推导会被当作「终局响应」，
        tool_use 静默丢弃、后续恢复误判。断言 assistant 消息依 blocks 重建，
        且 missing 检测基于推导结果生效。
        """
        _write_seed(dbm, "run-derive", [])
        span_id = trace.start_span("run-derive", "llm_chat", 0, 0)
        trace.end_span(
            span_id,
            replay_delta={
                "response": {
                    "stop_reason": "tool_use",
                    "content": "",
                    # 关键：无 "tool_calls" 字段，仅 blocks 含 tool_use
                    "blocks": [
                        ToolUseBlock(
                            id="t1", name="search_bangumi", input={"title": "foo"}
                        ).model_dump()
                    ],
                }
            },
        )

        result = trace.replay("run-derive")

        assistant = next(m for m in result.messages if m.role == "assistant")
        assert [type(b) for b in assistant.content] == [ToolUseBlock]
        assert _blocks(assistant)[0].id == "t1"
        # 未执行 → missing 基于推导出的 tool_calls 生效
        assert [tc.id for tc in result.missing_tool_calls] == ["t1"]
        warns = [line for level, line in log_records if level == "WARNING"]
        assert any("blocks" in line and "推导" in line for line in warns)

    def test_empty_tool_calls_derives_from_blocks_with_tool_use(self, dbm):
        """``tool_calls`` 为空列表（非缺失）但 blocks 含 tool_use → 同样推导。"""
        _write_seed(dbm, "run-derive-empty", [])
        _write_llm_chat_with_blocks(
            dbm,
            "run-derive-empty",
            0,
            [],
            [ToolUseBlock(id="t2", name="get_subject_detail", input={}).model_dump()],
        )
        _write_tool_exec(dbm, "run-derive-empty", 0, 1, "t2", "res2")

        result = trace.replay("run-derive-empty")

        assistant = next(m for m in result.messages if m.role == "assistant")
        assert [type(b) for b in assistant.content] == [ToolUseBlock]
        assert result.missing_tool_calls == [], "已执行完毕不应判缺失"

    def test_terminal_round_with_thinking_returned_as_last_response(self, dbm):
        """终局轮：thinking + 终止工具调用（无 tool_execute）→ 作为 last_response 交回。"""
        _write_seed(dbm, "run-term-th", [])
        submit_input = {"subject_id": "2", "reason": "best match"}
        _write_llm_chat_with_blocks(
            dbm,
            "run-term-th",
            1,
            [
                {
                    "id": "t9",
                    "name": "submit_suggestion",
                    "input": submit_input,
                }
            ],
            [
                ThinkingBlock(thinking="decide", signature="s9").model_dump(),
                ToolUseBlock(
                    id="t9", name="submit_suggestion", input=submit_input
                ).model_dump(),
            ],
            stop_reason="tool_use",
        )

        result = trace.replay("run-term-th")

        assert result.last_response is not None
        assert result.last_response.stop_reason == "tool_use"
        tcs = result.last_response.tool_calls
        assert [tc.name for tc in tcs] == ["submit_suggestion"]
        assert tcs[0].input == submit_input
        # 终止工具未执行 → 计入缺失供 runtime 分派（语义不变）
        assert [tc.id for tc in result.missing_tool_calls] == ["t9"]


class TestReplayTotalTokens:
    """replay 累计已发生 llm_chat span 的 tokens，供恢复路径写回 total_tokens。"""

    def test_replay_accumulates_total_tokens(self, dbm):
        """2 轮 llm_chat（tokens=100/50）→ ReplayResult.total_tokens == 150。"""
        _write_seed(dbm, "run-toks", [])
        _write_llm_chat(
            dbm,
            "run-toks",
            0,
            [{"id": "t1", "name": "search_bangumi", "input": {}}],
            tokens=100,
        )
        _write_tool_exec(dbm, "run-toks", 0, 1, "t1", "res1")
        _write_llm_chat(
            dbm,
            "run-toks",
            1,
            [],
            stop_reason="end_turn",
            content="done",
            tokens=50,
        )

        result = trace.replay("run-toks")
        assert result.total_tokens == 150, (
            f"应累计 2 轮 tokens=150，实际 {result.total_tokens}"
        )

    def test_replay_total_tokens_zero_when_no_chat_tokens(self, dbm):
        """无 tokens 数据时累计为 0（不报错、不误记）。"""
        _write_seed(dbm, "run-toks-zero", [])
        _write_llm_chat(
            dbm, "run-toks-zero", 0, [], stop_reason="end_turn", content="done"
        )

        result = trace.replay("run-toks-zero")
        assert result.total_tokens == 0


class TestReplayCheckpointResume:
    def test_missing_only_missing_tool(self, dbm):
        """missing_tool_calls 仅含缺失的工具。"""
        _write_seed(dbm, "run-miss", [])
        _write_llm_chat(
            dbm,
            "run-miss",
            0,
            [
                {"id": "t1", "name": "search_bangumi", "input": {"title": "foo"}},
                {
                    "id": "t2",
                    "name": "get_subject_detail",
                    "input": {"subject_id": "2"},
                },
            ],
        )
        _write_tool_exec(dbm, "run-miss", 0, 1, "t1", "res1")
        # t2 故意不写

        result = trace.replay("run-miss")
        missing_ids = [tc.id for tc in result.missing_tool_calls]
        assert missing_ids == ["t2"]
        assert result.executed_iterations == 0
        # 已成功的不重跑：t1 的 tool_result 在 messages 中
        tr_ids = [
            _blocks(m)[0].tool_use_id
            for m in result.messages
            if m.role == "user"
            and isinstance(m.content, list)
            and any(isinstance(b, ToolResultBlock) for b in m.content)
        ]
        assert tr_ids == ["t1"]

    def test_incomplete_round_not_counted(self, dbm):
        """不完整轮不计 executed_iterations、不追加预算消息。"""
        _write_seed(dbm, "run-inc", [])
        _write_llm_chat(
            dbm,
            "run-inc",
            0,
            [
                {"id": "t1", "name": "x", "input": {}},
                {"id": "t2", "name": "y", "input": {}},
            ],
        )
        _write_tool_exec(dbm, "run-inc", 0, 1, "t1", "r1")

        result = trace.replay("run-inc")
        assert result.executed_iterations == 0
        # 无预算消息（缺失轮不追加）
        budget_msgs = [
            m
            for m in result.messages
            if m.role == "user" and isinstance(m.content, str)
        ]
        assert budget_msgs == []

    def test_completed_rounds_not_replayed_as_missing(self, dbm):
        """已完整执行的轮次不重跑，missing 仅最后一轮缺失项。"""
        _write_seed(dbm, "run-comp", [])
        # 轮 0 完整
        _write_llm_chat(dbm, "run-comp", 0, [{"id": "a", "name": "x", "input": {}}])
        _write_tool_exec(dbm, "run-comp", 0, 1, "a", "ra")
        # 轮 1 缺失
        _write_llm_chat(
            dbm,
            "run-comp",
            1,
            [
                {"id": "b", "name": "y", "input": {}},
                {"id": "c", "name": "z", "input": {}},
            ],
        )
        _write_tool_exec(dbm, "run-comp", 1, 1, "b", "rb")

        result = trace.replay("run-comp")
        assert result.executed_iterations == 1
        missing_ids = [tc.id for tc in result.missing_tool_calls]
        assert missing_ids == ["c"]


class TestReplayTerminalDispatch:
    def test_end_turn_returns_last_response(self, dbm):
        """终局 end_turn 响应分派到 last_response。"""
        _write_seed(dbm, "run-end", [])
        _write_llm_chat(
            dbm, "run-end", 0, [], stop_reason="end_turn", content="no suggestion"
        )

        result = trace.replay("run-end")
        assert result.last_response is not None
        assert result.last_response.stop_reason == "end_turn"
        assert result.last_response.content == "no suggestion"
        assert result.executed_iterations == 0

    def test_break_on_empty_delta_no_exception(self, dbm):
        """空 delta 的轮次从该轮 break，不抛异常。"""
        _write_seed(dbm, "run-empty", [])
        # 轮 0：llm_chat 的 replay_delta 为空（异常数据）
        trace.start_span("run-empty", "llm_chat", 0, 0)
        # 不写 end_span（replay_delta 为空字符串）

        # 不应抛异常
        result = trace.replay("run-empty")
        assert result.executed_iterations == 0
        assert result.last_response is None

    def test_break_on_missing_llm_chat_no_exception(self, dbm):
        """某轮无 llm_chat（仅有 tool_execute）时 break，不抛异常。"""
        _write_seed(dbm, "run-nochat", [])
        # 轮 0 只有 tool_execute 无 llm_chat
        _write_tool_exec(dbm, "run-nochat", 0, 1, "t1", "r1")

        result = trace.replay("run-nochat")
        # 无 llm_chat → break → 不抛异常
        assert result.executed_iterations == 0


class TestReplaySortingStability:
    def test_seed_precedes_llm_chat_when_both_at_0_0(self, dbm):
        """seed 行与首轮 llm_chat 同 (0,0) 时，seed 必须排在 chat 之前。"""
        seed = [Message(role="system", content="sys")]
        _write_seed(dbm, "run-sort", seed)
        # seed 与 llm_chat 均为 iteration=0, sequence=0
        _write_llm_chat(dbm, "run-sort", 0, [{"id": "t1", "name": "x", "input": {}}])
        _write_tool_exec(dbm, "run-sort", 0, 1, "t1", "r1")
        result = trace.replay("run-sort")
        # seed 必须在前缀位置（首条消息 = system/seed）
        assert result.messages[0].role == "system"
        assert result.messages[0].content == "sys"


class TestReplayEncryptionTransparent:
    def test_replay_reads_encrypted_delta_transparently(self, dbm, crypto_on):
        """replay_delta 加密落库，replay 读取时透明解密。"""
        seed = [Message(role="user", content="ctx")]
        _write_seed(dbm, "run-crypt", seed)
        _write_llm_chat(
            dbm,
            "run-crypt",
            0,
            [
                {
                    "id": "t1",
                    "name": "search_bangumi",
                    "input": {"title": "secret-title"},
                }
            ],
        )
        _write_tool_exec(dbm, "run-crypt", 0, 1, "t1", "secret-result")

        result = trace.replay("run-crypt")
        # 解密透明：能还原 assistant 消息
        assistant = result.messages[1]  # [0]=seed
        assert assistant.role == "assistant"
        assert _blocks(assistant)[0].input["title"] == "secret-title"
        # tool_result 也解密
        tr = [
            m
            for m in result.messages
            if m.role == "user" and isinstance(m.content, list)
        ]
        assert _blocks(tr[0])[0].content == "secret-result"


class TestReplayAbnormalBranchLogging:
    """异常数据分支不得静默：每条跳过/中断路径都须留日志。"""

    def test_replay_seed_parse_failure_logs_warning(self, dbm, log_records):
        """seed 条目反序列化失败：跳过该条并记 warning，其余 seed 正常重建。"""
        _ensure_run(dbm, "run-seed-bad")
        span_id = trace.start_span("run-seed-bad", "seed", 0, 0)
        dbm.agent_runs.update_step(
            span_id,
            replay_delta={
                "seed_messages": [
                    {"role": "system", "content": "sys"},
                    {"content": "缺 role 字段，无法 model_validate"},
                ]
            },
            ended_at=_now_epoch(),
        )
        _write_llm_chat(
            dbm, "run-seed-bad", 0, [], stop_reason="end_turn", content="hi"
        )

        result = trace.replay("run-seed-bad")

        # 合法 seed 正常重建，非法条目被跳过（不影响其余）
        assert result.messages[0].role == "system"
        assert result.messages[0].content == "sys"
        # 记录 warning，且含 seed 关键信息与异常内容
        warns = [line for level, line in log_records if level == "WARNING"]
        assert any("seed" in line for line in warns)

    def test_replay_missing_llm_chat_logs_warning(self, dbm, log_records):
        """某轮无 llm_chat 行：在该轮 break 并记 warning。"""
        _write_seed(dbm, "run-nochat-log", [])
        _write_tool_exec(dbm, "run-nochat-log", 0, 1, "t1", "r1")

        result = trace.replay("run-nochat-log")

        assert result.executed_iterations == 0
        warns = [line for level, line in log_records if level == "WARNING"]
        assert any("llm_chat" in line for line in warns)

    def test_replay_empty_chat_delta_logs_info(self, dbm, log_records):
        """llm_chat 的 replay_delta 为空（无 response）：在该轮 break 并记 info。"""
        _write_seed(dbm, "run-empty-info", [])
        # 只 start_span 不 end_span → replay_delta 为空字符串
        trace.start_span("run-empty-info", "llm_chat", 0, 0)

        result = trace.replay("run-empty-info")

        assert result.executed_iterations == 0
        infos = [line for level, line in log_records if level == "INFO"]
        assert any("replay_delta" in line for line in infos)

    def test_replay_skips_non_tool_execute_rows_logs_warning(self, dbm, log_records):
        """重建轮内非 tool_execute 行：跳过并记 warning（replay 数据异常，默认可见）。"""
        _write_seed(dbm, "run-skip-row", [])
        _write_llm_chat(
            dbm, "run-skip-row", 0, [{"id": "t1", "name": "x", "input": {}}]
        )
        _write_tool_exec(dbm, "run-skip-row", 0, 1, "t1", "r1")

        result = trace.replay("run-skip-row")

        assert result.executed_iterations == 1
        warns = [line for level, line in log_records if level == "WARNING"]
        # 同轮 llm_chat 行在重建循环中被跳过（数据异常），应留 warning
        assert any("llm_chat" in line for line in warns)

    def test_replay_tool_result_parse_failure_logs_warning(self, dbm, log_records):
        """tool_execute 无有效 tool_result：跳过并记 warning，工具计入缺失。"""
        _write_seed(dbm, "run-tr-bad", [])
        _write_llm_chat(dbm, "run-tr-bad", 0, [{"id": "t1", "name": "x", "input": {}}])
        # tool_execute 行存在，但 replay_delta 无 tool_result 字段
        span_id = trace.start_span("run-tr-bad", "tool_execute", 0, 1)
        trace.end_span(span_id, replay_delta={"foo": "bar"})

        result = trace.replay("run-tr-bad")

        # 该工具未记录 → 计入 missing_tool_calls
        assert [tc.id for tc in result.missing_tool_calls] == ["t1"]
        # 不追加任何 tool_result 消息
        tr_msgs = [
            m
            for m in result.messages
            if m.role == "user" and isinstance(m.content, list)
        ]
        assert tr_msgs == []
        warns = [line for level, line in log_records if level == "WARNING"]
        assert any("tool_result" in line for line in warns)


class TestReplayResponseTolerantModel:
    """批次2：response/tool_calls 模型化（ReplayResponse/ReplayToolCall）后的宽容解析。"""

    def test_parse_response_returns_none_on_empty_delta_and_empty_response(self):
        """空 delta / 空 response → None（与旧实现「空 response 判假 break」语义一致）。"""
        assert trace._parse_response({"replay_delta": ""}) is None
        assert trace._parse_response({"replay_delta": {"response": {}}}) is None
        assert trace._parse_response({"replay_delta": None}) is None

    def test_parse_response_missing_fields_uses_defaults(self, dbm):
        """response 缺 content/tool_calls/blocks → 取默认值，不抛异常。"""
        _write_seed(dbm, "run-tolerant", [])
        span_id = trace.start_span("run-tolerant", "llm_chat", 0, 0)
        trace.end_span(span_id, replay_delta={"response": {"stop_reason": "end_turn"}})

        result = trace.replay("run-tolerant")

        resp = result.last_response
        assert isinstance(resp, trace.ReplayResponse)
        assert resp.stop_reason == "end_turn"
        assert resp.content == ""
        assert resp.tool_calls == []
        assert resp.blocks is None

    def test_parse_response_extra_fields_ignored(self, dbm):
        """response 含额外字段 → 忽略，不抛异常、不影响已知字段。"""
        _write_seed(dbm, "run-extra", [])
        span_id = trace.start_span("run-extra", "llm_chat", 0, 0)
        trace.end_span(
            span_id,
            replay_delta={
                "response": {
                    "stop_reason": "end_turn",
                    "content": "hi",
                    "totally_unknown": {"nested": 1},
                }
            },
        )

        result = trace.replay("run-extra")

        resp = result.last_response
        assert isinstance(resp, trace.ReplayResponse)
        assert resp.stop_reason == "end_turn"
        assert resp.content == "hi"

    def test_bad_tool_call_element_dropped_with_warning(self, dbm, log_records):
        """坏 tool_calls 元素（非 dict）被丢弃并 warning，合法项保留，不中断重放。"""
        _write_seed(dbm, "run-bad-tc", [])
        _write_llm_chat(
            dbm,
            "run-bad-tc",
            0,
            [
                {"id": "t1", "name": "x", "input": {}},
                "not-a-dict",
                123,
            ],
        )
        _write_tool_exec(dbm, "run-bad-tc", 0, 1, "t1", "r1")

        result = trace.replay("run-bad-tc")

        assistant = next(m for m in result.messages if m.role == "assistant")
        assert [b.id for b in _blocks(assistant)] == ["t1"]
        warns = [line for level, line in log_records if level == "WARNING"]
        assert any("tool_calls" in line and "丢弃" in line for line in warns)

    def test_non_list_tool_calls_treated_as_empty_with_warning(self, dbm, log_records):
        """tool_calls 非列表（损坏数据）→ 记 warning 后按空处理。"""
        _write_seed(dbm, "run-tc-dict", [])
        span_id = trace.start_span("run-tc-dict", "llm_chat", 0, 0)
        trace.end_span(
            span_id,
            replay_delta={
                "response": {
                    "stop_reason": "end_turn",
                    "content": "x",
                    "tool_calls": {"not": "a list"},
                }
            },
        )

        result = trace.replay("run-tc-dict")

        assert result.last_response is not None
        assert result.last_response.tool_calls == []
        warns = [line for level, line in log_records if level == "WARNING"]
        assert any("tool_calls" in line and "非列表" in line for line in warns)

    def test_missing_tool_calls_are_typed_models(self, dbm):
        """missing_tool_calls 元素为 ReplayToolCall（类型化，下游按属性访问）。"""
        _write_seed(dbm, "run-miss-model", [])
        _write_llm_chat(
            dbm, "run-miss-model", 0, [{"id": "t1", "name": "x", "input": {}}]
        )

        result = trace.replay("run-miss-model")

        assert result.missing_tool_calls
        assert all(
            isinstance(tc, trace.ReplayToolCall) for tc in result.missing_tool_calls
        )
