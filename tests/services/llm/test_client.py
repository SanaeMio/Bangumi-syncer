"""Tests for app.services.llm.client (Task 1.4)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.llm.models import ChatResponse, Message, Usage

# ---------------------------------------------------------------------------
# test data
# ---------------------------------------------------------------------------

TEST_LLM_CONFIG = {
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
    """Reset the LLMClient singleton before and after each test."""
    import app.services.llm.client as client_mod

    client_mod._llm_client = None
    yield
    client_mod._llm_client = None


@pytest.fixture
def mock_config():
    """Mock config_manager.get_llm_config to return test config."""
    with patch(
        "app.services.llm.client.config_manager.get_llm_config",
        return_value=dict(TEST_LLM_CONFIG),
    ):
        yield


@pytest.fixture
def mock_log_usage():
    """Mock database_manager.llm_usage.log_usage for verification.

    The _log_usage method imports ``database_manager`` inside its body via
    ``from app.core.database import database_manager``.  Because the conftest
    already triggers the lazy instantiation, the module attribute exists and
    can be patched directly.
    """
    with patch("app.core.database.database_manager.llm_usage.log_usage") as mock_log:
        yield mock_log


@pytest.fixture
def mock_logger():
    """Mock app.core.logging.logger to verify log output."""
    with patch("app.services.llm.client.logger") as mock_log:
        yield mock_log


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _chat_patch_path():
    """Return the target string for patching the provider's chat method."""
    return "app.services.llm.client.OpenAICompatProvider.chat"


def _sleep_patch_path():
    """Return the target string for patching asyncio.sleep inside client."""
    return "app.services.llm.client.asyncio.sleep"


def _build_client(provider_chat_mock, *, mock_sleep=None):
    """Create an LLMClient inside active patches for provider.chat (and
    optionally asyncio.sleep).  Caller must manage the patches via
    ``with patch(...)`` context managers.
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
    """Tests for LLMClient.chat()."""

    @pytest.mark.asyncio
    async def test_chat_success(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """A single successful call returns ChatResponse and logs usage."""
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

        mock_logger.info.assert_called_once()
        log_msg = mock_logger.info.call_args[0][0]
        assert "gpt-4o-mini" in log_msg
        assert "15" in log_msg
        assert "latency=" in log_msg

    @pytest.mark.asyncio
    async def test_chat_retry_succeeds_on_retry(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """First attempt fails, retry succeeds on attempt 2."""
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
        assert response.usage.total_tokens == 8
        assert mock_chat.await_count == 2
        mock_sleep.assert_awaited_once_with(1)

        mock_log_usage.assert_called_once()
        assert mock_log_usage.call_args[1]["status"] == "success"

    @pytest.mark.asyncio
    async def test_chat_all_retries_exhausted(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """All 3 attempts (initial + 2 retries) fail -> error ChatResponse."""
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
        """Correct backoff delays: 1 s then 3 s between retries."""
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
        """Success path logs status='success' and correct token counts."""
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
        """Failure path logs status='error' and includes error_message."""
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
        assert kwargs["error_message"] == "Token exceeded"
        assert isinstance(kwargs["latency_ms"], int)

    @pytest.mark.asyncio
    async def test_logger_info_contains_model_tokens_latency(
        self, reset_llm_singleton, mock_config, mock_log_usage, mock_logger
    ):
        """Success path logger.info includes model, tokens, and latency."""
        mock_chat = AsyncMock()
        mock_chat.return_value = ChatResponse(
            content="OK",
            model="test-model-v1",
            usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

        with patch(_chat_patch_path(), mock_chat):
            client = _build_client(mock_chat)
            await client.chat([Message(role="user", content="Log test")])

        mock_logger.info.assert_called_once()
        log_msg = mock_logger.info.call_args[0][0]
        assert "test-model-v1" in log_msg
        assert "30" in log_msg
        assert "latency=" in log_msg

    @pytest.mark.asyncio
    async def test_log_usage_failure_does_not_crash_chat(
        self, reset_llm_singleton, mock_config, mock_logger
    ):
        """If log_usage raises, chat still returns the response (best-effort)."""
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
        """chat() works fine when job_id and job_name are omitted (None)."""
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
        """When response.usage is None, token counts default to 0."""
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
        """Extra kwargs passed to chat() are forwarded to the provider."""
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
# get_llm_client singleton
# ===================================================================


class TestGetLlmClient:
    """Tests for the get_llm_client() singleton function."""

    def test_returns_same_instance(self, reset_llm_singleton, mock_config):
        """get_llm_client() returns the same instance on repeated calls."""
        from app.services.llm.client import get_llm_client

        client1 = get_llm_client()
        client2 = get_llm_client()
        assert client1 is client2

    def test_returns_llm_client_instance(self, reset_llm_singleton, mock_config):
        """get_llm_client() returns an LLMClient instance."""
        from app.services.llm.client import LLMClient, get_llm_client

        client = get_llm_client()
        assert isinstance(client, LLMClient)

    def test_two_calls_under_same_fixture(self, reset_llm_singleton, mock_config):
        """Multiple calls within a single test all return the same object."""
        from app.services.llm.client import get_llm_client

        clients = [get_llm_client() for _ in range(5)]
        first = clients[0]
        for c in clients[1:]:
            assert c is first
