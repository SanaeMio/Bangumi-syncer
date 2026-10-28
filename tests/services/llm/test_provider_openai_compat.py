"""app.services.llm.providers.openai_compat 测试。"""

import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.services.llm.models import Message, StreamAggregator
from app.services.llm.providers.openai_compat import OpenAICompatProvider


def _make_mock_client(  # noqa: PLR0913
    *,
    status_code: int = 200,
    json_body: dict | None = None,
    json_side_effect: Exception | None = None,
    post_side_effect: Exception | None = None,
    raise_for_status_side_effect: Exception | None = None,
):
    """创建一个 mock httpx.AsyncClient，准备用于 `async with`。"""

    mock_response = Mock()
    mock_response.status_code = status_code
    if json_side_effect is not None:
        mock_response.json = Mock(side_effect=json_side_effect)
    else:
        mock_response.json = Mock(return_value=json_body or {})

    if raise_for_status_side_effect is not None:
        mock_response.raise_for_status = Mock(side_effect=raise_for_status_side_effect)
    else:
        mock_response.raise_for_status = Mock()

    mock_client = AsyncMock()
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=mock_response)

    mock_client.aclose = AsyncMock()

    # 确保 async with 返回同一个 mock_client，
    # 且 __aexit__ 调用 aclose（匹配真实 httpx 行为）。
    mock_client.__aenter__.return_value = mock_client

    async def _mock_aexit(*args, **kwargs):
        await mock_client.aclose()

    mock_client.__aexit__ = _mock_aexit

    return mock_client


class TestOpenAICompatProviderInit:
    """构造函数和默认值。"""

    def test_default_values(self):
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1", api_key="sk-test"
        )
        assert provider.api_base == "https://api.openai.com/v1"
        assert provider.api_key == "sk-test"
        assert provider.model == "gpt-4o-mini"
        assert provider.max_tokens == 2000
        assert provider.temperature == 0.7
        assert provider.timeout == 60

    def test_custom_values(self):
        provider = OpenAICompatProvider(
            api_base="https://custom.api/v1",
            api_key="sk-custom",
            model="custom-model",
            max_tokens=500,
            temperature=0.3,
            timeout=30,
        )
        assert provider.model == "custom-model"
        assert provider.max_tokens == 500
        assert provider.temperature == 0.3
        assert provider.timeout == 30


class TestOpenAICompatBuildRequest:
    """：_build_request / _to_wire_messages / reasoning_effort 映射。"""

    def _provider(self, thinking_level="off", model="gpt-4o-mini"):
        return OpenAICompatProvider(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model=model,
            thinking_level=thinking_level,
        )

    def test_basic_request_shape(self):
        body = self._provider()._build_request([Message(role="user", content="Q")])
        assert body["model"] == "gpt-4o-mini"
        assert body["messages"] == [{"role": "user", "content": "Q"}]
        assert "reasoning_effort" not in body  # off 不传 = 现状行为

    def test_system_multi_block_joins_with_paragraph_separator(self):
        """system 多块以 ``\\n\\n`` 连接：多块本就代表多条独立规则，需保留边界。

        与 Anthropic 侧 ``_system_text`` 同一分隔语义。
        """
        from app.services.llm.models import TextBlock

        msg = Message(
            role="system",
            content=[TextBlock(text="第一段"), TextBlock(text="第二段")],
        )
        wire = self._provider()._to_wire_messages(msg)
        assert wire == [{"role": "system", "content": "第一段\n\n第二段"}]

    @pytest.mark.parametrize("role", ["assistant", "user"])
    def test_assistant_and_user_multi_block_join_without_separator(self, role):
        """assistant / user 多块**空串连接**：忠实还原 wire 形态，不注入分隔符。

        这些角色的多段文本是同一次发言被 wire 切开的结果（模型发出的是连续
        delta），注入 ``\\n\\n`` 会凭空改写语义。若将来引入多轮会话复用，
        这些消息会被发回 provider，分隔符即污染。
        """
        from app.services.llm.models import TextBlock

        msg = Message(
            role=role,
            content=[TextBlock(text="A"), TextBlock(text="B")],
        )
        wire = self._provider()._to_wire_messages(msg)
        assert wire == [{"role": role, "content": "AB"}]

    @pytest.mark.parametrize(
        "level,expected", [("low", "low"), ("medium", "medium"), ("high", "high")]
    )
    def test_reasoning_effort_o_series(self, level, expected):
        provider = self._provider(thinking_level=level, model="o4-mini")
        assert provider._reasoning_effort(level, "o4-mini") == expected
        body = provider._build_request([Message(role="user", content="Q")])
        assert body["reasoning_effort"] == expected

    def test_reasoning_effort_non_o_series_ignored(self):
        provider = self._provider(thinking_level="high", model="gpt-4o-mini")
        assert provider._reasoning_effort("high", "gpt-4o-mini") is None
        body = provider._build_request([Message(role="user", content="Q")])
        assert "reasoning_effort" not in body

    def test_reasoning_effort_kwargs_override(self):
        provider = self._provider(thinking_level="off", model="o3")
        body = provider._build_request(
            [Message(role="user", content="Q")], thinking_level="medium"
        )
        assert body["reasoning_effort"] == "medium"


