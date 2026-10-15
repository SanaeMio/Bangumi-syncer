"""app.services.llm.client 测试。

LLMClient 以 ``stream_chat`` 为唯一调用入口（流式形态唯一）；聚合消费
一律由调用方 ``collect(stream_chat(...))`` 完成，不再保留 ``chat()`` 别名。
本文件覆盖：重试/降级/终态语义、流式透传、流拒绝终态、落库防双计。
"""

import json
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.llm.models import (
    ChatResponse,
    Message,
    StreamChunk,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
    collect,
)

# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

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
    """在每个测试前后重置 LLMClient 单例。"""
    import app.services.llm.client as client_mod

    client_mod._llm_client = None
    yield
    client_mod._llm_client = None


@pytest.fixture
def mock_config():
    """Mock config_manager.get_llm_config，使其返回测试配置。"""
    with patch(
        "app.services.llm.client.config_manager.get_llm_config",
        return_value=dict(TEST_LLM_CONFIG),
    ):
        yield


@pytest.fixture
def mock_log_usage():
    """Mock database_manager.llm_usage.log_usage，用于验证。

    _log_usage 方法通过 ``from app.core.database import database_manager``
    在其内部导入 ``database_manager``。因为 conftest 已触发延迟实例化，
    模块属性已存在，可以直接 patch。
    """
    with patch("app.core.database.database_manager.llm_usage.log_usage") as mock_log:
        yield mock_log


@pytest.fixture
def mock_logger():
    """Mock app.core.logging.logger，用于验证日志输出。"""
    with patch("app.services.llm.client.logger") as mock_log:
        yield mock_log


# ---------------------------------------------------------------------------
# 辅助方法
# ---------------------------------------------------------------------------


def _sleep_patch_path():
    """返回用于在 client 内部 patch asyncio.sleep 的目标字符串。"""
    return "app.services.llm.client.asyncio.sleep"


def _build_client(*, mock_sleep=None):
    """创建 LLMClient（可选在 active patch 中替换 asyncio.sleep）。"""
    from app.services.llm.client import LLMClient

    if mock_sleep is not None:
        with patch(_sleep_patch_path(), mock_sleep):
            return LLMClient()
    return LLMClient()


def _httpx_status(
    code: int, text: str = "", headers: dict | None = None
) -> httpx.HTTPStatusError:
    """构造指定状态码的 httpx.HTTPStatusError。"""
    request = httpx.Request("POST", "https://test.api.com/v1/chat/completions")
    response = httpx.Response(code, text=text, request=request, headers=headers or {})
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


def _response_chunks(resp: ChatResponse) -> list[StreamChunk]:
    """把 ChatResponse 转为等价事件序列（测试构造流用）。"""
    chunks: list[StreamChunk] = []
    blocks = list(resp.blocks)
    if not blocks and resp.content:
        blocks = [TextBlock(text=resp.content)]
    for block in blocks:
        if isinstance(block, TextBlock):
            chunks.append(StreamChunk(type="text_delta", text=block.text))
        elif isinstance(block, ThinkingBlock):
            chunks.append(
                StreamChunk(
                    type="thinking_delta",
                    thinking=block.thinking,
                    signature=block.signature or "",
                )
            )
        elif isinstance(block, ToolUseBlock):
            chunks.append(
                StreamChunk(
                    type="tool_use_start",
                    tool_use_id=block.id,
                    tool_name=block.name,
                )
            )
            chunks.append(
                StreamChunk(
                    type="tool_use_delta",
                    tool_use_id=block.id,
                    partial_json=json.dumps(block.input, ensure_ascii=False),
                )
            )
    if resp.usage is not None:
        chunks.append(StreamChunk(type="usage", usage=resp.usage))
    chunks.append(StreamChunk(type="stop", stop_reason=resp.stop_reason))
    if resp.model and chunks:
        # 流式为唯一形态：ChatResponse.model 由事件携带，测试构造时填到首个事件
        chunks[0].model = resp.model
    return chunks


