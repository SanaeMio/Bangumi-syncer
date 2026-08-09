"""应用 Release 信息与 GitHub latest / releases 列表（respx）。"""

import re
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import app_release, deps
from app.api.app_release import (
    _ensure_latest_in_list,
    _merge_latest_if_missing,
    release_info,
)
from app.utils import github_release
from app.utils.github_release import LatestReleaseResult, ReleaseListItem


@pytest.fixture(autouse=True)
def _clear_gh_cache():

    github_release.clear_github_release_cache()

    yield

    github_release.clear_github_release_cache()


@pytest.mark.asyncio
async def test_release_info_without_login():

    app = FastAPI()

    app.include_router(app_release.router)

    async def _anon():

        return None

    app.dependency_overrides[deps.get_current_user_optional] = _anon

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/app/release-info")

    assert r.status_code == 200

    body = r.json()

    assert body["current_version"]

    assert body["current_version_display"].startswith("v")

    assert body["remote_loaded"] is False

    assert body["latest_version"] is None

    assert body["newer_releases"] == []


def _mock_latest():

    return httpx.Response(
        200,
        json={
            "tag_name": "v9.0.0",
            "html_url": "https://github.com/SanaeMio/Bangumi-syncer/releases/tag/v9.0.0",
            "name": "Nine",
            "body": "## Notes\nhello",
            "published_at": "2026-01-01T12:00:00Z",
        },
        headers={"ETag": '"abc"'},
    )


def _mock_releases_page(json_body):

    return httpx.Response(200, json=json_body)


@pytest.mark.asyncio
@respx.mock
async def test_release_info_with_login_and_github():

    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=_mock_latest())

    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=_mock_releases_page([])
    )

    app = FastAPI()

    app.include_router(app_release.router)

    async def _user():

        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_optional] = _user

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.get_version", return_value="1.0.0"):
            r = await ac.get("/api/app/release-info")

    assert r.status_code == 200

    body = r.json()

    assert body["remote_loaded"] is True

    assert body["current_version_display"] == "v1.0.0"

    assert body["latest_version"] == "9.0.0"

    assert body["latest_version_display"] == "v9.0.0"

    assert body["update_available"] is True

    assert body["release_url"].endswith("v9.0.0")

    assert body["updates_behind"] == 1

    assert len(body["newer_releases"]) == 1

    nr0 = body["newer_releases"][0]

    assert nr0["semver"] == "9.0.0"

    assert "hello" in (nr0.get("body_html") or "")

    assert "<" in (nr0.get("body_html") or "")


@pytest.mark.asyncio
@respx.mock
async def test_release_info_multiple_versions():

    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=_mock_latest())

    rel_json = [
        {
            "tag_name": "v9.0.0",
            "draft": False,
            "html_url": "https://github.com/SanaeMio/Bangumi-syncer/releases/tag/v9.0.0",
            "name": "Nine",
            "body": "## Nine\nlast",
            "published_at": "2026-03-01T00:00:00Z",
        },
        {
            "tag_name": "v3.0.0",
            "draft": False,
            "html_url": "https://github.com/SanaeMio/Bangumi-syncer/releases/tag/v3.0.0",
            "name": "Three",
            "body": "## Three\nmid",
            "published_at": "2026-02-01T00:00:00Z",
        },
        {
            "tag_name": "v2.0.0",
            "draft": False,
            "html_url": "https://github.com/SanaeMio/Bangumi-syncer/releases/tag/v2.0.0",
            "name": "Two",
            "body": "## Two\nold",
            "published_at": "2026-01-15T00:00:00Z",
        },
    ]

    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=_mock_releases_page(rel_json)
    )

    app = FastAPI()

    app.include_router(app_release.router)

    async def _user():

        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_optional] = _user

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.get_version", return_value="1.0.0"):
            r = await ac.get("/api/app/release-info")

    assert r.status_code == 200

    body = r.json()

    assert body["updates_behind"] == 3

    sems = [x["semver"] for x in body["newer_releases"]]

    assert sems == ["9.0.0", "3.0.0", "2.0.0"]

    assert "mid" in body["newer_releases"][1]["body_html"]


@pytest.mark.asyncio
@respx.mock
async def test_release_info_releases_list_error_still_shows_latest():

    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=_mock_latest())

    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=httpx.Response(500)
    )

    app = FastAPI()

    app.include_router(app_release.router)

    async def _user():

        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_optional] = _user

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.get_version", return_value="1.0.0"):
            r = await ac.get("/api/app/release-info")

    assert r.status_code == 200

    body = r.json()

    assert body["releases_fetch_error"]

    assert body["updates_behind"] == 1

    assert len(body["newer_releases"]) == 1

    assert body["newer_releases"][0]["semver"] == "9.0.0"


