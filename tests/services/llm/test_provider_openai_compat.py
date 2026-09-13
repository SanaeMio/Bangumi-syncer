"""app.services.llm.providers.openai_compat 测试（任务 1.3）。"""

from typing import Optional
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.services.llm.models import ChatResponse, Message
from app.services.llm.providers.openai_compat import OpenAICompatProvider


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


class TestOpenAICompatProviderChat:
    """使用 mock httpx 的 chat() 方法集成测试。"""

    @pytest.mark.asyncio
    async def test_request_format(self):
        """验证发送到 API 的请求格式正确。"""
        mock_client = _make_mock_client(
            json_body={
                "choices": [{"message": {"content": "Hello, world!"}}],
                "model": "gpt-4o-mini",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            }
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1",
                api_key="sk-test",
                model="gpt-4o-mini",
                max_tokens=2000,
                temperature=0.7,
                timeout=60,
            )
            messages = [
                Message(role="system", content="You are helpful."),
                Message(role="user", content="Hello"),
            ]
            await provider.chat(messages)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args

        # 验证 URL
        assert call_args[0][0] == "https://api.openai.com/v1/chat/completions"

        # 验证请求体
        body = call_args[1]["json"]
        assert body["model"] == "gpt-4o-mini"
        assert body["max_tokens"] == 2000
        assert body["temperature"] == 0.7
        assert body["messages"] == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        # 验证 headers
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Content-Type"] == "application/json"

        # 验证超时
        assert call_args[1]["timeout"] == 60

    @pytest.mark.asyncio
    async def test_normal_response_parsing(self):
        """验证正常的响应解析能提取内容和 usage。"""
        mock_client = _make_mock_client(
            json_body={
                "choices": [{"message": {"content": "The answer is 42."}}],
                "model": "gpt-4o-mini",
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 8,
                    "total_tokens": 23,
                },
            }
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1", api_key="sk-test"
            )
            resp = await provider.chat([Message(role="user", content="Q")])

        assert isinstance(resp, ChatResponse)
        assert resp.content == "The answer is 42."
        assert resp.model == "gpt-4o-mini"
        assert resp.usage is not None
        assert resp.usage.prompt_tokens == 15
        assert resp.usage.completion_tokens == 8
        assert resp.usage.total_tokens == 23

    @pytest.mark.asyncio
    async def test_response_without_usage(self):
        """没有 usage 字段的响应也应正确解析。"""
        mock_client = _make_mock_client(
            json_body={
                "choices": [{"message": {"content": "No usage here."}}],
                "model": "some-model",
            }
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1", api_key="sk-test"
            )
            resp = await provider.chat([Message(role="user", content="Q")])

        assert resp.content == "No usage here."
        assert resp.model == "some-model"
        assert resp.usage is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 429, 500])
    async def test_http_error_handling(self, status_code):
        """HTTP 错误应抛出 httpx.HTTPStatusError。"""
        mock_client = _make_mock_client(
            status_code=status_code,
            raise_for_status_side_effect=httpx.HTTPStatusError(
                "error",
                request=Mock(),
                response=Mock(status_code=status_code),
            ),
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1", api_key="sk-test"
            )
            with pytest.raises(httpx.HTTPStatusError):
                await provider.chat([Message(role="user", content="Q")])

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """超时应作为 httpx.TimeoutException 传播。"""
        mock_client = _make_mock_client(
            post_side_effect=httpx.TimeoutException("timeout")
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1", api_key="sk-test"
            )
            with pytest.raises(httpx.TimeoutException):
                await provider.chat([Message(role="user", content="Q")])

    @pytest.mark.asyncio
    async def test_json_parse_failure(self):
        """非 JSON 响应应抛出 JSON 解码错误。"""
        mock_client = _make_mock_client(json_side_effect=ValueError("Invalid JSON"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1", api_key="sk-test"
            )
            with pytest.raises(ValueError, match="Invalid JSON"):
                await provider.chat([Message(role="user", content="Q")])

    @pytest.mark.asyncio
    async def test_extra_kwargs_override_defaults(self):
        """传递给 chat() 的额外 kwargs 应覆盖默认参数。"""
        mock_client = _make_mock_client(
            json_body={
                "choices": [{"message": {"content": "OK"}}],
                "model": "gpt-4o-mini",
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1", api_key="sk-test"
            )
            await provider.chat(
                [Message(role="user", content="Q")],
                model="gpt-4o",
                max_tokens=100,
                temperature=0.1,
            )

        call_body = mock_client.post.call_args[1]["json"]
        assert call_body["model"] == "gpt-4o"
        assert call_body["max_tokens"] == 100
        assert call_body["temperature"] == 0.1

    @pytest.mark.asyncio
    async def test_context_manager_cleanup(self):
        """httpx 客户端应通过上下文管理器正确关闭。"""
        mock_client = _make_mock_client(
            json_body={
                "choices": [{"message": {"content": "OK"}}],
                "model": "gpt-4o-mini",
            }
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1", api_key="sk-test"
            )
            await provider.chat([Message(role="user", content="Q")])

        # 在 `async with` 内部，__aexit__ 应调用 aclose
        mock_client.aclose.assert_awaited_once()


class TestOpenAICompatBuildRequest:
    """Phase 2.2：_build_request / _to_wire_message / reasoning_effort 映射。"""

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

    def test_content_block_list_flattens_text_blocks(self):
        from app.services.llm.models import TextBlock

        msg = Message(
            role="system",
            content=[TextBlock(text="第一段"), TextBlock(text="第二段")],
        )
        wire = self._provider()._to_wire_message(msg)
        assert wire["content"] == "第一段\n\n第二段"

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


class TestOpenAICompatParseResponse:
    """Phase 2.2：_parse_response（含 refusal / 缺省字段）。"""

    def test_refusal_raises(self):
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1", api_key="sk-test"
        )
        with pytest.raises(ValueError, match="模型拒绝响应"):
            provider._parse_response(
                {"choices": [{"message": {"content": None, "refusal": "不行"}}]}
            )

    def test_missing_usage_and_model_defaults(self):
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1", api_key="sk-test"
        )
        resp = provider._parse_response({"choices": [{"message": {}}]})
        assert resp.content == ""
        assert resp.usage is None


class TestReasoningTemperatureAlignment:
    """H3：o 系列发 reasoning_effort 时 temperature 强制 1（与 Anthropic 侧对齐），
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


class TestFinishReasonMapping:
    """M10：OpenAI finish_reason → stop_reason（与 Anthropic 对齐，供 P4 截断判断）。"""

    def test_finish_reason_stop_mapped(self):
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1", api_key="sk-test"
        )
        resp = provider._parse_response(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "model": "gpt-4o-mini",
            }
        )
        assert resp.stop_reason == "stop"

    def test_finish_reason_length_mapped(self):
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1", api_key="sk-test"
        )
        resp = provider._parse_response(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "length"}],
                "model": "gpt-4o-mini",
            }
        )
        assert resp.stop_reason == "length"

    def test_missing_finish_reason_defaults_empty(self):
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1", api_key="sk-test"
        )
        resp = provider._parse_response(
            {"choices": [{"message": {"content": "ok"}}], "model": "gpt-4o-mini"}
        )
        assert resp.stop_reason == ""