def _stream_fn(steps, calls=None):
    """构造可赋给 ``provider.stream`` 的 async generator 函数。

    steps 为按调用次序取用的列表，元素是 ``list[StreamChunk]``（正常产出）
    或 ``Exception``（迭代即抛）；超出长度后重复使用最后一项。
    calls 非 None 时记录每次调用的 kwargs。
    """
    state = {"i": 0}

    async def _gen(messages, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        i = state["i"]
        state["i"] = i + 1
        step = steps[i] if i < len(steps) else steps[-1]
        if isinstance(step, Exception):
            raise step
        for chunk in step:
            yield chunk

    return _gen


def _stream_partial_then_error(chunks, exc, calls=None):
    """产出部分事件后再抛异常的流函数（模拟首 chunk 后中断）。"""

    async def _gen(messages, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        for chunk in chunks:
            yield chunk
        raise exc

    return _gen


# ===================================================================
# LLMClient 聚合消费（collect(stream_chat) + 重试/降级/落库）
# ===================================================================


class TestLLMClientAggregateConsume:
    """``collect(LLMClient.stream_chat(...))`` 聚合消费测试（重试/降级/落库）。"""

    @pytest.mark.asyncio
    async def test_chat_success(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """单次成功流式调用返回 ChatResponse 并记录 usage。"""
        response = ChatResponse(
            content="Hello!",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([_response_chunks(response)])
            messages = [Message(role="user", content="Hello")]
            result = await collect(
                client.stream_chat(messages, job_id=1, job_name="test")
            )

        assert isinstance(result, ChatResponse)
        assert result.content == "Hello!"
        assert result.model == "gpt-4o-mini"
        assert result.usage is not None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5
        assert result.usage.total_tokens == 15

        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["job_id"] == 1
        assert kwargs["job_name"] == "test"
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["status"] == "success"
        assert kwargs["prompt_tokens"] == 10
        assert kwargs["completion_tokens"] == 5
        assert kwargs["total_tokens"] == 15

        mock_logger.debug.assert_called_once()
        log_msg = mock_logger.debug.call_args[0][0]
        assert "gpt-4o-mini" in log_msg
        assert "15" in log_msg
        assert "latency=" in log_msg

    @pytest.mark.asyncio
    async def test_chat_retry_succeeds_on_retry(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """首次尝试（首 chunk 前）失败，第 2 次重试成功。"""
        mock_sleep = AsyncMock()
        calls: list[dict] = []
        ok_chunks = _response_chunks(
            ChatResponse(
                content="Retry OK",
                model="gpt-4o-mini",
                usage=Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            )
        )

        with patch(_sleep_patch_path(), mock_sleep):
            client = _build_client()
            client._provider.stream = _stream_fn(
                [Exception("Connection error"), ok_chunks], calls
            )
            messages = [Message(role="user", content="Retry test")]
            response = await collect(client.stream_chat(messages))

        assert response.content == "Retry OK"
        assert response.usage is not None
        assert response.usage.total_tokens == 8
        assert len(calls) == 2
        mock_sleep.assert_awaited_once_with(1)

        mock_log_usage.assert_called_once()
        assert mock_log_usage.call_args[1]["status"] == "success"

    @pytest.mark.asyncio
    async def test_chat_all_retries_exhausted(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """全部 3 次尝试均失败 → 抛 LLMCallError（不返回空 ChatResponse）。"""
        from app.services.llm.client import LLMCallError

        mock_sleep = AsyncMock()
        calls: list[dict] = []

        with patch(_sleep_patch_path(), mock_sleep):
            client = _build_client()
            client._provider.stream = _stream_fn(
                [
                    Exception("Error 1"),
                    Exception("Error 2"),
                    Exception("Error 3"),
                ],
                calls,
            )
            messages = [Message(role="user", content="Always fail")]
            with pytest.raises(LLMCallError):
                await collect(client.stream_chat(messages))

        assert len(calls) == 3

        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["status"] == "error"
        assert "Error 3" in kwargs["error_message"]

        mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_backoff_delays(self, reset_llm_singleton, mock_config):
        """正确的退避延迟：重试间隔为 1 秒，然后 3 秒。"""
        from app.services.llm.client import LLMCallError

        mock_sleep = AsyncMock()

        with patch(_sleep_patch_path(), mock_sleep):
            client = _build_client()
            client._provider.stream = _stream_fn(
                [
                    Exception("Fail 1"),
                    Exception("Fail 2"),
                    Exception("Fail 3"),
                ]
            )
            with pytest.raises(LLMCallError):
                await collect(
                    client.stream_chat([Message(role="user", content="Test")])
                )

        assert mock_sleep.await_count == 2
        mock_sleep.assert_any_await(1)
        mock_sleep.assert_any_await(3)

    @pytest.mark.asyncio
    async def test_success_logs_correct_tokens(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """成功路径记录 status='success' 和正确的 token 计数（model 透传）。"""
        response = ChatResponse(
            content="OK",
            model="claude-3",
            usage=Usage(prompt_tokens=42, completion_tokens=58, total_tokens=100),
        )

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([_response_chunks(response)])
            result = await collect(
                client.stream_chat(
                    [Message(role="user", content="Count tokens")], model="claude-3"
                )
            )

        assert result.model == "claude-3"
        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["status"] == "success"
        assert kwargs["prompt_tokens"] == 42
        assert kwargs["completion_tokens"] == 58
        assert kwargs["total_tokens"] == 100
        assert kwargs["model"] == "claude-3"
        assert isinstance(kwargs["latency_ms"], int)
        assert kwargs["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_failure_path_logs_error(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """失败路径记录 status='error' 并包含 error_message，且抛 LLMCallError。"""
        from app.services.llm.client import LLMCallError

        mock_sleep = AsyncMock()

        with patch(_sleep_patch_path(), mock_sleep):
            client = _build_client()
            client._provider.stream = _stream_fn(
                [
                    RuntimeError("Token exceeded"),
                    RuntimeError("Token exceeded"),
                    RuntimeError("Token exceeded"),
                ]
            )
            with pytest.raises(LLMCallError):
                await collect(
                    client.stream_chat([Message(role="user", content="Error test")])
                )

        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["status"] == "error"
        assert kwargs["error_message"] == "RuntimeError: Token exceeded"
        assert isinstance(kwargs["latency_ms"], int)

    @pytest.mark.asyncio
    async def test_logger_info_contains_model_tokens_latency(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """成功路径 logger.debug 包含模型、token 和延迟信息。"""
        response = ChatResponse(
            content="OK",
            model="test-model-v1",
            usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([_response_chunks(response)])
            await collect(
                client.stream_chat(
                    [Message(role="user", content="Log test")], model="test-model-v1"
                )
            )

        mock_logger.debug.assert_called_once()
        log_msg = mock_logger.debug.call_args[0][0]
        assert "test-model-v1" in log_msg
        assert "30" in log_msg
        assert "latency=" in log_msg

    @pytest.mark.asyncio
    async def test_log_usage_failure_does_not_crash_chat(
        self, reset_llm_singleton, mock_config, mock_logger
    ):
        """即使 log_usage 抛出异常，chat 仍应返回响应（尽力而为）。"""
        response = ChatResponse(
            content="Hello!",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        with patch(_sleep_patch_path(), AsyncMock()):
            with patch(
                "app.core.database.database_manager.llm_usage.log_usage",
                side_effect=OSError("DB down"),
            ):
                client = _build_client()
                client._provider.stream = _stream_fn([_response_chunks(response)])
                messages = [Message(role="user", content="test")]
                result = await collect(client.stream_chat(messages))

        assert result.content == "Hello!"
        assert result.model == "gpt-4o-mini"
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_chat_without_optional_params(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """当 job_id 和 job_name 省略（None）时，chat() 正常工作。"""
        response = ChatResponse(
            content="OK",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([_response_chunks(response)])
            result = await collect(
                client.stream_chat([Message(role="user", content="Q")])
            )

        assert result.content == "OK"
        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["job_id"] is None
        assert kwargs["job_name"] == ""
        assert kwargs["status"] == "success"

    @pytest.mark.asyncio
    async def test_chat_usage_none_handles_zero_tokens(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """当流未上报 usage 时，token 计数默认为 0。"""
        response = ChatResponse(content="No usage", model="gpt-4o-mini", usage=None)

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([_response_chunks(response)])
            await collect(client.stream_chat([Message(role="user", content="Q")]))

        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["prompt_tokens"] == 0
        assert kwargs["completion_tokens"] == 0
        assert kwargs["total_tokens"] == 0

    @pytest.mark.asyncio
    async def test_chat_passes_kwargs_to_provider(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """传递给 chat() 的额外 kwargs 会转发给 provider.stream。"""
        calls: list[dict] = []
        response = ChatResponse(
            content="OK",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([_response_chunks(response)], calls)
            await collect(
                client.stream_chat(
                    [Message(role="user", content="Q")],
                    temperature=0.1,
                    max_tokens=100,
                )
            )

        assert len(calls) == 1
        assert calls[0].get("temperature") == 0.1
        assert calls[0].get("max_tokens") == 100


# ===================================================================
# anthropic_compat 工厂分支（Feature 4）
# ===================================================================


def _make_config(provider: str, **overrides) -> dict:
    """构造 LLM 配置字典。"""
    cfg = dict(TEST_LLM_CONFIG, provider=provider)
    cfg.update(overrides)
    return cfg


class TestAnthropicProviderFactory:
    """anthropic_compat 工厂分支（Scenario 4.1/4.2/4.4）。"""

    def test_anthropic_provider_created(self, reset_llm_singleton):
        """Scenario 4.1: provider=anthropic_compat 时实例化 AnthropicProvider。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.anthropic import AnthropicProvider

        cfg = _make_config("anthropic_compat")
        with patch(
            "app.services.llm.client.config_manager.get_llm_config",
            return_value=cfg,
        ):
            client = LLMClient()

        provider = client._provider
        assert isinstance(provider, AnthropicProvider)
        # 参数正确传入
        assert provider.api_base == "https://test.api.com/v1"
        assert provider.api_key == "sk-test-key"
        assert provider.model == "gpt-4o-mini"
        assert provider.max_tokens == 2000
        assert provider.temperature == 0.7
        assert provider.timeout == 60
        # 测试 cfg 未提供 thinking_level → provider 构造函数默认 "off"
        assert provider.thinking_level == "off"

    def test_build_provider_ignores_global_thinking_level(self, reset_llm_singleton):
        """全局 [llm].thinking_level 已移除：cfg 即便带该键，provider 默认仍为 off。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.anthropic import AnthropicProvider

        cfg = _make_config("anthropic_compat", thinking_level="high")
        with patch(
            "app.services.llm.client.config_manager.get_llm_config",
            return_value=cfg,
        ):
            client = LLMClient()

        provider = client._provider
        assert isinstance(provider, AnthropicProvider)
        assert provider.thinking_level == "off"

    def test_openai_provider_ignores_global_thinking_level(self, reset_llm_singleton):
        """openai_compat 同样不读全局 thinking_level：cfg=high 时 provider 仍为 off。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        cfg = _make_config("openai_compat", thinking_level="high")
        with patch(
            "app.services.llm.client.config_manager.get_llm_config",
            return_value=cfg,
        ):
            client = LLMClient()

        assert isinstance(client._provider, OpenAICompatProvider)
        assert client._provider.thinking_level == "off"

    def test_openai_provider_accepts_explicit_thinking_level(self):
        """显式构造参数 thinking_level 仍生效（任务级 kwargs 覆盖依赖此路径）。"""
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1",
            api_key="sk-test-key",
            model="gpt-4o-mini",
            max_tokens=2000,
            temperature=0.7,
            timeout=60,
            thinking_level="high",
        )
        assert provider.thinking_level == "high"

    def test_unknown_provider_raises(self, reset_llm_singleton):
        """Scenario 4.2: 非法 provider 抛 ValueError 并提示支持列表。"""
        from app.services.llm.client import LLMClient

        cfg = _make_config("unknown_provider")
        with patch(
            "app.services.llm.client.config_manager.get_llm_config",
            return_value=cfg,
        ):
            with pytest.raises(ValueError, match="Unsupported LLM provider"):
                LLMClient()

    @pytest.mark.asyncio
    async def test_thinking_level_kwargs_passed_to_anthropic(
        self, reset_llm_singleton, mock_log_usage, mock_logger
    ):
        """Scenario 4.4: chat() 的 thinking_level kwargs 透传到 AnthropicProvider.stream。"""
        from app.services.llm.client import LLMClient

        calls: list[dict] = []
        response = ChatResponse(
            content="OK",
            model="claude-sonnet-4-6",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

        cfg = _make_config("anthropic_compat")
        with patch(
            "app.services.llm.client.config_manager.get_llm_config",
            return_value=cfg,
        ):
            with patch(_sleep_patch_path(), AsyncMock()):
                client = LLMClient()
                client._provider.stream = _stream_fn(
                    [_response_chunks(response)], calls
                )
                await collect(
                    client.stream_chat(
                        [Message(role="user", content="Q")],
                        thinking_level="high",
                    )
                )

        assert calls[0].get("thinking_level") == "high"


# ===================================================================
# get_llm_client singleton
# ===================================================================


class TestGetLlmClient:
    """get_llm_client() 单例函数测试。"""

    def test_returns_same_instance(self, reset_llm_singleton, mock_config):
        """重复调用 get_llm_client() 返回同一实例。"""
        from app.services.llm.client import get_llm_client

        client1 = get_llm_client()
        client2 = get_llm_client()
        assert client1 is client2

    def test_returns_llm_client_instance(self, reset_llm_singleton, mock_config):
        """get_llm_client() 返回 LLMClient 实例。"""
        from app.services.llm.client import LLMClient, get_llm_client

        client = get_llm_client()
        assert isinstance(client, LLMClient)

    def test_two_calls_under_same_fixture(self, reset_llm_singleton, mock_config):
        """单次测试内的多次调用均返回同一对象。"""
        from app.services.llm.client import get_llm_client

        clients = [get_llm_client() for _ in range(5)]
        first = clients[0]
        for c in clients[1:]:
            assert c is first


# ===================================================================
# 方案 C：参数类 400 降级重试（checkbox2 双重保险第二道）
# ===================================================================


class TestParamRejectionDegradation:
    """端点拒绝扩展参数 → provider._extras_disabled 置位 → 立即重试。"""

    @pytest.mark.asyncio
    async def test_param_rejection_degrades_and_retries(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """首次 400（unrecognized argument）→ 降级标记置位 → 重试成功。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        ok = _response_chunks(ChatResponse(content="ok", model="gpt-4o", usage=None))

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk-test", thinking_level="high"
        )
        provider.stream = _stream_fn(
            [
                _httpx_status(
                    400,
                    '{"error": "Unrecognized request argument supplied: '
                    'reasoning_effort"}',
                ),
                ok,
            ],
            calls,
        )

        client = LLMClient()
        client._provider = provider
        resp = await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert resp.content == "ok"
        assert len(calls) == 2  # 降级后立即重试，无退避
        assert provider._extras_disabled is True

    @pytest.mark.asyncio
    async def test_tool_choice_rejection_degrades_and_retries(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """首次 400（thinking 模式拒绝强制 tool_choice）→ 专用降级标记置位 → 重试成功。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.anthropic import AnthropicProvider

        calls: list[dict] = []
        ok = _response_chunks(
            ChatResponse(content="ok", model="deepseek-v4-pro", usage=None)
        )

        provider = AnthropicProvider(
            api_base="https://api.deepseek.com/anthropic/v1",
            api_key="sk-test",
            model="deepseek-v4-pro",
        )
        provider.stream = _stream_fn(
            [
                _httpx_status(
                    400,
                    '{"error": {"message": '
                    '"Thinking mode does not support this tool_choice"}}',
                ),
                ok,
            ],
            calls,
        )

        client = LLMClient()
        client._provider = provider
        resp = await collect(
            client.stream_chat(
                [Message(role="user", content="Q")],
                tools=[
                    {"name": "t", "description": "d", "parameters": {"type": "object"}}
                ],
                tool_choice="t",
            )
        )

        assert resp.content == "ok"
        assert len(calls) == 2  # 降级后立即重试，无退避
        assert provider._force_tool_choice_degraded is True
        assert provider._extras_disabled is True

    @pytest.mark.asyncio
    async def test_param_rejection_latency_excludes_failed_attempt(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """降级重试：成功落库的 latency 由 stream_chat 全程墙钟测量。

        调用顺序（time.time）：stream_chat.t0 → retry.t_attempt
        → 降级重置 t_attempt → stream_chat 成功时刻。降级时重置 t_attempt，
        使失败尝试的耗时不计入 latency。
        """
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        ok = _response_chunks(ChatResponse(content="ok", model="gpt-4o", usage=None))

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk-test", thinking_level="high"
        )
        provider.stream = _stream_fn(
            [
                _httpx_status(
                    400,
                    '{"error": "Unrecognized request argument supplied: '
                    'reasoning_effort"}',
                ),
                ok,
            ],
            calls,
        )

        times = [2000.0, 2000.5, 2000.5, 2000.5]
        with patch("app.services.llm.client.time.time", side_effect=times):
            client = LLMClient()
            client._provider = provider
            await collect(client.stream_chat([Message(role="user", content="Q")]))

        # 降级重置 t_attempt：成功落库 latency = 2000.5 - 2000.0 = 500ms
        assert mock_log_usage.call_args[1]["latency_ms"] == 500

    @pytest.mark.asyncio
    async def test_non_param_400_terminal_no_degradation(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """非参数类 400（如 invalid_api_key）不触发降级，且为终态 → 只尝试一次。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        mock_sleep = AsyncMock()

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk-test"
        )
        provider.stream = _stream_fn(
            [_httpx_status(400, '{"error": "Invalid API key provided"}')], calls
        )

        with patch("app.services.llm.client.asyncio.sleep", mock_sleep):
            client = LLMClient()
            client._provider = provider
            with pytest.raises(LLMCallError) as exc_info:
                await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert len(calls) == 1  # 确定性 400 → 终态，不重试
        assert exc_info.value.retryable is False
        assert provider._extras_disabled is False  # 未降级
        assert mock_sleep.await_count == 0


class TestTerminalErrorsNoRetry:
    """确定性错误（refusal/4xx）不重试；429 尊重 Retry-After。"""

    @pytest.mark.asyncio
    async def test_refusal_valueerror_no_retry(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """refusal（ValueError）为终态——只尝试一次，不再退避重试。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        mock_sleep = AsyncMock()

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn([ValueError("模型拒绝响应: 内容违反政策")], calls)

        with patch("app.services.llm.client.asyncio.sleep", mock_sleep):
            client = LLMClient()
            client._provider = provider
            with pytest.raises(LLMCallError):
                await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert len(calls) == 1  # 不重试
        assert mock_sleep.await_count == 0  # 无退避

    @pytest.mark.asyncio
    async def test_401_no_retry(self, reset_llm_singleton, mock_config, mock_log_usage):
        """401（密钥错误）为终态——只尝试一次，抛 LLMCallError。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        mock_sleep = AsyncMock()

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn(
            [_httpx_status(401, '{"error": "Invalid API key"}')], calls
        )

        with patch("app.services.llm.client.asyncio.sleep", mock_sleep):
            client = LLMClient()
            client._provider = provider
            with pytest.raises(LLMCallError):
                await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert len(calls) == 1
        assert mock_sleep.await_count == 0

    @pytest.mark.asyncio
    async def test_429_respects_retry_after(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """429 尊重 Retry-After（5s）→ 退避 5s 重试。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        mock_sleep = AsyncMock()
        ok = _response_chunks(ChatResponse(content="ok", model="m", usage=None))

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn(
            [
                _httpx_status(429, "rate limited", headers={"Retry-After": "5"}),
                ok,
            ],
            calls,
        )

        with patch("app.services.llm.client.asyncio.sleep", mock_sleep):
            client = LLMClient()
            client._provider = provider
            resp = await collect(
                client.stream_chat([Message(role="user", content="Q")])
            )

        assert resp.content == "ok"
        assert len(calls) == 2
        assert mock_sleep.await_args is not None
        assert mock_sleep.await_args.args[0] == 5  # Retry-After 优先

    @pytest.mark.asyncio
    async def test_429_retry_after_capped_at_60(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """顺手项 2：429 Retry-After 超大值被钳制到 60s，避免请求长时间挂起。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        mock_sleep = AsyncMock()
        ok = _response_chunks(ChatResponse(content="ok", model="m", usage=None))

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn(
            [
                _httpx_status(429, "rate limited", headers={"Retry-After": "9999"}),
                ok,
            ],
            calls,
        )

        with patch("app.services.llm.client.asyncio.sleep", mock_sleep):
            client = LLMClient()
            client._provider = provider
            resp = await collect(
                client.stream_chat([Message(role="user", content="Q")])
            )

        assert resp.content == "ok"
        assert len(calls) == 2
        assert mock_sleep.await_args is not None
        assert mock_sleep.await_args.args[0] == 60  # 钳制到 60s 上限


class TestParamRejectionExtended:
    """Anthropic invalid_request_error / 422 网关也触发降级。"""

    def test_anthropic_text_422_matches(self):
        from app.services.llm.client import _is_param_rejection

        e = _httpx_status(
            400,
            '{"type": "error", "error": {"type": "invalid_request_error", '
            '"message": "does not support thinking"}}',
        )
        assert _is_param_rejection(e) is True

    def test_422_gateway_matches(self):
        from app.services.llm.client import _is_param_rejection

        e = _httpx_status(422, '{"detail": "extra fields not permitted"}')
        assert _is_param_rejection(e) is True

    def test_terminal_400_not_param(self):
        """普通 400（API key 无效）不是参数拒绝、是终态错误。"""
        from app.services.llm.client import _is_param_rejection, _is_terminal_error

        e = _httpx_status(400, '{"error": "Invalid API key"}')
        assert _is_param_rejection(e) is False
        assert _is_terminal_error(e) is True


class TestStreamRejectionDetection:
    """stream 拒绝模式识别，且优先于通用参数拒绝。"""

    @pytest.mark.parametrize(
        "text",
        [
            "stream is not supported",
            "this model does not support streaming",
            "streaming is not supported by endpoint",
            "unsupported parameter: stream",
            "unknown parameter: stream",
            "stream: not supported",
            # 网关文案 "unrecognized parameter 'stream' is not supported"
            "unrecognized parameter 'stream' is not supported",
            'unrecognized parameter "stream" is not supported',
        ],
    )
    def test_stream_rejection_patterns_match(self, text):
        from app.services.llm.client import _is_stream_rejection

        assert _is_stream_rejection(_httpx_status(400, text)) is True

    def test_stream_options_not_mistaken_as_stream_rejection(self):
        """'unrecognized parameter stream_options' 不应误判为 stream 拒绝。"""
        from app.services.llm.client import _is_stream_rejection

        e = _httpx_status(
            400, "unrecognized parameter 'stream_options' is not supported"
        )
        assert _is_stream_rejection(e) is False

    def test_non_stream_rejection_does_not_match(self):
        from app.services.llm.client import _is_stream_rejection

        e = _httpx_status(400, '{"error": "Invalid API key"}')
        assert _is_stream_rejection(e) is False

    def test_stream_rejection_takes_priority_over_param(self):
        """'unknown parameter: stream' 同时命中通用模式，但流式判定优先。"""
        from app.services.llm.client import (
            _is_param_rejection,
            _is_stream_rejection,
        )

        e = _httpx_status(400, '{"error": "unknown parameter: stream"}')
        assert _is_stream_rejection(e) is True
        assert _is_param_rejection(e) is True  # 通用模式也会命中，故顺序很关键


# ===================================================================
# LLMCallError：重试耗尽抛异常（不再返回空 ChatResponse）
# ===================================================================


class TestLLMCallError:
    """重试耗尽时抛 LLMCallError，携带 retryable 标志。"""

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises_llm_call_error(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """全部重试耗尽 → 抛 LLMCallError（不再返回空 ChatResponse）。"""
        from app.services.llm.client import LLMCallError

        mock_sleep = AsyncMock()

        with patch(_sleep_patch_path(), mock_sleep):
            client = _build_client()
            client._provider.stream = _stream_fn(
                [
                    Exception("Error 1"),
                    Exception("Error 2"),
                    Exception("Error 3"),
                ]
            )
            with pytest.raises(LLMCallError):
                await collect(
                    client.stream_chat([Message(role="user", content="Always fail")])
                )

        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["status"] == "error"

    @pytest.mark.asyncio
    async def test_429_retryable_true(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """429 重试耗尽 → LLMCallError.retryable=True。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        mock_sleep = AsyncMock()
        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn([_httpx_status(429, "rate limited")])

        with patch("app.services.llm.client.asyncio.sleep", mock_sleep):
            client = LLMClient()
            client._provider = provider
            with pytest.raises(LLMCallError) as exc_info:
                await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_401_retryable_false(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """401 → LLMCallError.retryable=False（确定性错误）。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn([_httpx_status(401, '{"error": "Invalid"}')])

        client = LLMClient()
        client._provider = provider
        with pytest.raises(LLMCallError) as exc_info:
            await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_403_retryable_false(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """403 → LLMCallError.retryable=False（确定性错误）。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn([_httpx_status(403, '{"error": "Forbidden"}')])

        client = LLMClient()
        client._provider = provider
        with pytest.raises(LLMCallError) as exc_info:
            await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_valueerror_refusal_retryable_false(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """ValueError（refusal）→ LLMCallError.retryable=False。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn([ValueError("模型拒绝响应: 内容违反政策")])

        client = LLMClient()
        client._provider = provider
        with pytest.raises(LLMCallError) as exc_info:
            await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_param_400_degraded_then_failed_retryable_false(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """参数类 400 降级后仍失败 → LLMCallError.retryable=False（终态）。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        param_400 = _httpx_status(
            400,
            '{"error": "Unrecognized request argument supplied: reasoning_effort"}',
        )
        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn([param_400])

        client = LLMClient()
        client._provider = provider
        with pytest.raises(LLMCallError) as exc_info:
            await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_success_does_not_raise(
        self, reset_llm_singleton, mock_config, mock_log_usage
    ):
        """成功路径不抛异常，正常返回 ChatResponse。"""
        response = ChatResponse(
            content="Hello!",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([_response_chunks(response)])
            result = await collect(
                client.stream_chat([Message(role="user", content="Hello")])
            )

        assert result.content == "Hello!"
        assert result.model == "gpt-4o-mini"


# ===================================================================
# stream_chat / fallback / 落库防双计
# ===================================================================


class TestStreamChat:
    """stream_chat() 增量消费、重试、降级与兜底。"""

    @pytest.mark.asyncio
    async def test_collect_aggregates_stream_events(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 1：collect(stream_chat()) 聚合 provider.stream 事件为等价 ChatResponse。"""
        chunks = [
            StreamChunk(type="thinking_delta", thinking="think", signature="sig"),
            StreamChunk(type="text_delta", text="Hello"),
            StreamChunk(type="tool_use_start", tool_use_id="t1", tool_name="search"),
            StreamChunk(
                type="tool_use_delta", tool_use_id="t1", partial_json='{"q": "x"}'
            ),
            StreamChunk(
                type="usage",
                usage=Usage(prompt_tokens=3, completion_tokens=4, total_tokens=7),
            ),
            StreamChunk(type="stop", stop_reason="tool_use"),
        ]

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([chunks])
            resp = await collect(
                client.stream_chat([Message(role="user", content="Q")])
            )

        assert resp.content == "Hello"
        assert resp.stop_reason == "tool_use"
        assert [b.type for b in resp.blocks] == ["thinking", "text", "tool_use"]
        tool_block = resp.blocks[2]
        assert isinstance(tool_block, ToolUseBlock)
        assert tool_block.input == {"q": "x"}
        assert resp.usage is not None
        assert resp.usage.total_tokens == 7
        mock_log_usage.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_chat_passthrough_order_and_logs_once(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 2：stream_chat() 透传事件顺序与内容不变；耗尽时落库一次。"""
        chunks = [
            StreamChunk(type="text_delta", text="Hel"),
            StreamChunk(type="text_delta", text="lo"),
            StreamChunk(
                type="usage",
                usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            ),
            StreamChunk(type="stop", stop_reason="end_turn"),
        ]

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([chunks])
            received = [
                chunk
                async for chunk in client.stream_chat(
                    [Message(role="user", content="hi")], job_name="j"
                )
            ]

        assert received == chunks
        mock_log_usage.assert_called_once()
        assert mock_log_usage.call_args[1]["status"] == "success"

    @pytest.mark.asyncio
    async def test_stream_retry_before_first_chunk_connection_error(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 3：首 chunk 前连接错误 → 退避重试，第 2 次成功。"""
        mock_sleep = AsyncMock()
        calls: list[dict] = []
        ok = _response_chunks(ChatResponse(content="ok", usage=None))

        with patch(_sleep_patch_path(), mock_sleep):
            client = _build_client()
            client._provider.stream = _stream_fn(
                [httpx.ConnectError("boom"), ok], calls
            )
            resp = await collect(
                client.stream_chat([Message(role="user", content="Q")])
            )

        assert resp.content == "ok"
        assert len(calls) == 2
        mock_sleep.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_stream_retry_429_retry_after(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 4：首 chunk 前 429（含 Retry-After）→ 按退避重试。"""
        mock_sleep = AsyncMock()
        calls: list[dict] = []
        ok = _response_chunks(ChatResponse(content="ok", usage=None))

        with patch(_sleep_patch_path(), mock_sleep):
            client = _build_client()
            client._provider.stream = _stream_fn(
                [
                    _httpx_status(429, "slow down", headers={"Retry-After": "7"}),
                    ok,
                ],
                calls,
            )
            resp = await collect(
                client.stream_chat([Message(role="user", content="Q")])
            )

        assert resp.content == "ok"
        assert len(calls) == 2
        assert mock_sleep.await_args is not None
        assert mock_sleep.await_args.args[0] == 7

    @pytest.mark.asyncio
    async def test_stream_param_rejection_degrades_and_retries(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 5：首 chunk 前 400 参数拒绝 → 置 _extras_disabled 立即重试。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        ok = _response_chunks(ChatResponse(content="ok", usage=None))

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk", thinking_level="high"
        )
        provider.stream = _stream_fn(
            [
                _httpx_status(
                    400,
                    '{"error": "Unrecognized request argument supplied: '
                    'reasoning_effort"}',
                ),
                ok,
            ],
            calls,
        )

        client = LLMClient()
        client._provider = provider
        resp = await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert resp.content == "ok"
        assert len(calls) == 2
        assert provider._extras_disabled is True

    @pytest.mark.asyncio
    async def test_stream_rejection_raises_llm_call_error_no_retry(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 6：400 stream 拒绝 → 确定性 LLMCallError（不重试、不降级、不伪装）。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        stream_calls: list[dict] = []
        mock_sleep = AsyncMock()

        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn(
            [_httpx_status(400, "stream is not supported")], stream_calls
        )

        client = LLMClient()
        client._provider = provider

        with patch(_sleep_patch_path(), mock_sleep):
            with pytest.raises(LLMCallError) as exc_info:
                await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is False
        assert "端点不支持流式调用" in str(exc_info.value)
        assert len(stream_calls) == 1  # 不重试
        assert mock_sleep.await_count == 0  # 无退避
        assert provider._extras_disabled is False  # 不降级
        mock_log_usage.assert_called_once()
        assert mock_log_usage.call_args[1]["status"] == "error"

    @pytest.mark.asyncio
    async def test_stream_rejection_unrecognized_parameter_stream_terminal(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """网关文案 "unrecognized parameter 'stream' is not supported" 判为终态流拒绝。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        stream_calls: list[dict] = []
        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn(
            [
                _httpx_status(
                    400,
                    '{"error": "unrecognized parameter \'stream\' is not supported"}',
                )
            ],
            stream_calls,
        )

        client = LLMClient()
        client._provider = provider

        with pytest.raises(LLMCallError) as exc_info:
            await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is False
        assert len(stream_calls) == 1  # 不重试 stream
        # 关键：不得走普通参数降级（否则会带 stream 重试直至耗尽）
        assert provider._extras_disabled is False
        mock_log_usage.assert_called_once()
        assert mock_log_usage.call_args[1]["status"] == "error"

    @pytest.mark.asyncio
    async def test_stream_error_after_first_chunk_no_retry(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 7：首 chunk 后异常 → LLMCallError(retryable=True)，不重试。"""
        from app.services.llm.client import LLMCallError

        calls: list[dict] = []
        mock_sleep = AsyncMock()

        with patch(_sleep_patch_path(), mock_sleep):
            client = _build_client()
            client._provider.stream = _stream_partial_then_error(
                [StreamChunk(type="text_delta", text="partial")],
                RuntimeError("mid-stream broken"),
                calls,
            )
            with pytest.raises(LLMCallError) as exc_info:
                await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is True
        assert len(calls) == 1  # 不重试
        assert mock_sleep.await_count == 0
        # 中断记为错误落库
        mock_log_usage.assert_called_once()
        assert mock_log_usage.call_args[1]["status"] == "error"

    @pytest.mark.asyncio
    async def test_stream_chat_aclose_no_log_no_leak(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 8：消费方提前 aclose → 不落库成功、记 warning、无异常泄漏。"""
        chunks = [
            StreamChunk(type="text_delta", text="a"),
            StreamChunk(type="text_delta", text="b"),
        ]

        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([chunks])
            gen = client.stream_chat([Message(role="user", content="Q")], job_name="j")
            first = await gen.__anext__()
            assert first.text == "a"
            await gen.aclose()  # 不应抛异常

        mock_log_usage.assert_not_called()
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_stream_terminal_401_no_retry(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """场景 10：终态 4xx（401）→ LLMCallError(retryable=False)，不重试。"""
        from app.services.llm.client import LLMCallError, LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        calls: list[dict] = []
        mock_sleep = AsyncMock()
        provider = OpenAICompatProvider(
            api_base="https://test.api.com/v1", api_key="sk"
        )
        provider.stream = _stream_fn(
            [_httpx_status(401, '{"error": "Invalid API key"}')], calls
        )

        with patch("app.services.llm.client.asyncio.sleep", mock_sleep):
            client = LLMClient()
            client._provider = provider
            with pytest.raises(LLMCallError) as exc_info:
                await collect(client.stream_chat([Message(role="user", content="Q")]))

        assert exc_info.value.retryable is False
        assert len(calls) == 1
        assert mock_sleep.await_count == 0


# ===================================================================
# stream_chat 纯公开契约 + 元数据跟踪
# ===================================================================


class TestStreamChatContract:
    """stream_chat 纯公开契约（无下划线参数）与轻量元数据跟踪。"""

    def test_client_has_no_chat_alias(self):
        """chat() 过渡别名已删除：LLMClient 仅以 stream_chat 为唯一调用入口。"""
        from app.services.llm.client import LLMClient

        assert not hasattr(LLMClient, "chat"), (
            "LLMClient.chat() 过渡别名应已删除；消费方一律 collect(stream_chat(...))"
        )

    def test_stream_chat_signature_has_no_private_params(self):
        """stream_chat 不再暴露 _state / _result 下划线参数。"""
        import inspect

        from app.services.llm.client import LLMClient

        params = inspect.signature(LLMClient.stream_chat).parameters
        assert "_state" not in params
        assert "_result" not in params

    @pytest.mark.asyncio
    async def test_usage_missing_logs_zero_and_warns(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """流未上报 usage → 落库 0 且记 warning（网关忽略 stream_options 可见）。"""
        chunks = [
            StreamChunk(type="text_delta", text="hi"),
            StreamChunk(type="stop", stop_reason="end_turn"),
        ]
        with patch(_sleep_patch_path(), AsyncMock()):
            client = _build_client()
            client._provider.stream = _stream_fn([chunks])
            await collect(client.stream_chat([Message(role="user", content="Q")]))

        kwargs = mock_log_usage.call_args[1]
        assert kwargs["status"] == "success"
        assert kwargs["prompt_tokens"] == 0
        assert kwargs["completion_tokens"] == 0
        assert kwargs["total_tokens"] == 0
        warning_messages = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert any("usage" in msg for msg in warning_messages)


# ===================================================================
# openai_responses 工厂分支与枚举收口
# ===================================================================


class TestOpenAIResponsesProviderFactory:
    """openai_responses 注册与枚举收口。

    覆盖：_build_provider 构建并透传参数、LLMClient 集成路径、
    枚举三处一致（_PROVIDER_MAP / Literal / 非法值拒绝）。
    """

    def test_build_provider_openai_responses_passes_params(self):
        """_build_provider('openai_responses', cfg, proxy) 返回实例且参数透传正确。"""
        from app.services.llm.client import _build_provider
        from app.services.llm.providers.openai_responses import (
            OpenAIResponsesProvider,
        )

        cfg = _make_config("openai_responses", thinking_level="medium")
        provider = _build_provider("openai_responses", cfg, "http://proxy.local:7890")

        assert isinstance(provider, OpenAIResponsesProvider)
        assert provider.api_base == "https://test.api.com/v1"
        assert provider.api_key == "sk-test-key"
        assert provider.model == "gpt-4o-mini"
        assert provider.max_tokens == 2000
        assert provider.temperature == 0.7
        assert provider.timeout == 60
        # 全局 cfg 的 thinking_level 不再影响 provider 默认（仅任务级 kwargs 覆盖）
        assert provider.thinking_level == "off"
        assert provider.proxy == "http://proxy.local:7890"

    def test_build_provider_defaults_thinking_level_off(self):
        """cfg 未提供 thinking_level 时，构建出的 provider 默认为 off。"""
        from app.services.llm.client import _build_provider
        from app.services.llm.providers.openai_responses import (
            OpenAIResponsesProvider,
        )

        cfg = _make_config("openai_responses")
        provider = _build_provider("openai_responses", cfg, None)

        assert isinstance(provider, OpenAIResponsesProvider)
        assert provider.thinking_level == "off"
        assert provider.proxy is None

    def test_llm_client_uses_openai_responses(self, reset_llm_singleton):
        """LLMClient 依据配置 provider=openai_responses 实例化对应 provider。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.openai_responses import (
            OpenAIResponsesProvider,
        )

        cfg = _make_config("openai_responses")
        with patch(
            "app.services.llm.client.config_manager.get_llm_config",
            return_value=cfg,
        ):
            client = LLMClient()

        assert isinstance(client._provider, OpenAIResponsesProvider)
        assert client._provider_name == "openai_responses"

    def test_provider_map_contains_all_supported(self):
        """枚举一致性：_PROVIDER_MAP 同时含三类 provider（回归旧两类）。"""
        from app.services.llm.client import _PROVIDER_MAP

        assert set(_PROVIDER_MAP) == {
            "openai_compat",
            "anthropic_compat",
            "openai_responses",
        }

    def test_llm_config_update_accepts_openai_responses(self):
        """LLMConfigUpdate 接受 provider='openai_responses'。"""
        from app.models.summary import LLMConfigUpdate

        model = LLMConfigUpdate(provider="openai_responses")

        assert model.provider == "openai_responses"

    def test_llm_config_update_rejects_unknown_provider(self):
        """非法 provider 仍被 Literal 拒绝（422 边界）。"""
        from pydantic import ValidationError

        from app.models.summary import LLMConfigUpdate

        with pytest.raises(ValidationError):
            LLMConfigUpdate(provider=cast("Any", "banana"))
