"""provider 流式错误体读取回归测试。

httpx 的流式响应在未读取时访问 ``response.text`` 会抛 ``ResponseNotRead``。
provider 必须在 ``raise_for_status()`` 前 ``aread()``，否则 client 层的
参数降级（``_is_param_rejection``）与流式终态判定（``_is_stream_rejection``）
都会失效。

本文件同时守住"三个 provider 统一走公共流式方法"，防止以后新增 provider
再次遗漏错误体读取。
"""

from pathlib import Path

import httpx
import pytest

from app.services.llm.client import _is_param_rejection, _is_stream_rejection
from app.services.llm.providers import base as provider_base

PROVIDER_DIR = Path(__file__).resolve().parents[3] / "app/services/llm/providers"


class _AsyncBody(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aiter__(self):
        yield self._body


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    transport = httpx.MockTransport(lambda request: response)
    client = httpx.AsyncClient(transport=transport)

    def _factory(**kwargs):
        return client

    monkeypatch.setattr(provider_base, "create_async_client", _factory)


@pytest.mark.asyncio
async def test_open_stream_response_reads_error_body_before_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 未预读流：公共方法先 aread，HTTPStatusError.response.text 可读。"""
    body = b'{"error":"unknown parameter: stream"}'
    response = httpx.Response(400, stream=_AsyncBody(body))
    _install_mock_client(monkeypatch, response)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        async with provider_base.open_stream_response(
            url="https://example.test/v1/chat",
            body={"stream": True},
            headers={},
            timeout=1.0,
        ):
            pass

    assert "unknown parameter" in exc_info.value.response.text
    assert _is_stream_rejection(exc_info.value) is True


@pytest.mark.asyncio
async def test_open_stream_response_classifies_param_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 未预读流：参数拒绝可被 client 层正确识别。"""
    body = b'{"error":"unknown parameter: reasoning_effort"}'
    response = httpx.Response(400, stream=_AsyncBody(body))
    _install_mock_client(monkeypatch, response)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        async with provider_base.open_stream_response(
            url="https://example.test/v1/chat",
            body={"stream": True},
            headers={},
            timeout=1.0,
        ):
            pass

    assert _is_param_rejection(exc_info.value) is True


def test_all_providers_use_shared_stream_helper() -> None:
    """三个 provider 必须接入公共方法，且不得保留重复的错误体读取。"""
    for name in ("anthropic.py", "openai_compat.py", "openai_responses.py"):
        source = (PROVIDER_DIR / name).read_text(encoding="utf-8")
        assert "open_stream_response" in source, f"{name} 未接入公共流式方法"
        assert "await response.aread()" not in source, (
            f"{name} 仍有重复的错误体读取逻辑"
        )
