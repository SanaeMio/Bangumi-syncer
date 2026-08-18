"""app.services.llm.providers.anthropic 测试。"""

from typing import Optional
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.services.llm.models import (
    ChatResponse,
    Message,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ThinkingLevel,
)
from app.services.llm.providers.anthropic import AnthropicProvider


def _make_mock_client(  # noqa: PLR0913
    *,
    status_code: int = 200,
    json_body: Optional[dict] = None,
    json_side_effect: Optional[Exception] = None,
    post_side_effect: Optional[Exception] = None,
    raise_for_status_side_effect: Optional[Exception] = None,
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

    mock_client.__aenter__.return_value = mock_client

    async def _mock_aexit(*args, **kwargs):
        await mock_client.aclose()

    mock_client.__aexit__ = _mock_aexit

    return mock_client


def _make_provider(
    *,
    api_base: str = "https://api.anthropic.com/v1",
    api_key: str = "sk-test",
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 2000,
    temperature: float = 0.7,
    timeout: int = 60,
    thinking_level: ThinkingLevel = "off",
) -> AnthropicProvider:
    """构造测试 provider，仅允许覆盖需要调整的参数。"""
    return AnthropicProvider(
        api_base=api_base,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        thinking_level=thinking_level,
    )


class TestAnthropicProviderInit:
    """构造函数和默认值。"""

    def test_default_values(self):
        provider = _make_provider()
        assert provider.api_base == "https://api.anthropic.com/v1"
        assert provider.api_key == "sk-test"
        assert provider.model == "claude-sonnet-4-6"
        assert provider.max_tokens == 2000
        assert provider.temperature == 0.7
        assert provider.timeout == 60
        assert provider.thinking_level == "off"

    def test_custom_values(self):
        provider = AnthropicProvider(
            api_base="https://custom.api/v1/",
            api_key="sk-custom",
            model="custom-model",
            max_tokens=500,
            temperature=0.3,
            timeout=30,
            thinking_level="medium",
        )
        # api_base 尾斜杠被去除
        assert provider.api_base == "https://custom.api/v1"
        assert provider.model == "custom-model"
        assert provider.max_tokens == 500
        assert provider.temperature == 0.3
        assert provider.timeout == 30
        assert provider.thinking_level == "medium"


# ===================================================================
# Feature 1: 请求构建
# ===================================================================


class TestBuildRequest:
    """_build_request 纯函数测试。"""

    def test_text_message_wire_format(self):
        """Scenario 1.1: 纯文本请求符合 Messages API 格式。"""
        provider = _make_provider()
        body = provider._build_request([Message(role="user", content="Hello")])
        assert body["model"] == "claude-sonnet-4-6"
        assert body["max_tokens"] == 2000
        assert body["temperature"] == 0.7
        assert body["messages"] == [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
        ]
        assert "system" not in body
        assert "thinking" not in body

    def test_temperature_kwargs_override(self):
        """Scenario 1.1: temperature kwargs 覆盖配置值。"""
        provider = _make_provider()
        body = provider._build_request(
            [Message(role="user", content="Hello")], temperature=0.3
        )
        assert body["temperature"] == 0.3

    def test_system_prompt_top_level(self):
        """Scenario 1.2: system prompt 提升为顶层参数。"""
        provider = _make_provider()
        body = provider._build_request(
            [
                Message(role="system", content="你是追番助手"),
                Message(role="user", content="Hello"),
            ]
        )
        assert body["system"] == "你是追番助手"
        assert all(m["role"] != "system" for m in body["messages"])

    def test_multiple_system_messages_joined(self):
        """Scenario 1.3: 多条 system 消息用 \\n\\n 合并。"""
        provider = _make_provider()
        body = provider._build_request(
            [
                Message(role="system", content="规则A"),
                Message(role="system", content="规则B"),
                Message(role="user", content="Hello"),
            ]
        )
        assert body["system"] == "规则A\n\n规则B"

    def test_single_system_message_unchanged(self):
        """Scenario 1.3: 单条 system 消息原样传递，不合并不加分隔符。"""
        provider = _make_provider()
        body = provider._build_request(
            [
                Message(role="system", content="规则A"),
                Message(role="user", content="Hello"),
            ]
        )
        assert body["system"] == "规则A"

    def test_system_message_content_blocks(self):
        """system 消息 content 为 list 时提取 text block 拼接。"""
        provider = _make_provider()
        body = provider._build_request(
            [
                Message(
                    role="system",
                    content=[TextBlock(text="规则A"), TextBlock(text="规则B")],
                ),
                Message(role="user", content="Hello"),
            ]
        )
        assert body["system"] == "规则A规则B"

    def test_system_message_blocks_ignore_non_text(self):
        """system 消息 content 混入 thinking block 时只提取 text。"""
        provider = _make_provider()
        body = provider._build_request(
            [
                Message(
                    role="system",
                    content=[ThinkingBlock(thinking="思考"), TextBlock(text="规则A")],
                ),
                Message(role="user", content="Hello"),
            ]
        )
        assert body["system"] == "规则A"

    def test_thinking_level_medium_maps_budget(self):
        """Scenario 1.4: thinking_level=medium 映射 budget_tokens=4096，temperature 强制为 1。"""
        provider = _make_provider(thinking_level="medium")
        body = provider._build_request([Message(role="user", content="Q")])
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        assert body["temperature"] == 1

    def test_thinking_level_high_kwargs_override(self):
        """Scenario 1.4/1.6: kwargs thinking_level=high 覆盖全局。"""
        provider = _make_provider(thinking_level="off")
        body = provider._build_request(
            [Message(role="user", content="Q")], thinking_level="high"
        )
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 8192}
        assert body["temperature"] == 1

    def test_thinking_level_off_no_thinking(self):
        """Scenario 1.5: thinking_level=off 不传 thinking，temperature 保持配置值。"""
        provider = _make_provider(thinking_level="off")
        body = provider._build_request([Message(role="user", content="Q")])
        assert "thinking" not in body
        assert body["temperature"] == 0.7

    def test_thinking_level_default_off(self):
        """Scenario 1.5: 未配置 thinking_level（缺省 off）时行为一致。"""
        provider = _make_provider()
        body = provider._build_request([Message(role="user", content="Q")])
        assert "thinking" not in body
        assert body["temperature"] == 0.7

    def test_per_call_override_global_default(self):
        """Scenario 1.6: per-call 覆盖全局默认，未传 kwargs 仍走全局。"""
        provider = _make_provider(thinking_level="off")
        body_override = provider._build_request(
            [Message(role="user", content="Q")], thinking_level="high"
        )
        assert body_override["thinking"]["budget_tokens"] == 8192

        body_default = provider._build_request([Message(role="user", content="Q")])
        assert "thinking" not in body_default

    def test_haiku_model_thinking_degraded(self):
        """Scenario 1.7: claude-haiku 模型 thinking 降级为 off。"""
        provider = _make_provider(thinking_level="medium")
        body = provider._build_request(
            [Message(role="user", content="Q")],
            model="claude-haiku-4-5-20251001",
        )
        assert "thinking" not in body
        assert body["temperature"] == 0.7

    def test_thinking_enabled_unknown_model_ok(self):
        """Scenario 1.7: 未知模型按支持处理。"""
        provider = _make_provider(thinking_level="medium")
        assert provider._thinking_enabled("medium", "unknown-model") == 4096
        assert provider._thinking_enabled("off", "claude-sonnet-4-6") == 0
        assert provider._thinking_enabled("invalid-level", "claude-sonnet-4-6") == 0


