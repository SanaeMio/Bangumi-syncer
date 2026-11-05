"""Anthropic 流内错误事件测试。

Anthropic 可能在 200 流里发送 ``event: error``（overloaded / api_error /
rate_limit_error），provider 必须抛出可识别的 ``ProviderStreamError``，
由 LLMClient 统一决定重试或终态，不能静默当成功。
"""

import json

import pytest

from app.services.llm.providers.anthropic import AnthropicProvider
from app.services.llm.providers.base import ProviderStreamError


def _make_anthropic() -> AnthropicProvider:
    return AnthropicProvider(
        api_base="https://api.anthropic.com/v1",
        api_key="sk-test",
        model="claude-sonnet-4-6",
        max_tokens=2000,
        temperature=0.7,
        timeout=60,
        thinking_level="off",
        prompt_cache=True,
    )


async def _aiter(lines: list[str]):
    for line in lines:
        yield line


def _error_event(error_type: str, message: str = "boom") -> list[str]:
    payload = {"type": "error", "error": {"type": error_type, "message": message}}
    return ["event: error", f"data: {json.dumps(payload)}", ""]


@pytest.mark.asyncio
async def test_anthropic_error_event_raises_retryable_provider_error():
    provider = _make_anthropic()

    with pytest.raises(ProviderStreamError) as exc_info:
        async for _ in provider._iter_stream_chunks(
            _aiter(_error_event("overloaded_error"))
        ):
            pass

    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_anthropic_invalid_request_error_is_not_retryable():
    provider = _make_anthropic()

    with pytest.raises(ProviderStreamError) as exc_info:
        async for _ in provider._iter_stream_chunks(
            _aiter(_error_event("invalid_request_error"))
        ):
            pass

    assert exc_info.value.retryable is False
