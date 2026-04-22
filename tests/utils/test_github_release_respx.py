"""github_release 对外请求（respx）。"""

import re
from unittest.mock import patch

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


def test_strip_tag_for_semver():
    assert github_release.strip_tag_for_semver("v1.2.3") == "1.2.3"
    assert github_release.strip_tag_for_semver("V0.1.0-rc.1") == "0.1.0-rc.1"
    assert github_release.strip_tag_for_semver("2.0.0") == "2.0.0"
    assert github_release.strip_tag_for_semver("") == ""


def test_parse_release_row_skips_draft_and_invalid():
    assert (
        github_release._parse_release_row(
            {
                "tag_name": "v9.0.0",
                "draft": True,
                "html_url": "https://example.com",
            }
        )
        is None
    )
    assert github_release._parse_release_row(None) is None
    assert github_release._parse_release_row({"draft": False}) is None
    row = github_release._parse_release_row(
        {
            "tag_name": "v1.0.0-beta.1",
            "draft": False,
            "html_url": "https://example.com/r",
            "name": "B",
            "body": "x",
            "published_at": "2026-01-01T00:00:00Z",
        }
    )
    assert row is not None
    assert row.semver == "1.0.0-beta.1"
    assert row.tag_name == "v1.0.0-beta.1"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_orders_newest_first_with_prerelease():
    rel_json = [
        {
            "tag_name": "v1.0.0-beta",
            "draft": False,
            "html_url": "https://example.com/beta",
            "name": "beta",
            "body": "",
            "published_at": None,
        },
        {
            "tag_name": "v1.0.0-rc.2",
            "draft": False,
            "html_url": "https://example.com/rc2",
            "name": "rc2",
            "body": "",
            "published_at": None,
        },
        {
            "tag_name": "v1.0.0",
            "draft": False,
            "html_url": "https://example.com/stable",
            "name": "stable",
            "body": "",
            "published_at": None,
        },
        {
            "tag_name": "v0.9.0",
            "draft": False,
            "html_url": "https://example.com/old",
            "name": "old",
            "body": "",
            "published_at": None,
        },
    ]
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(200, json=rel_json)
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0-beta")
    assert err is None
    assert [x.semver for x in items] == ["1.0.0", "1.0.0-rc.2"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_deduplicates_same_semver():
    rel_json = [
        {
            "tag_name": "v2.0.0",
            "draft": False,
            "html_url": "https://example.com/a",
            "name": "first",
            "body": "",
            "published_at": None,
        },
        {
            "tag_name": "v2.0.0",
            "draft": False,
            "html_url": "https://example.com/b",
            "name": "second",
            "body": "",
            "published_at": None,
        },
    ]
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(200, json=rel_json)
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert err is None
    assert len(items) == 1
    assert items[0].semver == "2.0.0"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_second_call_uses_memory_cache_single_http():
    route = respx.get(github_release.GITHUB_LATEST_URL).mock(
        return_value=httpx.Response(200, json={"tag_name": "v1.0.0"})
    )
    r1 = await github_release.fetch_latest_release()
    r2 = await github_release.fetch_latest_release()
    assert r1.ok and r2.ok
    assert r2.from_cache is True
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_sends_if_none_match_and_handles_304():
    route = respx.get(github_release.GITHUB_LATEST_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json={"tag_name": "v3.1.0", "name": "A"},
                headers={"ETag": '"etag1"'},
            ),
            httpx.Response(304),
        ]
    )
    r1 = await github_release.fetch_latest_release()
    assert r1.ok and r1.tag_name == "v3.1.0"
    github_release._cache_expires_monotonic = 0.0
    r2 = await github_release.fetch_latest_release()
    assert r2.from_cache is True
    assert r2.tag_name == "v3.1.0"
    assert route.call_count == 2
    assert route.calls[1].request.headers.get("If-None-Match") == '"etag1"'


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_304_without_cached_body_returns_status_error():
    github_release.clear_github_release_cache()
    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=httpx.Response(304))
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert "304" in (r.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_404():
    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=httpx.Response(404))
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert "未找到" in (r.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_unexpected_status():
    respx.get(github_release.GITHUB_LATEST_URL).mock(
        return_value=httpx.Response(502, text="bad")
    )
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert "502" in (r.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_403():
    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=httpx.Response(403))
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert "403" in (r.error or "") or "拒绝" in (r.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_200_missing_tag_name():
    respx.get(github_release.GITHUB_LATEST_URL).mock(
        return_value=httpx.Response(200, json={"name": "only"})
    )
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert "tag_name" in (r.error or "")
    assert github_release._cache_body is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_200_body_not_json():
    respx.get(github_release.GITHUB_LATEST_URL).mock(
        return_value=httpx.Response(200, content=b"not json")
    )
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert "JSON" in (r.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_200_json_not_object():
    respx.get(github_release.GITHUB_LATEST_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    r = await github_release.fetch_latest_release()
    assert not r.ok
    assert "格式异常" in (r.error or "")


def test_build_result_from_payload_missing_tag_name():
    r = github_release._build_result_from_payload({"name": "x"}, from_cache=True)
    assert not r.ok
    assert "tag_name" in (r.error or "")
    assert r.from_cache is True

    r2 = github_release._build_result_from_payload({"tag_name": 123}, from_cache=False)
    assert not r2.ok


def test_parse_release_row_empty_semver_after_strip_v():
    assert github_release._parse_release_row({"tag_name": "v", "draft": False}) is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_403_returns_partial_error():
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(403)
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert items == []
    assert err and "限流" in err


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_non_200_returns_error():
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(501)
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert items == []
    assert err and "501" in err


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_invalid_json_body():
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(200, content=b"{")
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert items == []
    assert err and "JSON" in err


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_skips_row_when_compare_raises():
    rel_json = [
        {
            "tag_name": "v2.0.0",
            "draft": False,
            "html_url": "https://example.com/a",
            "name": "ok",
            "body": "",
            "published_at": None,
        },
    ]
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(200, json=rel_json)
    )
    with patch(
        "app.utils.github_release.is_strictly_newer",
        side_effect=RuntimeError("boom"),
    ):
        items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert err is None
    assert items == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_timeout():
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        side_effect=httpx.TimeoutException("t")
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert items == []
    assert err and "超时" in err


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_empty_first_page_stops():
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(200, json=[])
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert err is None
    assert items == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_skips_unparseable_rows():
    rel_json = [
        {"draft": False},
        {
            "tag_name": "v2.0.0",
            "draft": False,
            "html_url": "https://example.com/a",
            "name": "ok",
            "body": "",
            "published_at": None,
        },
    ]
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(200, json=rel_json)
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert err is None
    assert len(items) == 1
    assert items[0].semver == "2.0.0"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_newer_releases_than_connect_error():
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        side_effect=httpx.ConnectError("e")
    )
    items, err = await github_release.fetch_newer_releases_than("1.0.0")
    assert items == []
    assert err and "网络错误" in err
