"""TraktClient：_make_request、速率限制、部分 API 错误路径（mock httpx）。"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.trakt.client import TraktClient


@pytest.fixture
def client():
    with patch(
        "app.services.trakt.client.config_manager.get_trakt_config",
        return_value={"client_id": "cid"},
    ):
        return TraktClient("access-token-xyz")


@pytest.mark.asyncio
async def test_make_request_200_and_204(client):
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"x": 1}
    ok.headers = httpx.Headers({})
    no_content = MagicMock(status_code=204)
    no_content.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(side_effect=[ok, no_content])
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            assert await client._make_request("GET", "/a") == {"x": 1}
            assert await client._make_request("GET", "/b") == {}


@pytest.mark.asyncio
async def test_make_request_401_raises(client):
    resp = MagicMock(status_code=401)
    resp.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(return_value=resp)
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with pytest.raises(ValueError, match="认证失败"):
                await client._make_request("GET", "/x")


@pytest.mark.asyncio
async def test_make_request_429_retries_then_ok(client):
    r429 = MagicMock(status_code=429)
    r429.headers = httpx.Headers({"Retry-After": "0"})
    r200 = MagicMock(status_code=200)
    r200.json.return_value = []
    r200.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(side_effect=[r429, r200])
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                out = await client._make_request("GET", "/sync/history")
    assert out == []


@pytest.mark.asyncio
async def test_make_request_429_invalid_retry_after_uses_default(client):
    r429 = MagicMock(status_code=429)
    r429.headers = httpx.Headers({"Retry-After": "bad"})
    r200 = MagicMock(status_code=200)
    r200.json.return_value = {}
    r200.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(side_effect=[r429, r200])
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                await client._make_request("GET", "/y")


@pytest.mark.asyncio
async def test_make_request_other_status_retries_then_none(client):
    r500 = MagicMock(status_code=500, text="err")
    r500.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(return_value=r500)
    client.max_retries = 2
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                assert await client._make_request("GET", "/z") is None


@pytest.mark.asyncio
async def test_make_request_request_error_retries(client):
    client._client = MagicMock()
    client._client.request = AsyncMock(
        side_effect=httpx.RequestError("x", request=MagicMock())
    )
    client.max_retries = 2
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                assert await client._make_request("GET", "/e") is None


@pytest.mark.asyncio
async def test_make_request_generic_exception_retries(client):
    client._client = MagicMock()
    client._client.request = AsyncMock(side_effect=RuntimeError("oops"))
    client.max_retries = 2
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                assert await client._make_request("GET", "/g") is None


def test_update_rate_limit_invalid_headers(client):
    h = httpx.Headers({"X-RateLimit-Remaining": "nan", "X-RateLimit-Reset": "x"})
    client._update_rate_limit(h)


@pytest.mark.asyncio
async def test_check_rate_limit_waits_when_low_quota(client):
    client.rate_limit_remaining = 5
    client.rate_limit_reset = time.time() + 0.05
    with patch("app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock) as sl:
        await client._check_rate_limit()
    sl.assert_awaited()


@pytest.mark.asyncio
async def test_get_watched_history_skips_bad_items(client):
    with patch.object(
        client,
        "_make_request",
        new_callable=AsyncMock,
        return_value=[{"invalid": True}],
    ):
        items = await client.get_watched_history()
    assert items == []
