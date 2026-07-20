"""Tests for app.services.llm.providers.openai_compat (Task 1.3)."""

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
    """Create a mock httpx.AsyncClient ready for use in `async with`."""

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

    # Ensure async with returns the same mock_client,
    # and __aexit__ calls aclose (matching real httpx behaviour).
    mock_client.__aenter__.return_value = mock_client

    async def _mock_aexit(*args, **kwargs):
        await mock_client.aclose()

    mock_client.__aexit__ = _mock_aexit

    return mock_client


class TestOpenAICompatProviderInit:
    """Constructor and default values."""

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
    """Integration tests for chat() method using mocked httpx."""

    @pytest.mark.asyncio
    async def test_request_format(self):
        """Verify the correct request format is sent to the API."""
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

        # Check URL
        assert call_args[0][0] == "https://api.openai.com/v1/chat/completions"

        # Check request body
        body = call_args[1]["json"]
        assert body["model"] == "gpt-4o-mini"
        assert body["max_tokens"] == 2000
        assert body["temperature"] == 0.7
        assert body["messages"] == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        # Check headers
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Content-Type"] == "application/json"

        # Check timeout
        assert call_args[1]["timeout"] == 60

    @pytest.mark.asyncio
    async def test_normal_response_parsing(self):
        """Verify normal response parsing extracts content and usage."""
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
        """Response without usage field should still parse correctly."""
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
        """HTTP errors should raise httpx.HTTPStatusError."""
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
        """Timeout should propagate as httpx.TimeoutException."""
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
        """A non-JSON response should raise a json decode error."""
        mock_client = _make_mock_client(json_side_effect=ValueError("Invalid JSON"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            provider = OpenAICompatProvider(
                api_base="https://api.openai.com/v1", api_key="sk-test"
            )
            with pytest.raises(ValueError, match="Invalid JSON"):
                await provider.chat([Message(role="user", content="Q")])

    @pytest.mark.asyncio
    async def test_extra_kwargs_override_defaults(self):
        """Extra kwargs passed to chat() should override default parameters."""
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
        """The httpx client should be properly closed via context manager."""
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

        # Inside `async with`, __aexit__ should call aclose
        mock_client.aclose.assert_awaited_once()
