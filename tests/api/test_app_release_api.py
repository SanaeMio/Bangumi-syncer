"""release-info：已是最新时 release_history 含同 minor 线全部发行说明。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.app_release import (
    _ensure_latest_in_list,
    _merge_latest_if_missing,
    release_info,
)
from app.utils.github_release import LatestReleaseResult, ReleaseListItem


@pytest.mark.asyncio
async def test_release_info_anonymous_user_no_github():
    out = await release_info(user=None)
    assert out.remote_loaded is False
    assert out.latest_version is None
    assert out.newer_releases == []


@pytest.mark.asyncio
async def test_release_info_github_latest_failed():
    bad = LatestReleaseResult(ok=False, error="GitHub 挂了")
    with patch("app.api.app_release.get_version", return_value="1.0.0"):
        with patch(
            "app.api.app_release.fetch_latest_release",
            new_callable=AsyncMock,
            return_value=bad,
        ):
            out = await release_info(user={"username": "u"})

    assert out.remote_loaded is True
    assert out.github_error == "GitHub 挂了"
    assert out.newer_releases == []


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
    with patch("app.api.app_release.is_less_than", side_effect=RuntimeError("x")):
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
