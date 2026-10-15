"""app.services.llm.providers.openai_responses 测试。

覆盖 OpenAI Responses API provider 的请求构造、响应解析与流式事件映射。
BDD 场景与测试一一对应。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.services.llm.models import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from app.services.llm.providers.openai_responses import OpenAIResponsesProvider


def _provider(**kwargs) -> OpenAIResponsesProvider:
    params: dict[str, Any] = {
        "api_base": "https://api.openai.com/v1",
        "api_key": "sk-test",
    }
    params.update(kwargs)
    return OpenAIResponsesProvider(**params)


class _AsyncCM:
    """最小异步上下文管理器（模拟 httpx client.stream 返回）。"""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


def _make_stream_client(
    lines: list[str],
    *,
    raise_for_status_side_effect: Exception | None = None,
):
    """创建 mock httpx.AsyncClient（stream 流式路径）。"""
    mock_response = Mock()
    if raise_for_status_side_effect is not None:
        mock_response.raise_for_status = Mock(side_effect=raise_for_status_side_effect)
    else:
        mock_response.raise_for_status = Mock()

    async def _aiter_lines():
        for line in lines:
            yield line

    mock_response.aiter_lines = _aiter_lines

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client

    async def _mock_aexit(*args, **kwargs):
        pass

    mock_client.__aexit__ = _mock_aexit
    mock_client.stream = Mock(return_value=_AsyncCM(mock_response))
    return mock_client


def _sse(name: str, payload: dict, *, with_event_field: bool = True) -> list[str]:
    """构造一条 SSE 事件（含结尾空行边界）。"""
    lines: list[str] = []
    if with_event_field:
        lines.append(f"event: {name}")
    lines.append(f"data: {json.dumps(payload)}")
    lines.append("")
    return lines


async def _collect(provider, messages, **kwargs):
    return [chunk async for chunk in provider.stream(messages, **kwargs)]


# --------------------------------------------------------------------------- #
# 场景 1：请求映射（system/instructions + input 项）
# --------------------------------------------------------------------------- #
class TestBuildRequestMapping:
    """内部消息 → Responses wire 请求体。"""

    def test_system_maps_to_instructions_and_user_to_input(self):
        body = _provider()._build_request(
            [
                Message(role="system", content="你是助手"),
                Message(role="user", content="你好"),
            ]
        )
        assert body["model"] == "gpt-4o-mini"
        assert body["max_output_tokens"] == 2000
        assert body["instructions"] == "你是助手"
        assert body["input"] == [{"role": "user", "content": "你好"}]

    def test_multiple_system_messages_joined(self):
        body = _provider()._build_request(
            [
                Message(role="system", content="A"),
                Message(role="system", content="B"),
                Message(role="user", content="q"),
            ]
        )
        assert body["instructions"] == "A\n\nB"

    def test_no_system_message_omits_instructions(self):
        body = _provider()._build_request([Message(role="user", content="q")])
        assert "instructions" not in body

    def test_assistant_text_and_tool_use_map_to_items(self):
        body = _provider()._build_request(
            [
                Message(
                    role="assistant",
                    content=[
                        TextBlock(text="先查一下"),
                        ToolUseBlock(id="call_1", name="search", input={"q": "x"}),
                    ],
                )
            ]
        )
        assert body["input"][0] == {"role": "assistant", "content": "先查一下"}
        assert body["input"][1] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "search",
            "arguments": json.dumps({"q": "x"}, ensure_ascii=False),
        }

    def test_tool_result_maps_to_function_call_output(self):
        body = _provider()._build_request(
            [
                Message(
                    role="user",
                    content=[ToolResultBlock(tool_use_id="call_1", content="结果A")],
                )
            ]
        )
        assert body["input"] == [
            {"type": "function_call_output", "call_id": "call_1", "output": "结果A"}
        ]

    def test_thinking_block_ignored(self):
        body = _provider()._build_request(
            [
                Message(
                    role="assistant",
                    content=[
                        ThinkingBlock(thinking="内部推理", signature="sig"),
                        TextBlock(text="答案"),
                    ],
                )
            ]
        )
        assert body["input"] == [{"role": "assistant", "content": "答案"}]

    def test_max_tokens_kwarg_overrides_max_output_tokens(self):
        body = _provider()._build_request(
            [Message(role="user", content="q")], max_tokens=123
        )
        assert body["max_output_tokens"] == 123


# --------------------------------------------------------------------------- #
# 场景 2：响应解析（message/function_call/reasoning）
# --------------------------------------------------------------------------- #
class TestToolsFlattening:
    """tools 扁平形态与 tool_choice 归一。"""

    def test_internal_flat_tools_become_flat_responses_tools(self):
        body = _provider()._build_request(
            [Message(role="user", content="q")],
            tools=[
                {
                    "name": "search",
                    "description": "搜索",
                    "parameters": {"type": "object"},
                }
            ],
        )
        assert body["tools"] == [
            {
                "type": "function",
                "name": "search",
                "description": "搜索",
                "parameters": {"type": "object"},
            }
        ]

    def test_openai_wrapped_tools_unwrapped_to_flat(self):
        body = _provider()._build_request(
            [Message(role="user", content="q")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "d",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
        assert body["tools"] == [
            {
                "type": "function",
                "name": "search",
                "description": "d",
                "parameters": {"type": "object"},
            }
        ]

    def test_tool_choice_literals_passthrough(self):
        provider = _provider()
        for choice in ("auto", "required", "none"):
            body = provider._build_request(
                [Message(role="user", content="q")], tool_choice=choice
            )
            assert body["tool_choice"] == choice

    def test_tool_choice_tool_name_objectized(self):
        body = _provider()._build_request(
            [Message(role="user", content="q")], tool_choice="search"
        )
        assert body["tool_choice"] == {"type": "function", "name": "search"}

    def test_tool_choice_none_omitted(self):
        body = _provider()._build_request(
            [Message(role="user", content="q")], tool_choice=None
        )
        assert "tool_choice" not in body


# --------------------------------------------------------------------------- #
# 场景 5：reasoning 参数
# --------------------------------------------------------------------------- #
class TestReasoningParams:
    """thinking_level → reasoning.effort 与 temperature 强制。"""

    def test_thinking_level_medium_maps_effort_and_temperature_one(self):
        body = _provider(model="o4-mini", thinking_level="medium")._build_request(
            [Message(role="user", content="q")]
        )
        assert body["reasoning"] == {"effort": "medium"}
        assert body["temperature"] == 1

    def test_thinking_off_omits_reasoning(self):
        body = _provider(thinking_level="off")._build_request(
            [Message(role="user", content="q")]
        )
        assert "reasoning" not in body

    def test_kwargs_thinking_level_overrides_default(self):
        body = _provider(model="o4-mini", thinking_level="off")._build_request(
            [Message(role="user", content="q")], thinking_level="high"
        )
        assert body["reasoning"] == {"effort": "high"}

    def test_non_reasoning_model_ignores_effort(self):
        """非 o 系列模型不发送 reasoning（对齐 openai_compat 的 ^o\\d 判断）。"""
        body = _provider(model="gpt-4o-mini", thinking_level="high")._build_request(
            [Message(role="user", content="q")]
        )
        assert "reasoning" not in body
        assert body["temperature"] == 0.7


# --------------------------------------------------------------------------- #
# 场景 6：降级
# --------------------------------------------------------------------------- #
class TestDegradation:
    """端点级降级语义。"""

    def test_force_tool_choice_degraded_to_auto(self):
        provider = _provider()
        provider._force_tool_choice_degraded = True
        body = provider._build_request(
            [Message(role="user", content="q")], tool_choice="search"
        )
        assert body["tool_choice"] == "auto"

    def test_extras_disabled_omits_tools_choice_and_reasoning(self):
        provider = _provider(model="o4-mini", thinking_level="high")
        provider._extras_disabled = True
        body = provider._build_request(
            [Message(role="user", content="q")],
            tools=[{"name": "search", "description": "d", "parameters": {}}],
            tool_choice="search",
        )
        assert "tools" not in body
        assert "tool_choice" not in body
        assert "reasoning" not in body


# --------------------------------------------------------------------------- #
# chat 集成（端点 / 认证头）
# --------------------------------------------------------------------------- #
class TestStreamText:
    """流式文本增量。"""

    @pytest.mark.asyncio
    async def test_output_text_deltas_aggregate(self):
        lines = (
            _sse(
                "response.output_text.delta",
                {"type": "response.output_text.delta", "delta": "Hel"},
            )
            + _sse(
                "response.output_text.delta",
                {"type": "response.output_text.delta", "delta": "lo"},
            )
            + _sse(
                "response.completed",
                {"type": "response.completed", "response": {"status": "completed"}},
            )
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert "".join(c.text for c in chunks if c.type == "text_delta") == "Hello"
        assert chunks[-1].type == "stop"
        assert chunks[-1].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_stream_request_has_stream_flag(self):
        lines = _sse(
            "response.completed",
            {"type": "response.completed", "response": {"status": "completed"}},
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await _collect(_provider(), [Message(role="user", content="q")])

        call = mock_client.stream.call_args
        assert call[0][0] == "POST"
        assert call[0][1] == "https://api.openai.com/v1/responses"
        assert call[1]["json"]["stream"] is True


# --------------------------------------------------------------------------- #
# 场景 8：流式工具调用
# --------------------------------------------------------------------------- #
class TestStreamToolCall:
    """流式 function_call 增量。"""

    @pytest.mark.asyncio
    async def test_tool_use_start_and_delta_share_call_id(self):
        lines = (
            _sse(
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "id": "item_1",
                        "call_id": "call_1",
                        "name": "search",
                    },
                },
            )
            + _sse(
                "response.function_call_arguments.delta",
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "item_1",
                    "delta": '{"q":',
                },
            )
            + _sse(
                "response.function_call_arguments.delta",
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "item_1",
                    "delta": '"x"}',
                },
            )
            + _sse(
                "response.completed",
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "search",
                            }
                        ],
                    },
                },
            )
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        starts = [c for c in chunks if c.type == "tool_use_start"]
        assert len(starts) == 1
        assert starts[0].tool_use_id == "call_1"
        assert starts[0].tool_name == "search"

        deltas = [c for c in chunks if c.type == "tool_use_delta"]
        assert all(d.tool_use_id == "call_1" for d in deltas)
        assert "".join(d.partial_json for d in deltas) == '{"q":"x"}'
        assert chunks[-1].stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_reasoning_output_item_ignored(self):
        lines = _sse(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "item": {"type": "reasoning", "id": "r1"},
            },
        ) + _sse(
            "response.output_text.delta",
            {"type": "response.output_text.delta", "delta": "ok"},
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert [c.type for c in chunks] == ["text_delta"]


# --------------------------------------------------------------------------- #
# model 填充与 tool_use_stop 停点事件
# --------------------------------------------------------------------------- #
class TestStreamModelAndToolStop:
    """model 填充（completed/首个可用事件）与 output_item.done → tool_use_stop。"""

    @pytest.mark.asyncio
    async def test_model_from_completed_response(self):
        lines = _sse(
            "response.output_text.delta",
            {"type": "response.output_text.delta", "delta": "hi"},
        ) + _sse(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "model": "gpt-4o-real",
                    "output": [],
                },
            },
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        models = [c.model for c in chunks if c.model]
        assert models == ["gpt-4o-real"]
        # 聚合后 model 保留
        from app.services.llm.models import StreamAggregator

        agg = StreamAggregator()
        for chunk in chunks:
            agg.feed(chunk)
        assert agg.finalize().model == "gpt-4o-real"

    @pytest.mark.asyncio
    async def test_output_item_done_function_call_emits_tool_use_stop(self):
        lines = (
            _sse(
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "id": "item_1",
                        "call_id": "call_1",
                        "name": "search",
                    },
                },
            )
            + _sse(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "id": "item_1",
                        "call_id": "call_1",
                        "name": "search",
                    },
                },
            )
            + _sse(
                "response.completed",
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "search",
                            }
                        ],
                    },
                },
            )
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        stops = [c for c in chunks if c.type == "tool_use_stop"]
        assert len(stops) == 1
        assert stops[0].tool_use_id == "call_1"

    @pytest.mark.asyncio
    async def test_output_item_done_non_function_ignored(self):
        lines = _sse(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "item": {"type": "message", "id": "m1"},
            },
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert not [c for c in chunks if c.type == "tool_use_stop"]


# --------------------------------------------------------------------------- #
# 场景 9：流式 reasoning 摘要
# --------------------------------------------------------------------------- #
class TestStreamReasoningSummary:
    """reasoning summary 增量 → thinking_delta。"""

    @pytest.mark.asyncio
    async def test_reasoning_summary_deltas(self):
        lines = _sse(
            "response.reasoning_summary_text.delta",
            {"type": "response.reasoning_summary_text.delta", "delta": "想"},
        ) + _sse(
            "response.reasoning_summary_text.delta",
            {"type": "response.reasoning_summary_text.delta", "delta": "一下"},
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert "".join(c.thinking for c in chunks if c.type == "thinking_delta") == (
            "想一下"
        )


# --------------------------------------------------------------------------- #
# 场景 10：流式 completed（usage + stop 两分支）
# --------------------------------------------------------------------------- #
class TestStreamCompleted:
    """response.completed → usage + stop。"""

    @pytest.mark.asyncio
    async def test_completed_emits_usage_and_end_turn(self):
        lines = _sse(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [],
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 6,
                        "total_tokens": 11,
                    },
                },
            },
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        usage_chunks = [c for c in chunks if c.type == "usage"]
        assert len(usage_chunks) == 1
        assert usage_chunks[0].usage is not None
        assert usage_chunks[0].usage.prompt_tokens == 5
        assert usage_chunks[0].usage.completion_tokens == 6
        assert usage_chunks[0].usage.total_tokens == 11
        assert chunks[-1].type == "stop"
        assert chunks[-1].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_completed_with_function_call_stop_tool_use(self):
        lines = _sse(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [{"type": "function_call", "call_id": "c1", "name": "f"}],
                },
            },
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert chunks[-1].stop_reason == "tool_use"


# --------------------------------------------------------------------------- #
# 流式 incomplete（截断）与 _stop_reason_of 一致性
# --------------------------------------------------------------------------- #
class TestStreamIncomplete:
    """response.incomplete → usage + max_tokens stop（截断口径一致）。"""

    def test_stop_reason_of_incomplete_maps_max_tokens(self):
        assert (
            OpenAIResponsesProvider._stop_reason_of(
                {"status": "incomplete", "output": []}
            )
            == "max_tokens"
        )

    def test_stop_reason_of_incomplete_with_function_call_prefers_tool_use(self):
        assert (
            OpenAIResponsesProvider._stop_reason_of(
                {
                    "status": "incomplete",
                    "output": [{"type": "function_call", "call_id": "c", "name": "f"}],
                }
            )
            == "tool_use"
        )

    def test_stop_reason_of_completed_maps_end_turn(self):
        assert (
            OpenAIResponsesProvider._stop_reason_of(
                {"status": "completed", "output": []}
            )
            == "end_turn"
        )

    @pytest.mark.asyncio
    async def test_incomplete_event_emits_usage_and_max_tokens(self):
        lines = _sse(
            "response.incomplete",
            {
                "type": "response.incomplete",
                "response": {
                    "status": "incomplete",
                    "output": [],
                    "usage": {
                        "input_tokens": 7,
                        "output_tokens": 8,
                        "total_tokens": 15,
                    },
                },
            },
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        usage_chunks = [c for c in chunks if c.type == "usage"]
        assert len(usage_chunks) == 1
        assert usage_chunks[0].usage is not None
        assert usage_chunks[0].usage.total_tokens == 15
        assert chunks[-1].type == "stop"
        assert chunks[-1].stop_reason == "max_tokens"

    @pytest.mark.asyncio
    async def test_incomplete_event_with_function_call_stop_tool_use(self):
        lines = _sse(
            "response.incomplete",
            {
                "type": "response.incomplete",
                "response": {
                    "status": "incomplete",
                    "output": [{"type": "function_call", "call_id": "c1", "name": "f"}],
                },
            },
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert chunks[-1].stop_reason == "tool_use"


# --------------------------------------------------------------------------- #
# 场景 11：流式失败事件
# --------------------------------------------------------------------------- #
class TestStreamFailure:
    """response.failed / error → ValueError。"""

    @pytest.mark.asyncio
    async def test_response_failed_raises(self):
        lines = _sse(
            "response.failed",
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {"code": "server_error", "message": "boom"},
                },
            },
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError, match="boom"):
                await _collect(_provider(), [Message(role="user", content="q")])

    @pytest.mark.asyncio
    async def test_error_event_raises(self):
        lines = _sse(
            "error",
            {"type": "error", "code": "invalid_request", "message": "bad input"},
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError, match="bad input"):
                await _collect(_provider(), [Message(role="user", content="q")])


# --------------------------------------------------------------------------- #
# 事件名兼容：event 字段 / data.type 二者皆可
# --------------------------------------------------------------------------- #
class TestEventNameCompatibility:
    """SSE 事件名来源兼容（event: 字段或 data JSON 的 type）。"""

    @pytest.mark.asyncio
    async def test_event_name_from_data_type_when_event_field_absent(self):
        lines = _sse(
            "response.output_text.delta",
            {"type": "response.output_text.delta", "delta": "x"},
            with_event_field=False,
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert [c.text for c in chunks] == ["x"]

    @pytest.mark.asyncio
    async def test_event_name_from_event_field_when_data_has_no_type(self):
        lines = _sse(
            "response.output_text.delta",
            {"delta": "y"},
            with_event_field=True,
        )
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert [c.text for c in chunks] == ["y"]

    @pytest.mark.asyncio
    async def test_done_marker_ends_stream(self):
        lines = ["data: [DONE]", ""]
        mock_client = _make_stream_client(lines)
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await _collect(_provider(), [Message(role="user", content="q")])

        assert chunks == []


# --------------------------------------------------------------------------- #
# 场景 12：HTTP 错误
# --------------------------------------------------------------------------- #
class TestHttpErrors:
    """非 2xx → 抛异常（流式路径）。"""

    @pytest.mark.asyncio
    async def test_stream_http_error_raises(self):
        mock_client = _make_stream_client(
            [],
            raise_for_status_side_effect=httpx.HTTPStatusError(
                "HTTP 500", request=Mock(), response=Mock(status_code=500)
            ),
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError, match="500"):
                await _collect(_provider(), [Message(role="user", content="q")])