class TestReasoningTemperatureAlignment:
    """o 系列发 reasoning_effort 时 temperature 强制 1（与 Anthropic 侧对齐），
    避免推理模型对非 1 temperature 的硬 400。"""

    def test_o_series_forces_temperature_one(self):
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model="o4-mini",
            thinking_level="high",
        )
        body = provider._build_request([Message(role="user", content="Q")])
        assert body["reasoning_effort"] == "high"
        assert body["temperature"] == 1  # 硬 400 修复点

    def test_non_o_series_keeps_configured_temperature(self):
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
            temperature=0.7,
            thinking_level="off",
        )
        body = provider._build_request([Message(role="user", content="Q")])
        assert body["temperature"] == 0.7

    def test_o_series_user_temperature_overridden(self):
        """用户显式传 temperature=0.2 也被强制为 1（推理模型不接受非 1）。"""
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model="o3",
            thinking_level="low",
        )
        body = provider._build_request(
            [Message(role="user", content="Q")], temperature=0.2
        )
        assert body["temperature"] == 1


class _AsyncStreamCtx:
    """辅助：让 mock 的 client.stream(...) 支持 `async with`。"""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        return False


def _sse_lines(*payloads: str) -> list[str]:
    """把若干 data 负载拼成 SSE 文本行（每事件后跟空行边界）。"""
    lines: list[str] = []
    for payload in payloads:
        lines.append(f"data: {payload}")
        lines.append("")
    return lines


def _make_mock_stream_client(
    *,
    sse_lines: list[str],
    status_code: int = 200,
    is_success: bool = True,
    raise_for_status_side_effect: Exception | None = None,
):
    """创建支持 `client.stream(...)` 的 mock httpx.AsyncClient。"""
    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.is_success = is_success
    if raise_for_status_side_effect is not None:
        mock_response.raise_for_status = Mock(side_effect=raise_for_status_side_effect)
    else:
        mock_response.raise_for_status = Mock()
    mock_response.aread = AsyncMock(return_value=b"error body")

    async def _aiter_lines():
        for line in sse_lines:
            yield line

    mock_response.aiter_lines = _aiter_lines

    mock_client = AsyncMock()
    mock_client.stream = Mock(return_value=_AsyncStreamCtx(mock_response))
    mock_client.aclose = AsyncMock()
    mock_client.__aenter__.return_value = mock_client

    async def _mock_aexit(*args, **kwargs):
        await mock_client.aclose()

    mock_client.__aexit__ = _mock_aexit
    return mock_client