@pytest.mark.asyncio
@respx.mock
async def test_release_info_github_error():

    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=httpx.Response(403))

    app = FastAPI()

    app.include_router(app_release.router)

    async def _user():

        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_optional] = _user

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.get_version", return_value="3.0.0"):
            r = await ac.get("/api/app/release-info")

    assert r.status_code == 200

    body = r.json()

    assert body["github_error"]

    assert body["current_version_display"] == "v3.0.0"

    assert body["newer_releases"] == []


@pytest.fixture
def _app_with_anon():

    app = FastAPI()

    app.include_router(app_release.router)

    async def _anon():

        return None

    app.dependency_overrides[deps.get_current_user_optional] = _anon

    return app


@pytest.fixture
def _app_with_auth():

    app = FastAPI()

    app.include_router(app_release.router)

    async def _user():

        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_optional] = _user

    return app


@pytest.mark.asyncio
async def test_release_info_environment_direct(_app_with_anon):

    transport = ASGITransport(app=_app_with_anon)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.docker_helper") as mock_dh:
            mock_dh.is_docker = False

            with patch("app.api.app_release.upgrade_service") as mock_us:
                mock_us.is_upgrade_capable.return_value = True

                r = await ac.get("/api/app/release-info")

    body = r.json()

    assert body["environment"] == "direct"

    assert body["upgrade_available"] is True


@pytest.mark.asyncio
async def test_release_info_environment_docker(_app_with_anon):

    transport = ASGITransport(app=_app_with_anon)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.docker_helper") as mock_dh:
            mock_dh.is_docker = True

            with patch("app.api.app_release.upgrade_service") as mock_us:
                mock_us.is_upgrade_capable.return_value = False

                r = await ac.get("/api/app/release-info")

    body = r.json()

    assert body["environment"] == "docker"

    assert body["upgrade_available"] is False


@pytest.mark.asyncio
@respx.mock
async def test_release_info_with_login_has_environment(_app_with_auth):

    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=_mock_latest())

    respx.get(url=re.compile(r".*/Bangumi-syncer/releases\?.*")).mock(
        return_value=_mock_releases_page([])
    )

    transport = ASGITransport(app=_app_with_auth)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.get_version", return_value="1.0.0"):
            with patch("app.api.app_release.docker_helper") as mock_dh:
                mock_dh.is_docker = False

                with patch("app.api.app_release.upgrade_service") as mock_us:
                    mock_us.is_upgrade_capable.return_value = True

                    r = await ac.get("/api/app/release-info")

    body = r.json()

    assert body["environment"] == "direct"

    assert body["upgrade_available"] is True

    assert body["update_available"] is True


@pytest.mark.asyncio
@respx.mock
async def test_release_info_github_error_has_environment(_app_with_auth):

    respx.get(github_release.GITHUB_LATEST_URL).mock(return_value=httpx.Response(403))

    transport = ASGITransport(app=_app_with_auth)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.api.app_release.get_version", return_value="3.0.0"):
            with patch("app.api.app_release.docker_helper") as mock_dh:
                mock_dh.is_docker = False

                with patch("app.api.app_release.upgrade_service") as mock_us:
                    mock_us.is_upgrade_capable.return_value = False

                    r = await ac.get("/api/app/release-info")

    body = r.json()

    assert body["environment"] == "direct"

    assert body["upgrade_available"] is False

    assert body["github_error"]


