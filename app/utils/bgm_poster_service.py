"""Bangumi 条目封面 URL 批量解析（共享 API 客户端与进程级缓存）。"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.bangumi_api import BangumiApi
from .bgm_image_url import (
    build_poster_cache_namespace,
    extract_poster_url,
    rewrite_bgm_image_url,
    timeline_poster_size_order,
)

_POSTER_URL_TTL_SECONDS = 24 * 60 * 60

_bgm_api_instances: dict[tuple[str, bool, str], BangumiApi] = {}
_poster_url_cache: dict[tuple[str, int], tuple[str, float]] = {}


def _dev_config(key: str, fallback: Any = "") -> Any:
    return config_manager.get("dev", key, fallback=fallback)


def _bangumi_api_config_key() -> tuple[str, bool, str]:
    return (
        str(_dev_config("script_proxy", "") or ""),
        bool(_dev_config("ssl_verify", True)),
        str(_dev_config("bgm_api_proxy", "") or ""),
    )


def _poster_cache_namespace() -> str:
    return build_poster_cache_namespace(
        str(_dev_config("bgm_api_proxy", "") or ""),
        str(_dev_config("bgm_image_proxy", "") or "").strip(),
    )


def get_shared_bangumi_api() -> BangumiApi:
    """按 dev 代理配置复用 BangumiApi 实例，使 get_subject LRU 跨请求命中。"""
    key = _bangumi_api_config_key()
    api = _bgm_api_instances.get(key)
    if api is None:
        http_proxy, ssl_verify, bgm_api_proxy = key
        api = BangumiApi(
            http_proxy=http_proxy,
            ssl_verify=ssl_verify,
            bgm_api_proxy=bgm_api_proxy,
        )
        _bgm_api_instances[key] = api
    return api


def clear_poster_service_caches() -> None:
    """清空进程级 poster URL 缓存（主要用于测试）。"""
    _poster_url_cache.clear()
    _bgm_api_instances.clear()


def normalize_subject_id(value: Any) -> int | None:
    """将数据库/请求中的 subject_id 规范为 int；无效则返回 None。"""
    if value is None or value == "":
        return None
    try:
        sid = int(value)
    except (TypeError, ValueError):
        return None
    return sid if sid >= 1 else None


def _get_cached_poster_url(subject_id: int, namespace: str) -> str | None:
    entry = _poster_url_cache.get((namespace, subject_id))
    if not entry:
        return None
    url, expires_at = entry
    if time.monotonic() >= expires_at:
        _poster_url_cache.pop((namespace, subject_id), None)
        return None
    return url


def _set_cached_poster_url(subject_id: int, namespace: str, url: str) -> None:
    _poster_url_cache[(namespace, subject_id)] = (
        url,
        time.monotonic() + _POSTER_URL_TTL_SECONDS,
    )


def _resolve_poster_url_sync(
    subject_id: int,
    prefer_sizes: tuple[str, ...] | None = None,
) -> str | None:
    namespace = _poster_cache_namespace()
    cached = _get_cached_poster_url(subject_id, namespace)
    if cached:
        return cached

    bgm = get_shared_bangumi_api()
    try:
        subject = bgm.get_subject(subject_id)
    except Exception as e:
        logger.warning("获取条目 %s 封面失败: %s", subject_id, e)
        return None

    if not subject or not subject.get("id"):
        return None

    raw_url = extract_poster_url(subject, prefer_sizes=prefer_sizes)
    if not raw_url:
        return None

    image_proxy = str(_dev_config("bgm_image_proxy", "") or "").strip()
    poster_url = rewrite_bgm_image_url(raw_url, image_proxy)
    _set_cached_poster_url(subject_id, namespace, poster_url)
    return poster_url


def get_poster_urls_sync(
    subject_ids: list[Any],
    prefer_sizes: tuple[str, ...] | None = None,
) -> dict[int, str]:
    """同步批量解析封面 URL；失败条目跳过；未缓存条目并行请求。"""
    sizes = prefer_sizes if prefer_sizes is not None else timeline_poster_size_order()
    result: dict[int, str] = {}
    seen: set[int] = set()
    to_fetch: list[int] = []
    namespace = _poster_cache_namespace()

    for raw_id in subject_ids:
        subject_id = normalize_subject_id(raw_id)
        if subject_id is None or subject_id in seen:
            continue
        seen.add(subject_id)
        cached = _get_cached_poster_url(subject_id, namespace)
        if cached:
            result[subject_id] = cached
        else:
            to_fetch.append(subject_id)

    if not to_fetch:
        return result

    max_workers = min(8, len(to_fetch))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(_resolve_poster_url_sync, sid, sizes): sid
            for sid in to_fetch
        }
        for future in as_completed(future_to_id):
            subject_id = future_to_id[future]
            try:
                url = future.result()
                if url:
                    result[subject_id] = url
            except Exception as e:
                logger.warning("获取条目 %s 封面失败: %s", subject_id, e)

    return result


async def get_poster_urls(
    subject_ids: list[Any],
    prefer_sizes: tuple[str, ...] | None = None,
) -> dict[int, str]:
    """异步批量解析封面 URL（Bangumi API 调用在线程池中执行）。"""
    return await asyncio.to_thread(get_poster_urls_sync, subject_ids, prefer_sizes)
