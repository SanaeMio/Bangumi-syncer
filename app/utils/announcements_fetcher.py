"""
从 GitHub Raw（或本地/自定义 URL）拉取控制台公告 JSON，带缓存与多源回退。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..core.config import config_manager
from ..core.logging import logger

ANNOUNCEMENTS_RAW_URL = "https://raw.githubusercontent.com/SanaeMio/Bangumi-syncer/main/docs/announcements.json"
_GH_PROXY_MIRRORS = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
)
USER_AGENT = "SanaeMio/Bangumi-syncer (https://github.com/SanaeMio/Bangumi-syncer)"
REQUEST_TIMEOUT = 15.0
CACHE_TTL_SEC = 300.0

_cache_body: list[dict[str, Any]] | None = None
_cache_remote_loaded: bool = False
_cache_expires_monotonic: float = 0.0


@dataclass
class AnnouncementsFetchResult:
    ok: bool
    announcements: list[dict[str, Any]]
    remote_loaded: bool = False
    error: str | None = None
    from_cache: bool = False


def clear_announcements_cache() -> None:
    global _cache_body, _cache_remote_loaded, _cache_expires_monotonic
    _cache_body = None
    _cache_remote_loaded = False
    _cache_expires_monotonic = 0.0


def _cache_ttl_sec() -> float:
    raw = config_manager.get("dev", "announcements_cache_ttl", fallback="").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return CACHE_TTL_SEC


def _get_script_proxy() -> str | None:
    proxy = config_manager.get("dev", "script_proxy", fallback="").strip()
    return proxy or None


def _parse_announcements_payload(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("公告 JSON 根节点须为对象")
    items = data.get("announcements")
    if not isinstance(items, list):
        raise ValueError("缺少 announcements 数组")
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "")
        if not aid or not title:
            continue
        out.append(
            {
                "id": aid,
                "title": title,
                "level": str(item.get("level") or "info").strip() or "info",
                "published_at": str(item.get("published_at") or "").strip(),
                "body": body,
            }
        )
    return out


def _resolve_local_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return config_manager.cwd / path_str


def _load_from_local_file(path_str: str) -> AnnouncementsFetchResult:
    path = _resolve_local_path(path_str)
    if not path.is_file():
        return AnnouncementsFetchResult(
            ok=False,
            announcements=[],
            remote_loaded=False,
            error=f"本地公告文件不存在: {path}",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = _parse_announcements_payload(data)
        return AnnouncementsFetchResult(
            ok=True,
            announcements=items,
            remote_loaded=True,
        )
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"读取本地公告文件失败: {e}")
        return AnnouncementsFetchResult(
            ok=False,
            announcements=[],
            remote_loaded=False,
            error=str(e),
        )


def _build_fetch_urls() -> list[str]:
    custom = config_manager.get("dev", "announcements_url", fallback="").strip()
    if custom:
        return [custom]
    urls: list[str] = []
    for mirror in _GH_PROXY_MIRRORS:
        urls.append(mirror + ANNOUNCEMENTS_RAW_URL)
    urls.append(ANNOUNCEMENTS_RAW_URL)
    return urls


async def _fetch_url(url: str, proxy: str | None) -> httpx.Response:
    kwargs: dict[str, Any] = {
        "timeout": REQUEST_TIMEOUT,
        "headers": {"User-Agent": USER_AGENT},
    }
    if proxy:
        kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**kwargs) as client:
        return await client.get(url)


async def fetch_announcements() -> AnnouncementsFetchResult:
    """
    获取公告列表。优先本地文件，其次 HTTP 多源链。
    成功响应使用内存缓存降低请求频率。
    """
    global _cache_body, _cache_remote_loaded, _cache_expires_monotonic

    local_file = config_manager.get("dev", "announcements_file", fallback="").strip()
    if local_file:
        return _load_from_local_file(local_file)

    now = time.monotonic()
    if _cache_body is not None and now < _cache_expires_monotonic:
        return AnnouncementsFetchResult(
            ok=True,
            announcements=list(_cache_body),
            remote_loaded=_cache_remote_loaded,
            from_cache=True,
        )

    proxy = _get_script_proxy()
    last_error = "无法拉取公告"

    for url in _build_fetch_urls():
        try:
            r = await _fetch_url(url, proxy)
        except httpx.TimeoutException:
            last_error = "请求公告超时"
            logger.warning(f"公告拉取超时: {url}")
            continue
        except httpx.RequestError as e:
            last_error = f"网络错误: {e}"
            logger.warning(f"公告拉取网络错误 {url}: {e}")
            continue

        if r.status_code != 200:
            last_error = f"HTTP {r.status_code}"
            logger.warning(f"公告拉取非 200 {url}: {r.status_code}")
            continue

        try:
            data = r.json()
            items = _parse_announcements_payload(data)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = str(e)
            logger.warning(f"公告 JSON 解析失败: {e}")
            continue

        _cache_body = items
        _cache_remote_loaded = True
        _cache_expires_monotonic = time.monotonic() + _cache_ttl_sec()
        return AnnouncementsFetchResult(
            ok=True,
            announcements=items,
            remote_loaded=True,
        )

    logger.warning(f"公告全部拉取源失败: {last_error}")
    return AnnouncementsFetchResult(
        ok=False,
        announcements=[],
        remote_loaded=False,
        error=last_error,
    )