# ===== 以下为 release_info 函数级用例（原 test_app_release_api.py）=====
@pytest.mark.asyncio
async def test_release_history_minor_line_when_no_newer():
    gh = LatestReleaseResult(
        ok=True,
        tag_name="v3.11.1",
        html_url="https://github.com/example/r",
        name="Release 3.11.1",
        body="## 3.11.1\npatch",
        published_at="2024-02-01T00:00:00Z",
    )
    minor_rows = [
        ReleaseListItem(
            tag_name="v3.11.1",
            semver="3.11.1",
            html_url="https://github.com/example/3111",
            name="Release 3.11.1",
            body="## 3.11.1\npatch",
            published_at="2024-02-01T00:00:00Z",
        ),
        ReleaseListItem(
            tag_name="v3.11.0",
            semver="3.11.0",
            html_url="https://github.com/example/3110",
            name="Release 3.11.0",
            body="## 3.11.0\nbase",
            published_at="2024-01-01T00:00:00Z",
        ),
    ]

    with patch("app.api.app_release.get_version", return_value="3.11.1"):
        with patch(
            "app.api.app_release.fetch_latest_release",
            new_callable=AsyncMock,
            return_value=gh,
        ):
            with patch(
                "app.api.app_release.fetch_newer_releases_than",
                new_callable=AsyncMock,
                return_value=([], None),
            ):
                with patch(
                    "app.api.app_release.fetch_releases_in_minor_line",
                    new_callable=AsyncMock,
                    return_value=(minor_rows, None),
                ):
                    out = await release_info(user={"username": "u"})

    assert out.remote_loaded is True
    assert out.newer_releases == []
    assert len(out.release_history) == 2
    assert [x.semver for x in out.release_history] == ["3.11.1", "3.11.0"]
    assert "patch" in out.release_history[0].body_html
    assert "base" in out.release_history[1].body_html


@pytest.mark.asyncio
async def test_release_history_fallback_single_latest_when_minor_fetch_empty():
    gh = LatestReleaseResult(
        ok=True,
        tag_name="v2.0.0",
        html_url="https://github.com/example/r",
        name="Release 2",
        body="## R2\nb",
        published_at="2024-02-01T00:00:00Z",
    )

    with patch("app.api.app_release.get_version", return_value="2.0.0"):
        with patch(
            "app.api.app_release.fetch_latest_release",
            new_callable=AsyncMock,
            return_value=gh,
        ):
            with patch(
                "app.api.app_release.fetch_newer_releases_than",
                new_callable=AsyncMock,
                return_value=([], None),
            ):
                with patch(
                    "app.api.app_release.fetch_releases_in_minor_line",
                    new_callable=AsyncMock,
                    return_value=([], "拉取发行列表超时"),
                ):
                    out = await release_info(user={"username": "u"})

    assert out.remote_loaded is True
    assert out.newer_releases == []
    assert len(out.release_history) == 1
    assert out.release_history[0].tag_name == "v2.0.0"
    assert "b" in out.release_history[0].body_html
    assert out.releases_fetch_error == "拉取发行列表超时"


@pytest.mark.asyncio
async def test_release_history_empty_when_newer_exists():
    gh = LatestReleaseResult(
        ok=True,
        tag_name="v2.0.0",
        html_url="https://github.com/example/r",
        name="Release 2",
        body="body",
        published_at=None,
    )
    row = ReleaseListItem(
        tag_name="v2.0.0",
        semver="2.0.0",
        html_url="https://github.com/example/r",
        name="Release 2",
        body="body",
        published_at=None,
    )

    with patch("app.api.app_release.get_version", return_value="2.0.0"):
        with patch(
            "app.api.app_release.fetch_latest_release",
            new_callable=AsyncMock,
            return_value=gh,
        ):
            with patch(
                "app.api.app_release.fetch_newer_releases_than",
                new_callable=AsyncMock,
                return_value=([row], None),
            ):
                with patch(
                    "app.api.app_release.fetch_releases_in_minor_line",
                    new_callable=AsyncMock,
                ) as minor_mock:
                    out = await release_info(user={"username": "u"})

    assert len(out.newer_releases) == 1
    assert out.release_history == []
    minor_mock.assert_not_called()


def test_merge_latest_if_missing_noop_when_latest_sem_or_tag_empty():
    items: list[ReleaseListItem] = []
    _merge_latest_if_missing(
        items,
        latest_sem="",
        gh_tag="v1.0.0",
        gh_html="u",
        gh_name="n",
        gh_body=None,
        gh_published=None,
        current_cmp="0.1.0",
    )
    assert items == []

    _merge_latest_if_missing(
        items,
        latest_sem="2.0.0",
        gh_tag="",
        gh_html=None,
        gh_name=None,
        gh_body=None,
        gh_published=None,
        current_cmp="1.0.0",
    )
    assert items == []


def test_merge_latest_if_missing_noop_when_latest_already_present():
    items = [
        ReleaseListItem(
            tag_name="v2.0.0",
            semver="2.0.0",
            html_url=None,
            name=None,
            body=None,
            published_at=None,
        )
    ]
    _merge_latest_if_missing(
        items,
        latest_sem="2.0.0",
        gh_tag="v2.0.0",
        gh_html=None,
        gh_name=None,
        gh_body=None,
        gh_published=None,
        current_cmp="1.0.0",
    )
    assert len(items) == 1


