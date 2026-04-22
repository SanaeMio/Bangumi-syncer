"""release-info：当前为预发布时与 latest / 列表的 semver 行为（respx）。"""

import re
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import app_release, deps
from app.utils import github_release


@pytest.fixture(autouse=True)
def _clear_gh_cache():
    github_release.clear_github_release_cache()
    yield
    github_release.clear_github_release_cache()


@pytest.mark.asyncio
@respx.mock
async def test_release_info_prerelease_current_sorts_newer_with_stable_latest():
    respx.get(github_release.GITHUB_LATEST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v1.0.0",
                "html_url": "https://github.com/SanaeMio/Bangumi-syncer/releases/tag/v1.0.0",
                "name": "Stable",
                "body": "## 1.0\nstable",
                "published_at": "2026-06-01T00:00:00Z",
            },
        )
    )
    rel_json = [
        {
            "tag_name": "v1.0.0-rc.2",
            "draft": False,
            "html_url": "https://github.com/example/rc2",
            "name": "RC2",
            "body": "rc2",
            "published_at": None,
        },
        {
            "tag_name": "v1.0.0",
            "draft": False,
            "html_url": "https://github.com/example/stable",
            "name": "Stable",
            "body": "stable",
            "published_at": None,
        },
    ]
    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(200, json=rel_json)
    )

    app = FastAPI()
    app.include_router(app_release.router)

    async def _user():
        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_optional] = _user
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.get_version", return_value="1.0.0-rc.1"):
            r = await ac.get("/api/app/release-info")

    assert r.status_code == 200
    body = r.json()
    assert body["current_version"] == "1.0.0-rc.1"
    assert body["current_version_display"] == "v1.0.0-rc.1"
    assert body["latest_version"] == "1.0.0"
    assert body["update_available"] is True
    assert body["updates_behind"] == 2
    sems = [x["semver"] for x in body["newer_releases"]]
    assert sems == ["1.0.0", "1.0.0-rc.2"]
    assert body["release_history"] == []
