"""
应用版本与 GitHub Release 信息 API。

未登录时不请求 GitHub，仅返回 current_version。
"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析模型字段的 ``str | None`` 会失败，此处保留 Optional

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.app_version import get_display_version, get_version
from ..utils.github_release import (
    ReleaseListItem,
    fetch_latest_release,
    fetch_newer_releases_than,
    strip_tag_for_semver,
)
from ..utils.release_markdown import markdown_to_safe_html
from ..utils.semver_util import is_less_than, normalize_version_label, version_sort_key
from .deps import get_current_user_optional

router = APIRouter(prefix="/api", tags=["app"])


class NewerReleaseItem(BaseModel):
    """发行版一条（待更新列表或已是最新时的历史列表）；说明已转安全 HTML。"""

    semver: str = Field(description="去 v 后的比较用版本号")

    version_display: str = Field(description="界面展示用，带 v 前缀")

    tag_name: str

    title: Optional[str] = None

    body_html: str = Field(
        default="", description="Markdown 渲染后经 bleach 清洗的 HTML"
    )

    html_url: Optional[str] = None

    published_at: Optional[str] = None


class ReleaseInfoResponse(BaseModel):
    current_version: str

    current_version_display: str = Field(
        description="与 current_version 对应、供界面展示的 v 前缀形式（若已有 v 则不重复）"
    )

    remote_loaded: bool = Field(
        default=False,
        description="已登录并拉取到 GitHub 时为 True；未登录为 False",
    )

    latest_version: Optional[str] = None

    latest_version_display: Optional[str] = None

    update_available: Optional[bool] = Field(
        default=None,
        description="当前版本 semver 是否严格小于 latest；无法比较时为 None",
    )

    release_url: Optional[str] = None

    title: Optional[str] = None

    published_at: Optional[str] = None

    github_error: Optional[str] = None

    from_cache: Optional[bool] = None

    updates_behind: int = Field(
        default=0,
        description="严格新于当前的发行版数量（与 newer_releases 长度一致）",
    )

    newer_releases: list[NewerReleaseItem] = Field(
        default_factory=list,
        description="从新到旧；每条含可插入页面的 body_html",
    )

    release_history: list[NewerReleaseItem] = Field(
        default_factory=list,
        description="当 newer_releases 为空（已是最新）时，仅含远端 latest 一条发行说明",
    )

    releases_fetch_error: Optional[str] = Field(
        default=None,
        description="分页拉取 /releases 失败时的说明；若仍能展示 latest 合并结果，列表可能不完整",
    )


def _merge_latest_if_missing(
    items: list[ReleaseListItem],
    *,
    latest_sem: str,
    gh_tag: str | None,
    gh_html: str | None,
    gh_name: str | None,
    gh_body: str | None,
    gh_published: str | None,
    current_cmp: str,
) -> None:
    """当列表分页未包含 latest 对应 tag 时，用 releases/latest 补一条。"""

    if not latest_sem or not gh_tag:
        return

    if any(x.semver == latest_sem for x in items):
        return

    try:
        if not is_less_than(current_cmp, latest_sem):
            return

    except Exception:
        return

    items.append(
        ReleaseListItem(
            tag_name=gh_tag,
            semver=latest_sem,
            html_url=gh_html,
            name=gh_name,
            body=gh_body,
            published_at=gh_published,
        )
    )


@router.get("/app/release-info", response_model=ReleaseInfoResponse)
async def release_info(
    user: Optional[dict] = Depends(get_current_user_optional),
) -> ReleaseInfoResponse:

    current = get_version()

    cur_disp = get_display_version(current)

    if user is None:
        return ReleaseInfoResponse(
            current_version=current,
            current_version_display=cur_disp,
            remote_loaded=False,
        )

    gh = await fetch_latest_release()

    if not gh.ok:
        return ReleaseInfoResponse(
            current_version=current,
            current_version_display=cur_disp,
            remote_loaded=True,
            github_error=gh.error,
        )

    latest = strip_tag_for_semver(gh.tag_name or "")

    update_available: Optional[bool] = None

    if latest:
        try:
            update_available = is_less_than(current, latest)

        except Exception:
            update_available = None

    latest_disp = get_display_version(latest) if latest else None

    current_cmp = normalize_version_label(current)

    newer_raw, list_err = await fetch_newer_releases_than(current_cmp)

    _merge_latest_if_missing(
        newer_raw,
        latest_sem=latest,
        gh_tag=gh.tag_name,
        gh_html=gh.html_url,
        gh_name=gh.name,
        gh_body=gh.body,
        gh_published=gh.published_at,
        current_cmp=current_cmp,
    )

    newer_raw.sort(key=lambda x: version_sort_key(x.semver), reverse=True)

    newer_models = [
        NewerReleaseItem(
            semver=it.semver,
            version_display=get_display_version(it.semver),
            tag_name=it.tag_name,
            title=it.name,
            body_html=markdown_to_safe_html(it.body),
            html_url=it.html_url,
            published_at=it.published_at,
        )
        for it in newer_raw
    ]

    release_history_models: list[NewerReleaseItem] = []
    if not newer_models and (gh.tag_name or "").strip():
        sem = (latest or "").strip() or strip_tag_for_semver(gh.tag_name or "")
        if not sem:
            sem = (gh.tag_name or "").strip().lstrip("vV") or "unknown"
        disp = latest_disp or get_display_version(sem)
        release_history_models = [
            NewerReleaseItem(
                semver=sem,
                version_display=disp,
                tag_name=(gh.tag_name or "").strip(),
                title=gh.name,
                body_html=markdown_to_safe_html(gh.body),
                html_url=gh.html_url,
                published_at=gh.published_at,
            )
        ]

    return ReleaseInfoResponse(
        current_version=current,
        current_version_display=cur_disp,
        remote_loaded=True,
        latest_version=latest or None,
        latest_version_display=latest_disp,
        update_available=update_available,
        release_url=gh.html_url,
        title=gh.name,
        published_at=gh.published_at,
        github_error=None,
        from_cache=gh.from_cache,
        updates_behind=len(newer_models),
        newer_releases=newer_models,
        release_history=release_history_models,
        releases_fetch_error=list_err,
    )
