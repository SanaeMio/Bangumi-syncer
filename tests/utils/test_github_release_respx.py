"""github_release 对外请求（respx）。"""

import httpx
import pytest
import respx

from app.utils import github_release


@pytest.fixture(autouse=True)
def _clear_cache():
    github_release.clear_github_release_cache()
    yield
    github_release.clear_github_release_cache()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_success():
    respx.get(github_release.GITHUB_LATEST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v1.2.3",
                "html_url": "https://example.com/r",
                "name": "R",
                "body": "x",
                "published_at": "2026-01-01T00:00:00Z",
            },
        )
    )
    r = await github_release.fetch_latest_release()
    assert r.ok
    assert r.tag_name == "v1.2.3"
    assert r.html_url == "https://example.com/r"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_connect_error():
    respx.get(github_release.GITHUB_LATEST_URL).mock(
        side_effect=httpx.ConnectError("refused")
    )
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert r.error


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_timeout():
    respx.get(github_release.GITHUB_LATEST_URL).mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert "超时" in (r.error or "")
