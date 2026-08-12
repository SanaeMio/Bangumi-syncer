"""Bangumi 条目封面 URL 批量解析（共享 API 客户端与进程级缓存）。"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.bangumi_api import BangumiApi
from ..utils.bangumi_constants import COLLECTION_TYPE_DOING
from .bgm_image_url import (
    build_poster_cache_namespace,
    extract_poster_url,
    rewrite_bgm_image_url,
    timeline_poster_size_order,
)

_POSTER_URL_TTL_SECONDS = 24 * 60 * 60
# 在看列表预取结果短缓存：避免并发请求（时间线+放送日历）各自重复拉分页
_WATCHING_TTL_SECONDS = 60
# 预取分页上限：控制在看拉取的最坏耗时（limit=50 时最多 3 页 150 条）
_WATCHING_MAX_PAGES = 3

_bgm_api_instances: dict[tuple[str, bool, str, str], BangumiApi] = {}
_poster_url_cache: dict[tuple[str, int], tuple[str, float]] = {}
_watching_lock = threading.Lock()
_watching_map_cache: dict[tuple, tuple[dict[int, str], float]] = {}


def _dev_config(key: str, fallback: Any = "") -> Any:
    return config_manager.get("dev", key, fallback=fallback)


def _bangumi_api_config_key(
    dev_snapshot: dict[str, Any] | None = None,
) -> tuple[str, bool, str, str]:
    if dev_snapshot is None:
        dev_snapshot = config_manager.get_dev_http_snapshot()
    return (
        str(dev_snapshot["script_proxy"] or ""),
        bool(dev_snapshot["ssl_verify"]),
        str(dev_snapshot["bgm_api_proxy"] or ""),
        str(dev_snapshot["ech_mode"] or ""),
    )


def _poster_cache_namespace() -> str:
    return build_poster_cache_namespace(
        str(_dev_config("bgm_api_proxy", "") or ""),
        str(_dev_config("bgm_image_proxy", "") or "").strip(),
    )


def get_shared_bangumi_api() -> BangumiApi:
    """按 dev 代理配置复用 BangumiApi 实例，使 get_subject LRU 跨请求命中。"""
    dev_snapshot = config_manager.get_dev_http_snapshot()
    key = _bangumi_api_config_key(dev_snapshot)
    api = _bgm_api_instances.get(key)
    if api is None:
        http_proxy, ssl_verify, bgm_api_proxy, ech_mode = key
        api = BangumiApi(
            http_proxy=http_proxy,
            ssl_verify=ssl_verify,
            bgm_api_proxy=bgm_api_proxy,
            ech_mode=ech_mode,
        )
        _bgm_api_instances[key] = api
    return api


def clear_poster_service_caches() -> None:
    """清空进程级 poster URL 缓存（主要用于测试）。"""
    _poster_url_cache.clear()
    _bgm_api_instances.clear()
    _watching_map_cache.clear()


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


def _apply_image_proxy(raw_url: str) -> str:
    """按 [dev] bgm_image_proxy 改写 lain.bgm.tv 图片地址。"""
    image_proxy = str(_dev_config("bgm_image_proxy", "") or "").strip()
    return rewrite_bgm_image_url(raw_url, image_proxy)


def _build_watching_poster_map(
    prefer_sizes: tuple[str, ...] | None = None,
) -> dict[int, str]:
    """从当前激活账号的「在看」列表批量预取封面地址（至多 3 页，60s 内单飞）。

    封面 URL 无法从条目 ID 推导（hash 随机），逐个拉取 subject 成本高；
    在看列表接口每条记录自带 images，可一次性覆盖时间线的大多数条目。
    未命中（历史记录/已弃番）的条目由调用方回退逐 ID 拉取。

    Returns:
        {subject_id: 封面 URL}；无激活账号、无令牌、API 不可达或请求失败时
        返回空 dict（调用方回退到逐 ID 拉取，不影响封面可用性）。
    """
    from app.core import accounts as _accounts

    cfg = _accounts.get_active_bangumi_config()
    if not cfg or not cfg.get("username") or not cfg.get("access_token"):
        return {}

    cache_key = _watching_cache_key(cfg, prefer_sizes)
    now = time.monotonic()
    with _watching_lock:
        cached = _watching_map_cache.get(cache_key)
        if cached and now - cached[1] < _WATCHING_TTL_SECONDS:
            return cached[0]

    # 共享实例记录 API 不可达（TTL 内）时跳过预取，避免新建实例白跑分页
    shared = get_shared_bangumi_api()
    if shared.is_api_unreachable():
        logger.debug("Bangumi API 不可达（TTL 内），跳过在看列表封面预取")
        return {}

    dev_snapshot = config_manager.get_dev_http_snapshot()
    api = BangumiApi(
        username=cfg["username"],
        access_token=cfg["access_token"],
        private=cfg.get("private", False),
        http_proxy=dev_snapshot["script_proxy"],
        ssl_verify=dev_snapshot["ssl_verify"],
        bgm_api_proxy=dev_snapshot["bgm_api_proxy"],
        bgm_next_proxy=dev_snapshot["bgm_next_proxy"],
        ech_mode=dev_snapshot["ech_mode"],
    )
    try:
        items = api.list_user_collections(
            collection_type=COLLECTION_TYPE_DOING,
            limit=50,
            max_total=500,
            max_pages=_WATCHING_MAX_PAGES,
        )
    except Exception as e:
        logger.warning(f"从在看列表预取封面失败，回退逐 ID 拉取: {e}")
        return {}
    finally:
        api.close()

    result: dict[int, str] = {}
    for item in items:
        subject = item.get("subject") if isinstance(item, dict) else None
        if not isinstance(subject, dict):
            continue
        sid = normalize_subject_id(subject.get("id"))
        if sid is None:
            continue
        raw_url = extract_poster_url(subject, prefer_sizes=prefer_sizes)
        if raw_url:
            result[sid] = _apply_image_proxy(raw_url)

    with _watching_lock:
        _watching_map_cache[cache_key] = (result, time.monotonic())
    return result


def _watching_cache_key(
    cfg: dict[str, Any], prefer_sizes: tuple[str, ...] | None
) -> tuple:
    """在看预取缓存键：代理/改写配置 + 激活账号 + 尺寸偏好。"""
    snapshot = config_manager.get_dev_http_snapshot()
    return (
        snapshot["script_proxy"],
        snapshot["ssl_verify"],
        snapshot["bgm_api_proxy"],
        snapshot["bgm_next_proxy"],
        snapshot["ech_mode"],
        _poster_cache_namespace(),
        cfg.get("username"),
        prefer_sizes,
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
        # 封面图需要 API 的 images 字段，Archive 数据不含，须绕过 Archive 短路
        subject = bgm.get_subject(subject_id, use_archive=False)
    except Exception as e:
        logger.warning("获取条目 %s 封面失败: %s", subject_id, e)
        return None

    if not subject or not subject.get("id"):
        return None

    raw_url = extract_poster_url(subject, prefer_sizes=prefer_sizes)
    if not raw_url:
        return None

    poster_url = _apply_image_proxy(raw_url)
    _set_cached_poster_url(subject_id, namespace, poster_url)
    return poster_url


def get_poster_urls_sync(
    subject_ids: list[Any],
    prefer_sizes: tuple[str, ...] | None = None,
) -> dict[int, str]:
    """同步批量解析封面 URL；失败条目跳过；未缓存条目并行请求。

    优化：未缓存条目先尝试从当前激活账号的「在看」列表批量提取
    （1 个请求覆盖时间线大多数条目），未命中的少量条目再逐个拉取兜底。
    """
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

    # 在看列表批量预取（仅当存在未缓存条目时发起，命中后同样写入 24h 缓存）
    watching_map = _build_watching_poster_map(sizes)
    for subject_id in list(to_fetch):
        url = watching_map.get(subject_id)
        if url:
            result[subject_id] = url
            _set_cached_poster_url(subject_id, namespace, url)
            to_fetch.remove(subject_id)

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


# [dev] 代理相关配置变更时清空进程级缓存：命名空间虽然已随配置变化，但
# 旧条目带 24h TTL 仍会命中，配置变更后应整体失效（无需重启）
config_manager.register_config_change_listener(clear_poster_service_caches)