# ===================================================================
# Feature 2: 响应解析
# ===================================================================


class TestParseResponse:
    """_parse_response 纯函数测试。"""

    def test_text_response(self):
        """Scenario 2.1: 纯文本响应解析。"""
        provider = _make_provider()
        resp = provider._parse_response(
            {
                "content": [{"type": "text", "text": "你好"}],
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        )
        assert resp.content == "你好"
        assert isinstance(resp.blocks[0], TextBlock)
        assert resp.stop_reason == "end_turn"
        assert resp.model == "claude-sonnet-4-6"
        # Anthropic usage 字段映射
        assert resp.usage is not None
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.completion_tokens == 20
        assert resp.usage.total_tokens == 30

    def test_thinking_block_not_in_content(self):
        """Scenario 2.2: thinking block 解析但不进入 content。"""
        provider = _make_provider()
        resp = provider._parse_response(
            {
                "content": [
                    {"type": "thinking", "thinking": "思考中..."},
                    {"type": "text", "text": "答案"},
                ],
                "stop_reason": "end_turn",
            }
        )
        assert resp.content == "答案"
        assert isinstance(resp.blocks[0], ThinkingBlock)
        assert isinstance(resp.blocks[1], TextBlock)

    def test_thinking_block_with_signature(self):
        """thinking block 携带 signature。"""
        provider = _make_provider()
        resp = provider._parse_response(
            {
                "content": [{"type": "thinking", "thinking": "x", "signature": "sig1"}],
                "stop_reason": "end_turn",
            }
        )
        assert isinstance(resp.blocks[0], ThinkingBlock)
        assert resp.blocks[0].signature == "sig1"

    def test_redacted_thinking_block(self):
        """Scenario 2.3: redacted_thinking block 容错解析。"""
        provider = _make_provider()
        resp = provider._parse_response(
            {
                "content": [{"type": "redacted_thinking", "data": "xxx"}],
                "stop_reason": "end_turn",
            }
        )
        assert len(resp.blocks) == 1
        assert isinstance(resp.blocks[0], RedactedThinkingBlock)

    def test_unknown_block_type_skipped(self):
        """Scenario 2.4: 未知 block 类型（tool_use）跳过不崩溃，content 只取 text。"""
        provider = _make_provider()
        with patch("app.services.llm.providers.anthropic.logger") as mock_log:
            resp = provider._parse_response(
                {
                    "content": [
                        {"type": "tool_use", "id": "u1", "name": "search", "input": {}},
                        {"type": "text", "text": "结果"},
                    ],
                    "stop_reason": "tool_use",
                }
            )
        assert resp.content == "结果"
        assert len(resp.blocks) == 1
        assert isinstance(resp.blocks[0], TextBlock)
        assert resp.stop_reason == "tool_use"
        mock_log.warning.assert_called_once()

    def test_no_usage(self):
        """无 usage 字段时 usage 为 None。"""
        provider = _make_provider()
        resp = provider._parse_response(
            {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
        )
        assert resp.usage is None

    def test_empty_content(self):
        """空 content 数组 + max_tokens 结束原因。"""
        provider = _make_provider()
        resp = provider._parse_response({"content": [], "stop_reason": "max_tokens"})
        assert resp.content == ""
        assert resp.blocks == []
        assert resp.stop_reason == "max_tokens"


# ===================================================================
# chat() 集成（mock httpx）
# ===================================================================


class TestAnthropicProviderChat:
    """chat() 集成测试。"""

    @pytest.mark.asyncio
    async def test_request_format(self):
        """Scenario 1.1: 发送到 /v1/messages 的请求格式正确。"""
        mock_client = _make_mock_client(
            json_body={
                "content": [{"type": "text", "text": "Hello, world!"}],
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = _make_provider()
            messages = [
                Message(role="system", content="You are helpful."),
                Message(role="user", content="Hello"),
            ]
            await provider.chat(messages)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args

        assert call_args[0][0] == "https://api.anthropic.com/v1/messages"

        body = call_args[1]["json"]
        assert body["system"] == "You are helpful."
        assert body["messages"] == [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
        ]

        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Content-Type"] == "application/json"
        assert headers["anthropic-version"] == "2023-06-01"

        assert call_args[1]["timeout"] == 60

    @pytest.mark.asyncio
    async def test_normal_response_parsing(self):
        """Scenario 2.1: chat() 正常响应解析。"""
        mock_client = _make_mock_client(
            json_body={
                "content": [{"type": "text", "text": "The answer is 42."}],
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 15, "output_tokens": 8},
            }
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = _make_provider()
            resp = await provider.chat([Message(role="user", content="Q")])

        assert isinstance(resp, ChatResponse)
        assert resp.content == "The answer is 42."
        assert resp.model == "claude-sonnet-4-6"
        assert resp.stop_reason == "end_turn"
        assert resp.usage is not None
        assert resp.usage.prompt_tokens == 15
        assert resp.usage.completion_tokens == 8
        assert resp.usage.total_tokens == 23

    @pytest.mark.asyncio
    async def test_thinking_request(self):
        """thinking_level 开启时请求体含 thinking 且 temperature=1。"""
        mock_client = _make_mock_client(
            json_body={
                "content": [
                    {"type": "thinking", "thinking": "思考"},
                    {"type": "text", "text": "答案"},
                ],
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = _make_provider(thinking_level="medium")
            resp = await provider.chat([Message(role="user", content="Q")])

        body = mock_client.post.call_args[1]["json"]
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        assert body["temperature"] == 1
        # thinking 不计入 content
        assert resp.content == "答案"
        assert resp.usage is not None
        assert resp.usage.total_tokens == 150

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 429, 500])
    async def test_http_error_handling(self, status_code):
        """Scenario 2.5: HTTP 错误抛出 httpx.HTTPStatusError。"""
        mock_client = _make_mock_client(
            status_code=status_code,
            raise_for_status_side_effect=httpx.HTTPStatusError(
                "error",
                request=Mock(),
                response=Mock(status_code=status_code),
            ),
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = _make_provider()
            with pytest.raises(httpx.HTTPStatusError):
                await provider.chat([Message(role="user", content="Q")])
