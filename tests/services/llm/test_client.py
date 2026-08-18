"""app.services.llm.client 测试（任务 1.4）。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.llm.models import ChatResponse, Message, Usage

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


def _chat_patch_path():
    """返回用于 patch provider chat 方法的目标字符串。"""
    return "app.services.llm.client.OpenAICompatProvider.chat"


def _sleep_patch_path():
    """返回用于在 client 内部 patch asyncio.sleep 的目标字符串。"""
    return "app.services.llm.client.asyncio.sleep"


def _build_client(provider_chat_mock, *, mock_sleep=None):
    """在 provider.chat（及可选的 asyncio.sleep）的 active patch 中创建 LLMClient。

    调用方必须通过 ``with patch(...)`` 上下文管理器管理这些 patch。
    """
    from app.services.llm.client import LLMClient

    if mock_sleep is not None:
        with patch(_sleep_patch_path(), mock_sleep):
            return LLMClient()
    return LLMClient()


# ===================================================================
# LLMClient.chat
# ===================================================================


class TestLLMClientChat:
    """LLMClient.chat() 测试。"""

    @pytest.mark.asyncio
    async def test_chat_success(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """单次成功调用返回 ChatResponse 并记录 usage。"""
        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="Hello!",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        with patch(_chat_patch_path(), mock_chat):
            client = _build_client(mock_chat)
            messages = [Message(role="user", content="Hello")]
            response = await client.chat(messages, job_id=1, job_name="test")

        assert isinstance(response, ChatResponse)
        assert response.content == "Hello!"
        assert response.model == "gpt-4o-mini"
        assert response.usage is not None
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5
        assert response.usage.total_tokens == 15

        mock_chat.assert_awaited_once_with(messages)

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
        """首次尝试失败，第 2 次重试成功。"""
        mock_sleep = AsyncMock()
        mock_chat = AsyncMock()
        mock_chat.side_effect = [
            Exception("Connection error"),
            ChatResponse(
                content="Retry OK",
                model="gpt-4o-mini",
                usage=Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            ),
        ]

        with patch(_chat_patch_path(), mock_chat):
            with patch(_sleep_patch_path(), mock_sleep):
                client = _build_client(mock_chat, mock_sleep=mock_sleep)
                messages = [Message(role="user", content="Retry test")]
                response = await client.chat(messages)

        assert response.content == "Retry OK"
        assert response.usage is not None
        assert response.usage.total_tokens == 8
        assert mock_chat.await_count == 2
        mock_sleep.assert_awaited_once_with(1)

        mock_log_usage.assert_called_once()
        assert mock_log_usage.call_args[1]["status"] == "success"

    @pytest.mark.asyncio
    async def test_chat_all_retries_exhausted(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """全部 3 次尝试（首次 + 2 次重试）均失败 → 返回错误 ChatResponse。"""
        mock_sleep = AsyncMock()
        mock_chat = AsyncMock()
        mock_chat.side_effect = [
            Exception("Error 1"),
            Exception("Error 2"),
            Exception("Error 3"),
        ]

        with patch(_chat_patch_path(), mock_chat):
            with patch(_sleep_patch_path(), mock_sleep):
                client = _build_client(mock_chat, mock_sleep=mock_sleep)
                messages = [Message(role="user", content="Always fail")]
                response = await client.chat(messages)

        assert response.content == ""
        assert response.model == ""
        assert response.usage is None
        assert mock_chat.await_count == 3

        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["status"] == "error"
        assert "Error 3" in kwargs["error_message"]

        mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_backoff_delays(self, reset_llm_singleton, mock_config):
        """正确的退避延迟：重试间隔为 1 秒，然后 3 秒。"""
        mock_sleep = AsyncMock()
        mock_chat = AsyncMock()
        mock_chat.side_effect = [
            Exception("Fail 1"),
            Exception("Fail 2"),
            Exception("Fail 3"),
        ]

        with patch(_chat_patch_path(), mock_chat):
            with patch(_sleep_patch_path(), mock_sleep):
                client = _build_client(mock_chat, mock_sleep=mock_sleep)
                await client.chat([Message(role="user", content="Test")])

        assert mock_sleep.await_count == 2
        mock_sleep.assert_any_await(1)
        mock_sleep.assert_any_await(3)

    @pytest.mark.asyncio
    async def test_success_logs_correct_tokens(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """成功路径记录 status='success' 和正确的 token 计数。"""
        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="OK",
            model="claude-3",
            usage=Usage(prompt_tokens=42, completion_tokens=58, total_tokens=100),
        )

        with patch(_chat_patch_path(), mock_chat):
            client = _build_client(mock_chat)
            await client.chat([Message(role="user", content="Count tokens")])

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
        """失败路径记录 status='error' 并包含 error_message。"""
        mock_sleep = AsyncMock()
        mock_chat = AsyncMock()
        mock_chat.side_effect = [
            RuntimeError("Token exceeded"),
            RuntimeError("Token exceeded"),
            RuntimeError("Token exceeded"),
        ]

        with patch(_chat_patch_path(), mock_chat):
            with patch(_sleep_patch_path(), mock_sleep):
                client = _build_client(mock_chat, mock_sleep=mock_sleep)
                await client.chat([Message(role="user", content="Error test")])

        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["status"] == "error"
        assert kwargs["error_message"] == "RuntimeError: Token exceeded"
        assert isinstance(kwargs["latency_ms"], int)

    @pytest.mark.asyncio
    async def test_logger_info_contains_model_tokens_latency(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """成功路径 logger.info 包含模型、token 和延迟信息。"""
        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="OK",
            model="test-model-v1",
            usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

        with patch(_chat_patch_path(), mock_chat):
            client = _build_client(mock_chat)
            await client.chat([Message(role="user", content="Log test")])

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
        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="Hello!",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        with patch(_chat_patch_path(), mock_chat):
            with patch(
                "app.core.database.database_manager.llm_usage.log_usage",
                side_effect=OSError("DB down"),
            ):
                client = _build_client(mock_chat)
                messages = [Message(role="user", content="test")]
                response = await client.chat(messages)

        assert response.content == "Hello!"
        assert response.model == "gpt-4o-mini"
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_chat_without_optional_params(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """当 job_id 和 job_name 省略（None）时，chat() 正常工作。"""
        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="OK",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

        with patch(_chat_patch_path(), mock_chat):
            client = _build_client(mock_chat)
            response = await client.chat([Message(role="user", content="Q")])

        assert response.content == "OK"
        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["job_id"] is None
        assert kwargs["job_name"] == ""
        assert kwargs["status"] == "success"

    @pytest.mark.asyncio
    async def test_chat_usage_none_handles_zero_tokens(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """当 response.usage 为 None 时，token 计数默认为 0。"""
        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="No usage",
            model="gpt-4o-mini",
            usage=None,
        )

        with patch(_chat_patch_path(), mock_chat):
            client = _build_client(mock_chat)
            await client.chat([Message(role="user", content="Q")])

        mock_log_usage.assert_called_once()
        kwargs = mock_log_usage.call_args[1]
        assert kwargs["prompt_tokens"] == 0
        assert kwargs["completion_tokens"] == 0
        assert kwargs["total_tokens"] == 0

    @pytest.mark.asyncio
    async def test_chat_passes_kwargs_to_provider(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """传递给 chat() 的额外 kwargs 会转发给 provider。"""
        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="OK",
            model="gpt-4o-mini",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

        with patch(_chat_patch_path(), mock_chat):
            client = _build_client(mock_chat)
            await client.chat(
                [Message(role="user", content="Q")],
                temperature=0.1,
                max_tokens=100,
            )

        mock_chat.assert_awaited_once()
        _args, call_kwargs = mock_chat.call_args
        assert call_kwargs.get("temperature") == 0.1
        assert call_kwargs.get("max_tokens") == 100


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
        # thinking_level 缺省 off
        assert provider.thinking_level == "off"

    def test_anthropic_thinking_level_passed(self, reset_llm_singleton):
        """Scenario 4.1: thinking_level 从配置传入 provider。"""
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
        assert provider.thinking_level == "high"

    def test_openai_provider_no_thinking_level(self, reset_llm_singleton):
        """openai_compat 分支不受 thinking_level 影响（Phase 1 不改动）。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.openai_compat import OpenAICompatProvider

        cfg = _make_config("openai_compat", thinking_level="high")
        with patch(
            "app.services.llm.client.config_manager.get_llm_config",
            return_value=cfg,
        ):
            client = LLMClient()

        assert isinstance(client._provider, OpenAICompatProvider)
        assert not hasattr(client._provider, "thinking_level")

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
        """Scenario 4.4: chat() 的 thinking_level kwargs 透传到 AnthropicProvider。"""
        from app.services.llm.client import LLMClient
        from app.services.llm.providers.anthropic import AnthropicProvider

        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="OK",
            model="claude-sonnet-4-6",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

        cfg = _make_config("anthropic_compat")
        with patch(
            "app.services.llm.client.config_manager.get_llm_config",
            return_value=cfg,
        ):
            with patch.object(AnthropicProvider, "chat", mock_chat):
                client = LLMClient()
                await client.chat(
                    [Message(role="user", content="Q")],
                    thinking_level="high",
                )

        _args, call_kwargs = mock_chat.call_args
        assert call_kwargs.get("thinking_level") == "high"


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