def _chunk_payload(
    delta: dict, finish_reason: str | None = None, model: str | None = None
) -> str:
    """构造一个 OpenAI chat.completion.chunk 的 data 负载。"""
    choice: dict = {"delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    data: dict = {"choices": [choice]}
    if model is not None:
        data["model"] = model
    return json.dumps(data)


class TestOpenAICompatProviderStream:
    """OpenAI 兼容 provider 的 SSE 流式实现。"""

    def _provider(self, **kwargs) -> OpenAICompatProvider:
        return OpenAICompatProvider(
            api_base="https://api.openai.com/v1", api_key="sk-test", **kwargs
        )

    async def _collect(self, provider, messages, **kwargs):
        return [chunk async for chunk in provider.stream(messages, **kwargs)]

    async def test_text_deltas_yield_text_delta_and_aggregate(self):
        """文本增量：多个 content delta → 依次产出 text_delta，聚合后等于拼接。"""
        payloads = [
            _chunk_payload({"content": "Hello"}),
            _chunk_payload({"content": ", "}),
            _chunk_payload({"content": "world"}),
            _chunk_payload({}, finish_reason="stop"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="hi")])

        text_chunks = [c for c in chunks if c.type == "text_delta"]
        assert [c.text for c in text_chunks] == ["Hello", ", ", "world"]

        agg = StreamAggregator()
        for chunk in chunks:
            agg.feed(chunk)
        assert agg.finalize().content == "Hello, world"

    async def test_tool_call_fragments_yield_start_then_deltas(self):
        """工具调用分片：首个 chunk 带 id+name → start，后续 arguments → delta。"""
        payloads = [
            _chunk_payload(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": ""},
                        }
                    ]
                }
            ),
            _chunk_payload(
                {"tool_calls": [{"index": 0, "function": {"arguments": '{"city":'}}]}
            ),
            _chunk_payload(
                {"tool_calls": [{"index": 0, "function": {"arguments": '"Tokyo"}'}}]}
            ),
            _chunk_payload({}, finish_reason="tool_calls"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(
                provider, [Message(role="user", content="weather?")]
            )

        starts = [c for c in chunks if c.type == "tool_use_start"]
        assert len(starts) == 1
        assert starts[0].tool_use_id == "call_abc"
        assert starts[0].tool_name == "get_weather"

        deltas = [c for c in chunks if c.type == "tool_use_delta"]
        assert [c.partial_json for c in deltas] == ['{"city":', '"Tokyo"}']
        assert all(c.tool_use_id == "call_abc" for c in deltas)

        agg = StreamAggregator()
        for chunk in chunks:
            agg.feed(chunk)
        resp = agg.finalize()
        assert resp.stop_reason == "tool_use"
        tool_blocks = [b for b in resp.blocks if b.type == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].id == "call_abc"
        assert tool_blocks[0].name == "get_weather"
        assert tool_blocks[0].input == {"city": "Tokyo"}

    async def test_tool_call_missing_id_falls_back_to_call_index(self):
        """工具调用 id 兜底：首个 chunk 无 id → tool_use_id == call_0。"""
        payloads = [
            _chunk_payload(
                {
                    "tool_calls": [
                        {"index": 0, "function": {"name": "noop", "arguments": "{}"}}
                    ]
                }
            ),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        starts = [c for c in chunks if c.type == "tool_use_start"]
        assert len(starts) == 1
        assert starts[0].tool_use_id == "call_0"

    async def test_first_fragment_without_name_still_emits_start(self):
        """首分片只有 index+id（无 name）→ 仍建槽并 emit start，次分片 delta 带真实 id。

        否则后续 arguments 分片会产出无前置 start 的孤立 delta，且 id 丢失
        （旧代码回退 ``call_0``），下游聚合只能自愈并产生 warning 噪声。
        """
        payloads = [
            _chunk_payload(
                {"tool_calls": [{"index": 0, "id": "call_xyz"}]},
            ),
            _chunk_payload(
                {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}
            ),
            _chunk_payload({}, finish_reason="tool_calls"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        events = [c for c in chunks if c.type in ("tool_use_start", "tool_use_delta")]
        assert [c.type for c in events] == ["tool_use_start", "tool_use_delta"]
        assert events[0].tool_use_id == "call_xyz"
        assert events[0].tool_name == ""
        assert events[1].tool_use_id == "call_xyz"
        assert events[1].partial_json == "{}"

        # 下游聚合无「缺少 start 自愈」告警
        agg = StreamAggregator()
        for chunk in chunks:
            agg.feed(chunk)
        resp = agg.finalize()
        tool_blocks = [b for b in resp.blocks if b.type == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].id == "call_xyz"

    async def test_finish_reason_tool_calls_maps_to_tool_use(self):
        """finish_reason=tool_calls → stop_reason=tool_use（与 _parse_response 一致）。"""
        payloads = [
            _chunk_payload({}, finish_reason="tool_calls"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        stops = [c for c in chunks if c.type == "stop"]
        assert len(stops) == 1
        assert stops[0].stop_reason == "tool_use"

    async def test_finish_reason_stop_passthrough(self):
        """finish_reason=stop → stop_reason=stop（与 _parse_response 其余透传一致）。"""
        payloads = [
            _chunk_payload({"content": "ok"}, finish_reason="stop"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        stops = [c for c in chunks if c.type == "stop"]
        assert len(stops) == 1
        assert stops[0].stop_reason == "stop"

    async def test_model_field_populated_from_first_chunk(self):
        """model 填充：任一 chunk 顶层 model → 首个产出事件带上真实模型名。"""
        payloads = [
            _chunk_payload({"content": "Hello"}, model="gpt-4o-real"),
            _chunk_payload({"content": " world"}, model="gpt-4o-real"),
            _chunk_payload({}, finish_reason="stop", model="gpt-4o-real"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        assert chunks[0].model == "gpt-4o-real"
        # 只填一次：后续事件不再携带 model
        assert all(c.model == "" for c in chunks[1:])

        agg = StreamAggregator()
        for chunk in chunks:
            agg.feed(chunk)
        assert agg.finalize().model == "gpt-4o-real"

    async def test_tool_use_stop_emitted_before_stop(self):
        """openai_compat 无 per-tool 停点：finish_reason 时为已收到 tool_calls 补发 stop。"""
        payloads = [
            _chunk_payload(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_abc",
                            "function": {"name": "f", "arguments": "{}"},
                        }
                    ]
                }
            ),
            _chunk_payload({}, finish_reason="tool_calls"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        types = [c.type for c in chunks]
        assert "tool_use_stop" in types
        assert types.index("tool_use_stop") < types.index("stop")
        stop_chunks = [c for c in chunks if c.type == "tool_use_stop"]
        assert len(stop_chunks) == 1
        assert stop_chunks[0].tool_use_id == "call_abc"

    async def test_tool_use_stop_not_duplicated(self):
        """重复 finish_reason 不重复补发 tool_use_stop。"""
        payloads = [
            _chunk_payload(
                {"tool_calls": [{"index": 0, "id": "c0", "function": {"name": "f"}}]}
            ),
            _chunk_payload({}, finish_reason="tool_calls"),
            _chunk_payload({}, finish_reason="tool_calls"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        stop_chunks = [c for c in chunks if c.type == "tool_use_stop"]
        assert len(stop_chunks) == 1

    async def test_no_tool_use_stop_without_tool_calls(self):
        """无 tool_calls 时不产出 tool_use_stop。"""
        payloads = [
            _chunk_payload({"content": "ok"}, finish_reason="stop"),
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        assert not [c for c in chunks if c.type == "tool_use_stop"]

    async def test_usage_event_from_final_chunk(self):
        """usage 事件：末 chunk 含 usage → 产出 usage 事件且 token 数正确。"""
        usage_payload = json.dumps(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 22,
                    "total_tokens": 33,
                },
            }
        )
        payloads = [
            _chunk_payload({"content": "hi"}),
            _chunk_payload({}, finish_reason="stop"),
            usage_payload,
            "[DONE]",
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        usage_chunks = [c for c in chunks if c.type == "usage"]
        assert len(usage_chunks) == 1
        usage = usage_chunks[0].usage
        assert usage is not None
        assert usage.prompt_tokens == 11
        assert usage.completion_tokens == 22
        assert usage.total_tokens == 33

    async def test_done_marker_terminates_iteration(self):
        """[DONE] 终止：其后的事件不再产出。"""
        payloads = [
            _chunk_payload({"content": "first"}),
            "[DONE]",
            _chunk_payload({"content": "after-done"}),
        ]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            chunks = await self._collect(provider, [Message(role="user", content="x")])

        assert [c.text for c in chunks if c.type == "text_delta"] == ["first"]

    async def test_request_body_sets_stream_and_reuses_build_request(self):
        """请求体校验：stream/stream_options 注入，tools/reasoning_effort 与 chat() 一致。"""
        payloads = [_chunk_payload({"content": "ok"}), "[DONE]"]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider(model="o4-mini", thinking_level="high")
            await self._collect(
                provider,
                [Message(role="user", content="Q")],
                tools=[
                    {
                        "name": "get_weather",
                        "description": "查天气",
                        "parameters": {"type": "object"},
                    }
                ],
                tool_choice="get_weather",
            )

        stream_call = mock_client.stream.call_args
        assert stream_call[0][0] == "POST"
        assert stream_call[0][1] == "https://api.openai.com/v1/chat/completions"
        body = stream_call[1]["json"]
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        # reasoning_effort 映射 + o 系列 temperature 强制 1（与 chat() 一致）
        assert body["reasoning_effort"] == "high"
        assert body["temperature"] == 1
        # tools / tool_choice 规范化（与 chat() 一致）
        assert body["tools"][0]["type"] == "function"
        assert body["tools"][0]["function"]["name"] == "get_weather"
        assert body["tool_choice"] == {
            "type": "function",
            "function": {"name": "get_weather"},
        }

    async def test_request_body_honors_extras_disabled(self):
        """extras_disabled=True 的本次调用不发送 reasoning_effort。"""
        payloads = [_chunk_payload({"content": "ok"}), "[DONE]"]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider(model="o4-mini", thinking_level="high")
            await self._collect(
                provider,
                [Message(role="user", content="Q")],
                extras_disabled=True,
            )

        body = mock_client.stream.call_args[1]["json"]
        assert "reasoning_effort" not in body
        assert body["stream"] is True

    async def test_request_body_honors_force_tool_choice_degraded(self):
        """force_tool_choice_degraded=True 时强制 tool_choice 降级为 auto。"""
        payloads = [_chunk_payload({"content": "ok"}), "[DONE]"]
        mock_client = _make_mock_stream_client(sse_lines=_sse_lines(*payloads))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            await self._collect(
                provider,
                [Message(role="user", content="Q")],
                tools=[{"name": "get_weather", "description": "查天气"}],
                tool_choice="get_weather",
                force_tool_choice_degraded=True,
            )

        body = mock_client.stream.call_args[1]["json"]
        assert body["tool_choice"] == "auto"

    async def test_http_error_status_raises(self):
        """HTTP 错误：非 2xx → 抛 httpx.HTTPStatusError（不吞）。"""
        mock_client = _make_mock_stream_client(
            sse_lines=[],
            status_code=500,
            is_success=False,
            raise_for_status_side_effect=httpx.HTTPStatusError(
                "error",
                request=Mock(),
                response=Mock(status_code=500),
            ),
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = self._provider()
            with pytest.raises(httpx.HTTPStatusError):
                await self._collect(provider, [Message(role="user", content="Q")])


class TestOpenAICompatAssistantTextFidelity:
    """assistant/user 多段文本块的分隔语义（与 ChatResponse.content 同口径）。

    chat completions 的 assistant 消息 content 是**单个字符串**，协议无法表达
    「文本 → 工具调用 → 文本」的交错，只能降级为拼接。拼接必须与聚合器
    ``ChatResponse.content`` 同口径（空串连接），不得注入 wire 上不存在的
    ``"\\n\\n"``——否则模型看到的是「A\\n\\nB 然后调工具」，而实际是
    「A 调工具，然后说 B」，语义被改写。
    """

    def _provider(self, **kwargs) -> OpenAICompatProvider:
        params = {
            "api_base": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
        }
        params.update(kwargs)
        return OpenAICompatProvider(**params)

    def test_interleaved_text_joined_without_injected_separator(self):
        """assistant 交错文本不以 \\n\\n 合并，与 content 口径一致。"""
        from app.services.llm.models import TextBlock, ToolUseBlock

        msg = Message(
            role="assistant",
            content=[
                TextBlock(text="A"),
                ToolUseBlock(id="t1", name="f", input={}),
                TextBlock(text="B"),
            ],
        )
        wire = self._provider()._to_wire_messages(msg)

        assert len(wire) == 1
        assert wire[0]["content"] == "AB"
        assert len(wire[0]["tool_calls"]) == 1

    def test_single_text_block_unchanged(self):
        """单段文本行为不变（无额外分隔符）。"""
        from app.services.llm.models import TextBlock

        msg = Message(role="assistant", content=[TextBlock(text="只有一段")])
        wire = self._provider()._to_wire_messages(msg)

        assert wire == [{"role": "assistant", "content": "只有一段"}]

    def test_text_around_tool_result_joined_without_separator(self):
        """user 消息中工具结果两侧的文本同样不注入分隔符。"""
        from app.services.llm.models import TextBlock, ToolResultBlock

        msg = Message(
            role="user",
            content=[
                TextBlock(text="前"),
                ToolResultBlock(tool_use_id="t1", content="r"),
                TextBlock(text="后"),
            ],
        )
        wire = self._provider()._to_wire_messages(msg)

        assert wire[0] == {"role": "user", "content": "前后"}
        assert wire[1] == {"role": "tool", "tool_call_id": "t1", "content": "r"}

    def test_system_multi_block_keeps_paragraph_separator(self):
        """system 多块的 \\n\\n 分隔是**既有语义**（多条 system 合并），保持不变。

        区别在于：system 的多块本就代表多条独立规则，用空串连接会把两条规则
        粘成一段，反而更糟。
        """
        from app.services.llm.models import TextBlock

        msg = Message(
            role="system", content=[TextBlock(text="规则A"), TextBlock(text="规则B")]
        )
        wire = self._provider()._to_wire_messages(msg)

        assert wire == [{"role": "system", "content": "规则A\n\n规则B"}]