def test_merge_latest_if_missing_noop_when_current_not_less_than_latest():
    items: list[ReleaseListItem] = []
    _merge_latest_if_missing(
        items,
        latest_sem="2.0.0",
        gh_tag="v2.0.0",
        gh_html=None,
        gh_name=None,
        gh_body=None,
        gh_published=None,
        current_cmp="3.0.0",
    )
    assert items == []


def test_merge_latest_if_missing_skips_when_compare_raises():
    items: list[ReleaseListItem] = []
    with patch("app.api.app_release.is_less_than", side_effect=ValueError("x")):
        _merge_latest_if_missing(
            items,
            latest_sem="2.0.0",
            gh_tag="v2.0.0",
            gh_html=None,
            gh_name=None,
            gh_body=None,
            gh_published=None,
            current_cmp="1.0.0",
        )
    assert items == []


def test_merge_latest_if_missing_appends_when_absent():
    items: list[ReleaseListItem] = []
    _merge_latest_if_missing(
        items,
        latest_sem="2.0.0",
        gh_tag="v2.0.0",
        gh_html="https://r",
        gh_name="T",
        gh_body="## Hi",
        gh_published="2026-01-01T00:00:00Z",
        current_cmp="1.0.0",
    )
    assert len(items) == 1
    assert items[0].semver == "2.0.0"
    assert items[0].tag_name == "v2.0.0"


def test_ensure_latest_in_list_appends_when_absent():
    items = [
        ReleaseListItem(
            tag_name="v3.11.0",
            semver="3.11.0",
            html_url=None,
            name=None,
            body=None,
            published_at=None,
        )
    ]
    _ensure_latest_in_list(
        items,
        latest_sem="3.11.1",
        gh_tag="v3.11.1",
        gh_html="https://r",
        gh_name="T",
        gh_body="## Hi",
        gh_published="2026-01-01T00:00:00Z",
    )
    assert [x.semver for x in items] == ["3.11.1", "3.11.0"]


def test_ensure_latest_in_list_noop_when_present():
    items = [
        ReleaseListItem(
            tag_name="v3.11.1",
            semver="3.11.1",
            html_url=None,
            name=None,
            body=None,
            published_at=None,
        )
    ]
    _ensure_latest_in_list(
        items,
        latest_sem="3.11.1",
        gh_tag="v3.11.1",
        gh_html=None,
        gh_name=None,
        gh_body=None,
        gh_published=None,
    )
    assert len(items) == 1


@pytest.mark.asyncio
async def test_release_info_update_available_none_when_compare_raises():
    gh = LatestReleaseResult(
        ok=True,
        tag_name="v2.0.0",
        html_url="https://github.com/example/r",
        name="R2",
        body="b",
        published_at=None,
    )
    with patch("app.api.app_release.get_version", return_value="1.0.0"):
        with patch(
            "app.api.app_release.fetch_latest_release",
            new_callable=AsyncMock,
            return_value=gh,
        ):
            with patch(
                "app.api.app_release.fetch_newer_releases_than",
                new_callable=AsyncMock,
                return_value=([], None),
            ):
                with patch(
                    "app.api.app_release.is_less_than",
                    side_effect=ValueError("bad cmp"),
                ):
                    out = await release_info(user={"username": "u"})

    assert out.update_available is None
    assert out.remote_loaded is True


@pytest.mark.asyncio
async def test_release_history_unknown_semver_when_tag_is_only_v_prefix():
    gh = LatestReleaseResult(
        ok=True,
        tag_name="V",
        html_url="https://github.com/example/r",
        name="Weird",
        body="only v",
        published_at=None,
    )
    with patch("app.api.app_release.get_version", return_value="2.0.0"):
        with patch(
            "app.api.app_release.fetch_latest_release",
            new_callable=AsyncMock,
            return_value=gh,
        ):
            with patch(
                "app.api.app_release.fetch_newer_releases_than",
                new_callable=AsyncMock,
                return_value=([], None),
            ):
                with patch(
                    "app.api.app_release.fetch_releases_in_minor_line",
                    new_callable=AsyncMock,
                    return_value=([], None),
                ):
                    out = await release_info(user={"username": "u"})

    assert len(out.release_history) == 1
    assert out.release_history[0].semver == "unknown"
    assert "only v" in (out.release_history[0].body_html or "")


# ===== 当前为预发布版本的 semver 行为（原 test_app_release_prerelease.py）=====
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
