"""端到端流式集成测试：真实 httpx 传输层 + 原始 SSE 字节流。

与分层 mock 的既有测试（直接替换 ``provider.stream`` 或 patch ``httpx.AsyncClient``
类）不同，本文件只在 **httpx transport 层**（``httpx.MockTransport``）注入原始 SSE
字节流，真实穿透以下链路：

    provider.stream（httpx.AsyncClient.stream + iter_sse_events 解析）
        → LLMClient.stream_chat（重试 / 降级）
        → StreamAggregator 聚合

因此可捕获「wire 字节 → 归一化事件 → 聚合响应」全链路的真实行为，而非各层各自
mock 后的拼接假象。
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.llm.client import LLMCallError, LLMClient
from app.services.llm.models import Message, StreamAggregator
from app.services.llm.providers.anthropic import AnthropicProvider
from app.services.llm.providers.openai_compat import OpenAICompatProvider
from app.services.llm.providers.openai_responses import OpenAIResponsesProvider

TEST_LLM_CONFIG = {
    "provider": "openai_compat",
    "api_base": "https://test.api.com/v1",
    "api_key": "sk-test-key",
    "model": "gpt-4o-mini",
    "max_tokens": 2000,
    "temperature": 0.7,
    "timeout": 60,
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_llm_singleton():
    """每个测试前后重置 LLMClient 单例。"""
    import app.services.llm.client as client_mod

    client_mod._llm_client = None
    yield
    client_mod._llm_client = None


@pytest.fixture
def mock_log_usage():
    """拦截用量落库，避免污染真实数据库。"""
    with patch("app.core.database.database_manager.llm_usage.log_usage") as mock_log:
        yield mock_log


@pytest.fixture
def mock_sleep():
    """拦截退避睡眠，避免重试路径真实等待。"""
    with patch("app.services.llm.client.asyncio.sleep", AsyncMock()) as mock:
        yield mock


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _sse_bytes(*payloads: str) -> bytes:
    """把若干 data 负载拼成原始 SSE 字节流（每事件后跟空行边界）。"""
    return "".join(f"data: {p}\n\n" for p in payloads).encode("utf-8")


def _event_sse_bytes(*events: tuple[str, str]) -> bytes:
    """把若干 (event_name, data) 拼成带 event 字段的原始 SSE 字节流。"""
    return "".join(f"event: {name}\ndata: {data}\n\n" for name, data in events).encode(
        "utf-8"
    )


def _mock_transport_client(handler):
    """返回一个替换 create_async_client 的工厂：使用真实 AsyncClient + MockTransport。

    保留真实 httpx.AsyncClient（含 stream / aiter_lines 行为），仅把网络层替换为
    MockTransport，从而在 transport 层注入原始字节流。
    """

    def _factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return _factory


def _sse_response(body: bytes, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"content-type": "text/event-stream"},
        content=body,
    )


def _build_client(provider) -> LLMClient:
    """构造 LLMClient 并注入指定 provider（绕过 _PROVIDER_MAP 注册）。"""
    with patch(
        "app.services.llm.client.config_manager.get_llm_config",
        return_value=dict(TEST_LLM_CONFIG),
    ):
        client = LLMClient()
    client._provider = provider
    return client


async def _collect(client, messages, **kwargs):
    return [chunk async for chunk in client.stream_chat(messages, **kwargs)]


def _aggregate(chunks):
    agg = StreamAggregator()
    for chunk in chunks:
        agg.feed(chunk)
    return agg.finalize()


# ---------------------------------------------------------------------------
# 用例 A：openai_compat 原始 SSE
# ---------------------------------------------------------------------------


class TestOpenAICompatRawSSE:
    """用例 A：OpenAI /chat/completions 原始 SSE 字节 → 事件序列 + 聚合。"""

    _BODY = _sse_bytes(
        json.dumps({"choices": [{"index": 0, "delta": {"content": "Hello"}}]}),
        json.dumps({"choices": [{"index": 0, "delta": {"content": ", world"}}]}),
        json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        json.dumps(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        ),
        "[DONE]",
    )

    @pytest.mark.asyncio
    async def test_raw_sse_to_events_and_aggregate(
        self, reset_llm_singleton, mock_log_usage, mock_sleep
    ):
        """原始字节流穿透 provider.stream → 归一化事件序列正确。"""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return _sse_response(self._BODY)

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
        )
        client = _build_client(provider)

        with patch(
            "app.services.llm.providers.openai_compat.create_async_client",
            _mock_transport_client(handler),
        ):
            chunks = await _collect(
                client, [Message(role="user", content="hi")], job_name="t"
            )

        assert seen == ["/v1/chat/completions"]
        assert [c.type for c in chunks] == [
            "text_delta",
            "text_delta",
            "stop",
            "usage",
        ]
        assert [c.text for c in chunks if c.type == "text_delta"] == [
            "Hello",
            ", world",
        ]

        resp = _aggregate(chunks)
        assert resp.content == "Hello, world"
        assert resp.stop_reason == "stop"
        assert resp.usage is not None
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.completion_tokens == 5
        assert resp.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_collect_aggregates_raw_sse(
        self, reset_llm_singleton, mock_log_usage, mock_sleep
    ):
        """collect(stream_chat()) 对同一原始字节流聚合出等价 ChatResponse。"""
        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
        )
        client = _build_client(provider)

        with patch(
            "app.services.llm.providers.openai_compat.create_async_client",
            _mock_transport_client(lambda request: _sse_response(self._BODY)),
        ):
            resp = _aggregate(
                await _collect(client, [Message(role="user", content="hi")])
            )

        assert resp.content == "Hello, world"
        assert resp.stop_reason == "stop"
        assert resp.usage is not None
        assert resp.usage.total_tokens == 15
        # 正常耗尽 → 落库一次
        mock_log_usage.assert_called_once()
        assert mock_log_usage.call_args[1]["status"] == "success"


# ---------------------------------------------------------------------------
# 用例 B：anthropic 原始 SSE（含 thinking + signature）
# ---------------------------------------------------------------------------


class TestAnthropicRawSSE:
    """用例 B：Anthropic Messages 原始 SSE → thinking + signature + 文本。"""

    _BODY = _event_sse_bytes(
        (
            "message_start",
            json.dumps(
                {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": 7, "output_tokens": 0}},
                }
            ),
        ),
        (
            "content_block_start",
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""},
                }
            ),
        ),
        (
            "content_block_delta",
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "思考中"},
                }
            ),
        ),
        (
            "content_block_delta",
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "signature_delta", "signature": "sig-abc"},
                }
            ),
        ),
        (
            "content_block_start",
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text", "text": ""},
                }
            ),
        ),
        (
            "content_block_delta",
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "你好"},
                }
            ),
        ),
        (
            "content_block_delta",
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "，世界"},
                }
            ),
        ),
        (
            "message_delta",
            json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 12},
                }
            ),
        ),
        ("message_stop", json.dumps({"type": "message_stop"})),
    )

    @pytest.mark.asyncio
    async def test_raw_sse_to_events_and_aggregate(
        self, reset_llm_singleton, mock_log_usage, mock_sleep
    ):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return _sse_response(self._BODY)

        provider = AnthropicProvider(
            api_base="https://test.api.com/v1",
            api_key="sk-test",
            model="claude-sonnet-4-6",
        )
        client = _build_client(provider)

        with patch(
            "app.services.llm.providers.anthropic.create_async_client",
            _mock_transport_client(handler),
        ):
            chunks = await _collect(
                client, [Message(role="user", content="hi")], job_name="t"
            )

        assert seen == ["/v1/messages"]
        assert [c.type for c in chunks] == [
            "thinking_delta",
            "thinking_delta",
            "text_delta",
            "text_delta",
            "usage",
            "stop",
        ]
        # signature_delta 单独产出 thinking_delta（仅填 signature）
        assert [c.signature for c in chunks if c.signature] == ["sig-abc"]

        resp = _aggregate(chunks)
        assert resp.content == "你好，世界"
        assert resp.stop_reason == "end_turn"
        assert [b.type for b in resp.blocks] == ["thinking", "text"]
        assert resp.blocks[0].thinking == "思考中"
        assert resp.blocks[0].signature == "sig-abc"
        assert resp.usage is not None
        assert resp.usage.prompt_tokens == 7
        assert resp.usage.completion_tokens == 12
        assert resp.usage.total_tokens == 19


# ---------------------------------------------------------------------------
# 用例 C：openai_responses 原始 SSE（工具调用聚合）
# ---------------------------------------------------------------------------


class TestOpenAIResponsesRawSSE:
    """用例 C：Responses API 原始 SSE → 工具调用 start/delta → 聚合。"""

    _BODY = _event_sse_bytes(
        (
            "response.output_item.added",
            json.dumps(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "id": "item_1",
                        "type": "function_call",
                        "call_id": "call_xyz",
                        "name": "get_weather",
                    },
                }
            ),
        ),
        (
            "response.function_call_arguments.delta",
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "item_1",
                    "delta": '{"city":',
                }
            ),
        ),
        (
            "response.function_call_arguments.delta",
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "item_1",
                    "delta": '"Tokyo"}',
                }
            ),
        ),
        (
            "response.completed",
            json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_xyz",
                                "name": "get_weather",
                            }
                        ],
                        "usage": {
                            "input_tokens": 9,
                            "output_tokens": 4,
                            "total_tokens": 13,
                        },
                    },
                }
            ),
        ),
    )

    @pytest.mark.asyncio
    async def test_raw_sse_tool_call_aggregate(
        self, reset_llm_singleton, mock_log_usage, mock_sleep
    ):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return _sse_response(self._BODY)

        provider = OpenAIResponsesProvider(
            api_base="https://test.api.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
        )
        client = _build_client(provider)

        with patch(
            "app.services.llm.providers.openai_responses.create_async_client",
            _mock_transport_client(handler),
        ):
            chunks = await _collect(
                client, [Message(role="user", content="weather?")], job_name="t"
            )

        assert seen == ["/v1/responses"]
        assert [c.type for c in chunks] == [
            "tool_use_start",
            "tool_use_delta",
            "tool_use_delta",
            "usage",
            "stop",
        ]
        starts = [c for c in chunks if c.type == "tool_use_start"]
        assert starts[0].tool_use_id == "call_xyz"
        assert starts[0].tool_name == "get_weather"

        resp = _aggregate(chunks)
        assert resp.stop_reason == "tool_use"
        tool_blocks = [b for b in resp.blocks if b.type == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].id == "call_xyz"
        assert tool_blocks[0].name == "get_weather"
        assert tool_blocks[0].input == {"city": "Tokyo"}
        assert resp.usage is not None
        assert resp.usage.total_tokens == 13


# ---------------------------------------------------------------------------
# 用例 D：错误路径（HTTP 400 参数拒绝 → 降级重试）
# ---------------------------------------------------------------------------


class TestRawSSEErrorRetry:
    """用例 D：transport 层返回 400 参数拒绝 → client 降级重试。"""

    _OK_BODY = _sse_bytes(
        json.dumps({"choices": [{"index": 0, "delta": {"content": "ok"}}]}),
        json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        "[DONE]",
    )
    _REJECT_TEXT = (
        '{"error": "Unrecognized request argument supplied: reasoning_effort"}'
    )

    @pytest.mark.asyncio
    async def test_param_rejection_then_retry_succeeds(
        self, reset_llm_singleton, mock_log_usage, mock_sleep
    ):
        """首个 400（参数拒绝）→ 降级并立即重试 → 第二次成功；共 2 次请求。"""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(400, text=self._REJECT_TEXT)
            return _sse_response(self._OK_BODY)

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1",
            api_key="sk-test",
            thinking_level="high",
        )
        client = _build_client(provider)

        with patch(
            "app.services.llm.providers.openai_compat.create_async_client",
            _mock_transport_client(handler),
        ):
            resp = _aggregate(
                await _collect(client, [Message(role="user", content="Q")])
            )

        assert resp.content == "ok"
        assert calls["n"] == 2  # 降级后立即重试，无退避
        assert provider._extras_disabled is True
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_param_rejection_persists_terminal(
        self, reset_llm_singleton, mock_log_usage, mock_sleep
    ):
        """降级后仍返回 400 参数拒绝 → 终态 LLMCallError（retryable=False），2 次。"""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(400, text=self._REJECT_TEXT)

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1",
            api_key="sk-test",
            thinking_level="high",
        )
        client = _build_client(provider)

        with patch(
            "app.services.llm.providers.openai_compat.create_async_client",
            _mock_transport_client(handler),
        ):
            with pytest.raises(LLMCallError) as exc_info:
                await _collect(client, [Message(role="user", content="Q")])

        assert calls["n"] == 2
        assert exc_info.value.retryable is False
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stream_rejection_raises_terminal_error(
        self, reset_llm_singleton, mock_log_usage, mock_sleep
    ):
        """stream 参数被拒 → 确定性 LLMCallError（不重试、不降级、不伪装）。"""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(400, text='{"error": "stream is not supported"}')

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk-test"
        )
        client = _build_client(provider)

        with patch(
            "app.services.llm.providers.openai_compat.create_async_client",
            _mock_transport_client(handler),
        ):
            with pytest.raises(LLMCallError) as exc_info:
                await _collect(client, [Message(role="user", content="Q")])

        assert exc_info.value.retryable is False
        assert "端点不支持流式调用" in str(exc_info.value)
        assert calls["n"] == 1  # 只发一次，不重试
        mock_sleep.assert_not_awaited()
