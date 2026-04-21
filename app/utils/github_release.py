"""
查询 GitHub 仓库 latest release（api.github.com），带短时缓存与 ETag。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..core.logging import logger
from .semver_util import is_strictly_newer, version_tuple

GITHUB_LATEST_URL = (
    "https://api.github.com/repos/SanaeMio/Bangumi-syncer/releases/latest"
)
GITHUB_USER_AGENT = (
    "SanaeMio/Bangumi-syncer (https://github.com/SanaeMio/Bangumi-syncer)"
)
GITHUB_RELEASES_URL = "https://api.github.com/repos/SanaeMio/Bangumi-syncer/releases"
REQUEST_TIMEOUT = 15.0
CACHE_TTL_SEC = 300.0
RELEASES_LIST_TIMEOUT = 20.0

_cache_body: dict[str, Any] | None = None
_cache_expires_monotonic: float = 0.0
_cache_etag: str | None = None


@dataclass
class LatestReleaseResult:
    ok: bool
    tag_name: str | None = None
    html_url: str | None = None
    name: str | None = None
    body: str | None = None
    published_at: str | None = None
    error: str | None = None
    from_cache: bool = False


@dataclass
class ReleaseListItem:
    """单条 Release（列表 API），semver 为去 v 后的比较串。"""

    tag_name: str
    semver: str
    html_url: str | None
    name: str | None
    body: str | None
    published_at: str | None


def _strip_tag_version(tag_name: str) -> str:
    t = (tag_name or "").strip()
    if t.startswith("v") or t.startswith("V"):
        return t[1:].strip()
    return t


async def fetch_latest_release() -> LatestReleaseResult:
    """
    获取 releases/latest。成功时解析 JSON；失败时 ok=False 并带 error 说明。
    使用内存缓存（成功响应）降低对 api.github.com 的请求频率。
    """
    global _cache_body, _cache_expires_monotonic, _cache_etag

    now = time.monotonic()
    if _cache_body is not None and now < _cache_expires_monotonic:
        return _build_result_from_payload(_cache_body, from_cache=True)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": GITHUB_USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if _cache_etag:
        headers["If-None-Match"] = _cache_etag

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(GITHUB_LATEST_URL, headers=headers)
    except httpx.TimeoutException:
        logger.warning("GitHub releases/latest 请求超时")
        return LatestReleaseResult(ok=False, error="请求 GitHub 超时")
    except httpx.RequestError as e:
        logger.warning("GitHub releases/latest 网络错误: %s", e)
        return LatestReleaseResult(ok=False, error="无法连接 GitHub")

    if r.status_code == 304 and _cache_body is not None:
        return _build_result_from_payload(_cache_body, from_cache=True)

    if r.status_code == 403:
        logger.warning("GitHub releases/latest 403: %s", r.text[:200])
        return LatestReleaseResult(
            ok=False, error="GitHub API 拒绝访问（可能触发限流）"
        )
    if r.status_code == 404:
        return LatestReleaseResult(ok=False, error="未找到 latest release")
    if r.status_code != 200:
        logger.warning(
            "GitHub releases/latest 非预期状态 %s: %s",
            r.status_code,
            r.text[:200],
        )
        return LatestReleaseResult(ok=False, error=f"GitHub 返回状态码 {r.status_code}")

    try:
        data = r.json()
    except ValueError:
        return LatestReleaseResult(ok=False, error="GitHub 响应不是合法 JSON")

    if not isinstance(data, dict):
        return LatestReleaseResult(ok=False, error="GitHub 响应格式异常")

    built = _build_result_from_payload(data, from_cache=False)
    if not built.ok:
        return built

    etag = r.headers.get("ETag")
    if etag:
        _cache_etag = etag
    _cache_body = data
    _cache_expires_monotonic = time.monotonic() + CACHE_TTL_SEC

    return built


def _build_result_from_payload(
    data: dict[str, Any], *, from_cache: bool
) -> LatestReleaseResult:
    tag = data.get("tag_name")
    if not tag or not isinstance(tag, str):
        return LatestReleaseResult(
            ok=False, error="响应中缺少 tag_name", from_cache=from_cache
        )
    return LatestReleaseResult(
        ok=True,
        tag_name=tag,
        html_url=data.get("html_url")
        if isinstance(data.get("html_url"), str)
        else None,
        name=data.get("name") if isinstance(data.get("name"), str) else None,
        body=data.get("body") if isinstance(data.get("body"), str) else None,
        published_at=data.get("published_at")
        if isinstance(data.get("published_at"), str)
        else None,
        from_cache=from_cache,
    )


def clear_github_release_cache() -> None:
    """测试用。"""
    global _cache_body, _cache_expires_monotonic, _cache_etag
    _cache_body = None
    _cache_expires_monotonic = 0.0
    _cache_etag = None


def strip_tag_for_semver(tag_name: str) -> str:
    return _strip_tag_version(tag_name)


def _parse_release_row(data: Any) -> ReleaseListItem | None:
    if not isinstance(data, dict):
        return None
    if data.get("draft") is True:
        return None
    tag = data.get("tag_name")
    if not tag or not isinstance(tag, str):
        return None
    semver = _strip_tag_version(tag)
    if not semver:
        return None
    return ReleaseListItem(
        tag_name=tag,
        semver=semver,
        html_url=data.get("html_url")
        if isinstance(data.get("html_url"), str)
        else None,
        name=data.get("name") if isinstance(data.get("name"), str) else None,
        body=data.get("body") if isinstance(data.get("body"), str) else None,
        published_at=data.get("published_at")
        if isinstance(data.get("published_at"), str)
        else None,
    )


async def fetch_newer_releases_than(
    current_semver: str,
    *,
    max_pages: int = 5,
    per_page: int = 30,
) -> tuple[list[ReleaseListItem], str | None]:
    """
    拉取 GitHub releases 分页列表，返回 semver **严格大于** current_semver 的条目，
    按版本号从新到旧排序（与 GitHub 默认时间排序不同，以 semver 为准）。
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": GITHUB_USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    collected: list[ReleaseListItem] = []
    try:
        async with httpx.AsyncClient(timeout=RELEASES_LIST_TIMEOUT) as client:
            for page in range(1, max_pages + 1):
                r = await client.get(
                    GITHUB_RELEASES_URL,
                    headers=headers,
                    params={"page": page, "per_page": per_page},
                )
                if r.status_code == 403:
                    return (
                        collected,
                        "无法拉取发行列表（可能触发 GitHub API 限流）",
                    )
                if r.status_code != 200:
                    return (
                        collected,
                        f"发行列表请求失败（HTTP {r.status_code}）",
                    )
                try:
                    arr = r.json()
                except ValueError:
                    return collected, "发行列表响应不是合法 JSON"
                if not isinstance(arr, list) or not arr:
                    break
                for row in arr:
                    item = _parse_release_row(row)
                    if item is None:
                        continue
                    try:
                        if is_strictly_newer(item.semver, current_semver):
                            collected.append(item)
                    except Exception:
                        continue
                if len(arr) < per_page:
                    break
    except httpx.TimeoutException:
        return collected, "拉取发行列表超时"
    except httpx.RequestError as e:
        return collected, f"拉取发行列表网络错误: {e}"

    # 同一 tag 去重（保留任意一条）
    by_sem: dict[str, ReleaseListItem] = {}
    for it in collected:
        by_sem[it.semver] = it
    uniq = list(by_sem.values())
    uniq.sort(key=lambda x: version_tuple(x.semver), reverse=True)
    return uniq, None
