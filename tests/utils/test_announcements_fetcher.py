"""公告拉取：本地文件、HTTP 多源、缓存。"""

import json
from unittest.mock import patch

import httpx
import pytest
import respx

from app.utils import announcements_fetcher as af


@pytest.fixture(autouse=True)
def _clear_cache():
    af.clear_announcements_cache()
    yield
    af.clear_announcements_cache()


SAMPLE_JSON = {
    "announcements": [
        {
            "id": "test-1",
            "title": "测试公告",
            "level": "info",
            "published_at": "2026-06-08T12:00:00+08:00",
            "body": "正文",
        }
    ]
}


@pytest.mark.asyncio
async def test_fetch_from_local_file(tmp_path):
    path = tmp_path / "ann.json"
    path.write_text(json.dumps(SAMPLE_JSON), encoding="utf-8")

    def _cfg(section, key, fallback=""):
        if key == "announcements_file":
            return str(path)
        return fallback

    with patch.object(af.config_manager, "get", side_effect=_cfg):
        result = await af.fetch_announcements()

    assert result.ok is True
    assert result.remote_loaded is True
    assert len(result.announcements) == 1
    assert result.announcements[0]["id"] == "test-1"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_http_success_uses_cache():
    respx.get(url__startswith="https://ghfast.top/").mock(
        return_value=httpx.Response(200, json=SAMPLE_JSON)
    )

    with patch.object(af.config_manager, "get", return_value=""):
        first = await af.fetch_announcements()
        second = await af.fetch_announcements()

    assert first.ok is True
    assert first.from_cache is False
    assert second.from_cache is True
    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_http_fallback_chain():
    respx.get(url__startswith="https://ghfast.top/").mock(
        return_value=httpx.Response(500)
    )
    respx.get(url__startswith="https://gh-proxy.com/").mock(
        return_value=httpx.Response(500)
    )
    respx.get(af.ANNOUNCEMENTS_RAW_URL).mock(
        return_value=httpx.Response(200, json=SAMPLE_JSON)
    )

    with patch.object(af.config_manager, "get", return_value=""):
        result = await af.fetch_announcements()

    assert result.ok is True
    assert result.announcements[0]["title"] == "测试公告"
    assert len(respx.calls) == 3


@pytest.mark.asyncio
@respx.mock
async def test_fetch_all_sources_fail():
    respx.get(url__startswith="https://ghfast.top/").mock(
        return_value=httpx.Response(500)
    )
    respx.get(url__startswith="https://gh-proxy.com/").mock(
        return_value=httpx.Response(500)
    )
    respx.get(af.ANNOUNCEMENTS_RAW_URL).mock(return_value=httpx.Response(500))

    with patch.object(af.config_manager, "get", return_value=""):
        result = await af.fetch_announcements()

    assert result.ok is False
    assert result.announcements == []
    assert result.remote_loaded is False
