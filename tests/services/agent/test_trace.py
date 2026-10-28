"""span 记录器 + replay_delta 加密 + epoch 时间测试。

覆盖：
- start_span / end_span 全字段写入 + 时间注入参数 + epoch 断言
- end_span 无 payload_json 参数（签名检查）
- 三处 replay_delta 写路径落库均为 BGS1: 密文（加密开启时）
- get_steps 解密后还原明文
- 无前缀明文容错（decrypt 对明文原样返回）
- budget 独立行职责分离（agent_steps 新增行，llm_usage 不变）
- chat span（``recorder.chat_span()``）落库契约：全量 blocks / 空 blocks / 大载荷 / 异常 error span
- 无 32KB / error 标记机制
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.database import DatabaseManager, set_database_manager
from app.services.agent.observe import trace
from app.services.agent.observe.recorder import TraceRecorder
from app.services.llm.models import (
    ChatResponse,
    Message,
    RedactedThinkingBlock,
    StreamChunk,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)


@pytest.fixture
def dbm(tmp_path: Path) -> Iterator[DatabaseManager]:
    instance = DatabaseManager(str(tmp_path / "trace_enc.db"))
    set_database_manager(instance)
    yield instance
    if instance._connection._conn is not None:
        instance._connection._conn.close()
    set_database_manager(None)


@pytest.fixture
def crypto_on():
    """开启加密：注入测试 master secret。"""
    with patch(
        "app.core.config_secret_crypto._master_secret",
        return_value="test-master-secret-key-for-encryption",
    ):
        yield


@pytest.fixture
def log_records():
    """捕获自定义 Logger（非 stdlib logging）的日志行，产出 ``[(level, line)]``。"""
    from app.core.logging import logger as app_logger

    records: list[tuple[str, str]] = []

    def _listener(line: str, level: str) -> None:
        records.append((level, line))

    app_logger.add_listener(_listener)
    yield records
    app_logger.remove_listener(_listener)


def _chat_replay_delta(stop_reason, content, tool_calls):
    return {
        "response": {
            "stop_reason": stop_reason,
            "content": content,
            "tool_calls": tool_calls,
        }
    }


def _tool_replay_delta(tool_use_id, content, is_error=False):
    return {
        "tool_result": {
            "tool_use_id": tool_use_id,
            "content": content,
            "is_error": is_error,
        }
    }


def _ensure_run(dbm, run_id: str):
    """创建一条 pending run（FK 要求 run 先于 steps 存在，与生产流程一致）。"""
    dbm.agent_runs.create_pending(run_id, "match")


def _stream_from_response(resp):
    """把单个 ``ChatResponse`` 展开为事件流（含工具停点），供 chat span 消费。

    与生产 provider 的事件序列对齐：thinking/redacted_thinking/text/tool_use
    （start+delta+stop）、usage、结尾 stop（携带 stop_reason/model）；无块但有
    content 时补一个文本块。``block_index`` 按块的实际位置填充，交错块才不会塌陷。
    """

    async def _stream(messages, *, tools=None, tool_choice=None):
        blocks = list(resp.blocks)
        if not blocks and resp.content:
            blocks = [TextBlock(text=resp.content)]
        for index, block in enumerate(blocks):
            if isinstance(block, TextBlock):
                yield StreamChunk(type="text_delta", text=block.text, block_index=index)
            elif isinstance(block, ThinkingBlock):
                yield StreamChunk(
                    type="thinking_delta",
                    thinking=block.thinking,
                    signature=block.signature or "",
                    block_index=index,
                )
            elif isinstance(block, RedactedThinkingBlock):
                yield StreamChunk(
                    type="redacted_thinking_delta",
                    redacted_data=block.data,
                    block_index=index,
                )
            elif isinstance(block, ToolUseBlock):
                yield StreamChunk(
                    type="tool_use_start",
                    tool_use_id=block.id,
                    tool_name=block.name,
                    block_index=index,
                )
                yield StreamChunk(
                    type="tool_use_delta",
                    tool_use_id=block.id,
                    partial_json=json.dumps(block.input, ensure_ascii=False),
                    block_index=index,
                )
                yield StreamChunk(
                    type="tool_use_stop",
                    tool_use_id=block.id,
                    block_index=index,
                )
        if resp.usage is not None:
            yield StreamChunk(type="usage", usage=resp.usage)
        yield StreamChunk(type="stop", stop_reason=resp.stop_reason, model=resp.model)

    return _stream


def _consume_chat_span(recorder, stream_fn, messages=None):
    """以 ``recorder.chat_span()`` 消费一轮流（与 loop 的用法一致），返回聚合响应。

    ``resp`` 在 ``async with`` **块外**读取：``__aexit__`` 才做唯一一次 finalize。
    """
    msgs = messages if messages is not None else [Message(role="user", content="hi")]

    async def _run():
        async with recorder.chat_span() as span:
            async for chunk in stream_fn(msgs, tools=[]):
                span.feed(chunk)
        return span.resp

    return asyncio.run(_run())


class TestEndSpanSignature:
    def test_end_span_has_no_payload_json_param(self):
        """end_span 签名不含 payload_json 参数。"""
        sig = inspect.signature(trace.end_span)
        assert "payload_json" not in sig.parameters

    def test_end_span_has_epoch_time_params(self):
        """end_span 含可选 started_at / ended_at 整数时间注入参数。"""
        sig = inspect.signature(trace.end_span)
        assert "started_at" in sig.parameters
        assert "ended_at" in sig.parameters


class TestStartSpanEpoch:
    def test_start_span_default_epoch(self, dbm):
        """start_span 默认写入 epoch 整数 started_at。"""
        import time

        _ensure_run(dbm, "run-s")
        before = int(time.time())
        trace.start_span("run-s", "llm_chat", 0, 0)
        after = int(time.time())
        steps = dbm.agent_runs.get_steps("run-s")
        assert len(steps) == 1
        s = steps[0]
        assert isinstance(s["started_at"], int)
        assert before <= s["started_at"] <= after

    def test_start_span_inject_epoch(self, dbm):
        """start_span 接受注入的 started_at epoch。"""
        _ensure_run(dbm, "run-si")
        trace.start_span("run-si", "llm_chat", 0, 0, started_at=1_700_000_000)
        steps = dbm.agent_runs.get_steps("run-si")
        assert steps[0]["started_at"] == 1_700_000_000


class TestReplayDeltaEncryptedAtRest:
    def test_add_step_replay_delta_encrypted(self, dbm, crypto_on):
        """add_step 写入的 replay_delta 落库为 BGS1: 密文。"""
        _ensure_run(dbm, "run-e1")
        sid = trace.start_span("run-e1", "llm_chat", 0, 0)
        trace.end_span(
            sid,
            replay_delta=_chat_replay_delta(
                "tool_use", "go", [{"id": "t1", "name": "x", "input": {}}]
            ),
        )
        # 直接查库（不解密）
        conn = dbm._connection._get_connection()
        raw = conn.execute(
            "SELECT replay_delta FROM agent_steps WHERE span_id=?", (sid,)
        ).fetchone()[0]
        assert raw.startswith("BGS1:")
        # 解密后还原
        from app.core.config_secret_crypto import decrypt

        obj = json.loads(decrypt(raw))
        assert obj["response"]["tool_calls"][0]["id"] == "t1"

    def test_get_steps_decrypts_replay_delta(self, dbm, crypto_on):
        """get_steps 返回的 replay_delta 已解密为明文。"""
        _ensure_run(dbm, "run-e2")
        sid = trace.start_span("run-e2", "llm_chat", 0, 0)
        trace.end_span(
            sid,
            replay_delta=_chat_replay_delta(
                "tool_use", "go", [{"id": "t1", "name": "x", "input": {}}]
            ),
        )
        steps = dbm.agent_runs.get_steps("run-e2")
        # get_steps 解密：返回明文 JSON 字符串
        obj = json.loads(steps[0]["replay_delta"])
        assert obj["response"]["tool_calls"][0]["id"] == "t1"

    def test_update_step_replay_delta_encrypted(self, dbm, crypto_on):
        """update_step 写入的 replay_delta 落库为 BGS1: 密文。"""
        _ensure_run(dbm, "run-e3")
        sid = trace.start_span("run-e3", "tool_execute", 0, 1)
        dbm.agent_runs.update_step(
            sid,
            replay_delta=_tool_replay_delta("t1", "result"),
        )
        conn = dbm._connection._get_connection()
        raw = conn.execute(
            "SELECT replay_delta FROM agent_steps WHERE span_id=?", (sid,)
        ).fetchone()[0]
        assert raw.startswith("BGS1:")

    def test_plaintext_replay_delta_passes_through(self, dbm):
        """无前缀明文 replay_delta：decrypt 原样返回（历史数据兼容）。"""
        # 直接写一条明文 replay_delta（模拟历史数据）
        _ensure_run(dbm, "run-plain")
        sid = trace.start_span("run-plain", "llm_chat", 0, 0)
        conn = dbm._connection._get_connection()
        plain = json.dumps(_chat_replay_delta("end_turn", "hi", []))
        conn.execute(
            "UPDATE agent_steps SET replay_delta=? WHERE span_id=?",
            (plain, sid),
        )
        conn.commit()
        steps = dbm.agent_runs.get_steps("run-plain")
        # 解密容错：明文原样返回
        obj = json.loads(steps[0]["replay_delta"])
        assert obj["response"]["stop_reason"] == "end_turn"


class TestRecorderPersistsFullBlocks:
    """修复 round1 遗留 #1：recorder 的 replay_delta.response 追加全量 ``blocks``。

    仅追加字段、不升级 schema：既有 stop_reason/content/tool_calls 保持不变，
    旧数据（无 blocks）读取方走回退逻辑。
    """

    def test_chat_span_persists_full_blocks_with_thinking(self, dbm):
        """chat span 落库含全量 blocks（thinking/text/tool_use 顺序保持）。"""
        _ensure_run(dbm, "run-blocks-rec")
        recorder = TraceRecorder("run-blocks-rec", start_iteration=0)
        resp = ChatResponse(
            content="plan",
            blocks=[
                ThinkingBlock(thinking="let me think", signature="sig-1"),
                TextBlock(text="plan"),
                ToolUseBlock(id="t1", name="search_bangumi", input={"title": "x"}),
            ],
            stop_reason="tool_use",
            model="m",
        )

        got = _consume_chat_span(recorder, _stream_from_response(resp))

        # 聚合响应与落库同源：loop 读到的内容即 span 记录的内容
        assert got is not None
        assert got.blocks == resp.blocks

        step = next(
            s
            for s in dbm.agent_runs.get_steps("run-blocks-rec")
            if s["name"] == "llm_chat"
        )
        # iteration 跟踪：首轮 chat span iteration=0
        assert step["iteration"] == 0
        response = json.loads(step["replay_delta"])["response"]
        # 旧字段保持（流式下 content 由文本块聚合而来）
        assert response["stop_reason"] == "tool_use"
        assert response["content"] == "plan"
        assert response["tool_calls"][0]["id"] == "t1"
        # 追加字段：全量 blocks（含 thinking），逐条与 model_dump 一致
        assert response["blocks"] == [b.model_dump() for b in resp.blocks]
        assert [b["type"] for b in response["blocks"]] == [
            "thinking",
            "text",
            "tool_use",
        ]

    def test_chat_span_persists_empty_blocks(self, dbm):
        """无任何块事件的响应（end_turn）→ response["blocks"] 存空列表。"""
        _ensure_run(dbm, "run-blocks-empty")
        recorder = TraceRecorder("run-blocks-empty", start_iteration=0)

        async def stream_fn(messages, *, tools=None, tool_choice=None):
            yield StreamChunk(type="stop", stop_reason="end_turn", model="m")

        _consume_chat_span(recorder, stream_fn)

        step = next(
            s
            for s in dbm.agent_runs.get_steps("run-blocks-empty")
            if s["name"] == "llm_chat"
        )
        response = json.loads(step["replay_delta"])["response"]
        assert response["blocks"] == []

    def test_large_thinking_blocks_round_trip_through_encryption(self, dbm, crypto_on):
        """加密管道对更大 JSON 无影响：超长 thinking 块往返完整、不截断。"""
        _ensure_run(dbm, "run-blocks-big")
        big_thinking = "t" * 60_000
        recorder = TraceRecorder("run-blocks-big", start_iteration=0)
        resp = ChatResponse(
            content="",
            blocks=[
                ThinkingBlock(thinking=big_thinking, signature="sig"),
                ToolUseBlock(id="t1", name="x", input={}),
            ],
            stop_reason="tool_use",
            model="m",
        )

        _consume_chat_span(recorder, _stream_from_response(resp))

        # crypto_on 下 get_steps 透明解密；往返完整
        step = next(
            s
            for s in dbm.agent_runs.get_steps("run-blocks-big")
            if s["name"] == "llm_chat"
        )
        response = json.loads(step["replay_delta"])["response"]
        assert response["blocks"][0]["thinking"] == big_thinking


class TestNo32KbErrorMechanism:
    def test_no_max_replay_delta_bytes_constant(self):
        """trace.py 不再定义 MAX_REPLAY_DELTA_BYTES（无 32KB 机制）。"""
        assert not hasattr(trace, "MAX_REPLAY_DELTA_BYTES")

    def test_large_replay_delta_not_marked_error(self, dbm, crypto_on):
        """超大 replay_delta 不标记 status=error（无截断/error 机制）。"""
        _ensure_run(dbm, "run-big")
        sid = trace.start_span("run-big", "llm_chat", 0, 0)
        huge = {"data": "x" * 100_000}
        trace.end_span(sid, replay_delta=huge)
        steps = dbm.agent_runs.get_steps("run-big")
        # status 仍为 ok（未被标记 error）
        assert steps[0]["status"] == "ok"
        # 且完整保存（解密后数据完整）
        obj = json.loads(steps[0]["replay_delta"])
        assert len(obj["data"]) == 100_000


class TestEndSpanDoesNotOverwriteStartedAt:
    def test_end_span_does_not_overwrite_started_at(self, dbm):
        """end_span 未传 started_at 时不覆盖 start_span 已写入的真实开始时间。"""
        _ensure_run(dbm, "run-keep-start")
        # start_span 注入一个明确的过去时间 t0
        t0 = 1_700_000_000
        sid = trace.start_span("run-keep-start", "llm_chat", 0, 0, started_at=t0)
        # 确保可区分：end_span 的 ended_at 必须与 t0 不同
        trace.end_span(sid, ended_at=t0 + 100)
        steps = dbm.agent_runs.get_steps("run-keep-start")
        assert len(steps) == 1
        s = steps[0]
        # started_at 必须保持 t0，不能被 end_span 覆盖
        assert s["started_at"] == t0
        # ended_at 正常写入
        assert s["ended_at"] == t0 + 100
        # 两者必须不同（证明 started_at 未被 ended_at 覆盖）
        assert s["started_at"] != s["ended_at"]

    def test_end_span_explicit_started_at_overwrites(self, dbm):
        """end_span 显式传入 started_at 时允许覆盖（向后兼容）。"""
        _ensure_run(dbm, "run-explicit-start")
        t0 = 1_700_000_000
        t_new = 1_700_000_500
        sid = trace.start_span("run-explicit-start", "llm_chat", 0, 0, started_at=t0)
        trace.end_span(sid, started_at=t_new, ended_at=t_new + 50)
        steps = dbm.agent_runs.get_steps("run-explicit-start")
        assert steps[0]["started_at"] == t_new
        assert steps[0]["ended_at"] == t_new + 50


class TestAgentStepsVsLlmUsageSplit:
    def test_budget_row_lands_in_agent_steps_not_llm_usage(self, dbm):
        """职责分离行为契约：预算写**独立 budget 行**（agent_steps 新增行），
        llm_usage_logs 行数不变（用量聚合与重放载荷互不写入）。"""
        from app.services.agent.observe.recorder import TraceRecorder

        _ensure_run(dbm, "run-split")
        sid = trace.start_span("run-split", "tool_execute", 0, 1)
        trace.end_span(sid, replay_delta=_tool_replay_delta("t1", "r"))
        assert dbm.llm_usage.log_usage(model="m", total_tokens=1) is True

        conn = dbm._connection._get_connection()
        steps_before = conn.execute("SELECT COUNT(*) FROM agent_steps").fetchone()[0]
        usage_before = conn.execute("SELECT COUNT(*) FROM llm_usage_logs").fetchone()[0]
        assert usage_before == 1

        recorder = TraceRecorder("run-split", start_iteration=0)
        recorder.record_budget("[剩余轮次：2]")

        steps_after = conn.execute("SELECT COUNT(*) FROM agent_steps").fetchone()[0]
        usage_after = conn.execute("SELECT COUNT(*) FROM llm_usage_logs").fetchone()[0]
        # 预算为独立新增行（agent_steps +1），且绝不写 llm_usage_logs
        assert steps_after == steps_before + 1
        assert usage_after == usage_before == 1
        budget_rows = [
            st for st in dbm.agent_runs.get_steps("run-split") if st["name"] == "budget"
        ]
        assert len(budget_rows) == 1
        obj = json.loads(budget_rows[0]["replay_delta"])
        assert obj["kind"] == "budget_message"
        assert obj["budget_message"] == "[剩余轮次：2]"


class TestRecorderChatSpan:
    """``recorder.chat_span()``：单点聚合 + 轮末写 span（schema 不变）。"""

    def test_chat_span_persists_folded_response(self, dbm):
        from app.services.llm.models import StreamChunk, Usage

        _ensure_run(dbm, "run-stream-rec")
        recorder = TraceRecorder("run-stream-rec", start_iteration=0)

        async def stream_fn(messages, *, tools=None, tool_choice=None):
            yield StreamChunk(type="thinking_delta", thinking="想", signature="s")
            yield StreamChunk(type="text_delta", text="搜")
            yield StreamChunk(
                type="tool_use_start", tool_use_id="t1", tool_name="search_bangumi"
            )
            yield StreamChunk(
                type="tool_use_delta", tool_use_id="t1", partial_json='{"title": "x"}'
            )
            yield StreamChunk(type="tool_use_stop", tool_use_id="t1")
            yield StreamChunk(
                type="usage",
                usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            )
            yield StreamChunk(type="stop", stop_reason="tool_use")

        got = _consume_chat_span(recorder, stream_fn)

        # 聚合结果直接供控制流使用（与落库同源）
        assert got is not None
        assert got.content == "搜"
        assert [b.type for b in got.blocks] == ["thinking", "text", "tool_use"]

        step = next(
            s
            for s in dbm.agent_runs.get_steps("run-stream-rec")
            if s["name"] == "llm_chat"
        )
        assert step["tokens"] == 3
        response = json.loads(step["replay_delta"])["response"]
        assert response["stop_reason"] == "tool_use"
        assert response["content"] == "搜"
        assert response["tool_calls"][0]["id"] == "t1"
        assert [b["type"] for b in response["blocks"]] == [
            "thinking",
            "text",
            "tool_use",
        ]

    def test_chat_span_exception_writes_error_span(self, dbm):
        from app.services.llm.models import StreamChunk

        _ensure_run(dbm, "run-stream-err")
        recorder = TraceRecorder("run-stream-err", start_iteration=0)

        async def stream_fn(messages, *, tools=None, tool_choice=None):
            yield StreamChunk(type="text_delta", text="partial")
            raise RuntimeError("stream boom")

        async def _consume():
            with pytest.raises(RuntimeError, match="stream boom"):
                async with recorder.chat_span() as span:
                    async for chunk in stream_fn(
                        [Message(role="user", content="hi")], tools=[]
                    ):
                        span.feed(chunk)

        asyncio.run(_consume())

        step = next(
            s
            for s in dbm.agent_runs.get_steps("run-stream-err")
            if s["name"] == "llm_chat"
        )
        assert step["status"] == "error"
