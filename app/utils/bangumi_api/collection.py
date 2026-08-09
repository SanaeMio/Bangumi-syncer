"""BangumiApi 收藏状态管理（mixin）"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import httpx

from ...core.config import config_manager
from ...core.logging import logger
from ...utils.bangumi_constants import (
    COLLECTION_TYPE_DONE,
    COLLECTION_TYPE_ON_HOLD,
    COLLECTION_TYPE_WISH,
)


class CollectionMixin:
    """收藏/章节状态相关方法（供 BangumiApi 组合）"""

    def list_user_collections(
        self,
        subject_type: Optional[int] = None,
        collection_type: Optional[int] = None,
        limit: int = 30,
        max_total: int = 500,
    ) -> list[dict[str, Any]]:
        """批量获取用户收藏列表（分页拉满）

        用于"番剧放送日历"获取用户"在看"列表等场景。

        Args:
            subject_type: 条目类型过滤（2=动画, 6=三次元），None=全部
            collection_type: 收藏类型过滤（1=想看 2=看过 3=在看 4=搁置 5=抛弃），
                             None=全部
            limit: 单页大小（Bangumi API 上限 50）
            max_total: 最多拉取总数，防止异常用户收藏过多拖慢请求

        Returns:
            list[dict]，每个 dict 含 subject_id, type, name, name_cn 等字段
            （对齐 Bangumi API /v0/users/{username}/collections 返回的 data 项）
        """
        results: list[dict[str, Any]] = []
        offset = 0
        # 防御性上限：单次调用最多拉 max_total 条，避免异常场景无限拉取
        limit = min(max(1, limit), 50)
        max_total = max(limit, max_total)
        while offset < max_total:
            params: dict[str, Any] = {"limit": limit, "offset": offset}
            if subject_type is not None:
                params["subject_type"] = subject_type
            if collection_type is not None:
                params["type"] = collection_type
            res = self.get(f"users/{self.username}/collections", params=params)
            # self.get 返回 httpx.Response；404 表示用户名不存在
            if res.status_code == 404:
                raise ValueError(
                    f"Bangumi 用户 {self.username} 不存在（404），请检查配置的 username"
                )
            if res.status_code != 200:
                raise RuntimeError(
                    f"获取用户 {self.username} 收藏列表失败: HTTP {res.status_code}"
                )
            body = res.json()
            # 返回结构 {data: [...], total: N}
            data = body.get("data") if isinstance(body, dict) else None
            if not data:
                break
            results.extend(data)
            total = body.get("total", 0) if isinstance(body, dict) else 0
            offset += len(data)
            # 拉满或已到 max_total
            if offset >= total or offset >= max_total:
                break
        return results[:max_total]

    def get_subject_collection(self, subject_id: int) -> dict[str, Any]:
        res = self.get(f"users/{self.username}/collections/{subject_id}")
        if res.status_code == 404:
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(
                    f"get_subject_collection API返回非字典类型: {type(res)}, 内容: {res}"
                )
                res = {}
        except Exception as e:
            logger.error(f"get_subject_collection JSON解析失败: {e}")
            res = {}
        return res

    def get_ep_collection(self, episode_id: int) -> dict[str, Any]:
        res = self.get(f"users/-/collections/-/episodes/{episode_id}")
        if res.status_code == 404:
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(
                    f"get_ep_collection API返回非字典类型: {type(res)}, 内容: {res}"
                )
                res = {}
        except Exception as e:
            logger.error(f"get_ep_collection JSON解析失败: {e}")
            res = {}
        return res

    # ------------------------------------------------------------------
    # ensure_subject_watching 降级策略（与 mark_episode_watched 对称）：
    # - API 不可达时（_api_unreachable=True），不实际发请求，直接抛 _PendingSyncQueued
    #   通知上层（sync_service.sync_movie_watching）走"入队 + 不报错"分支
    # - API 可达但请求失败（连接错误/5xx）时，http_layer 会自动设置不可达标记
    #   并重新抛异常；本方法捕获后入队
    # ------------------------------------------------------------------

    def ensure_subject_watching(self, subject_id: int) -> int:
        """
        仅将条目收藏置为「在看」(COLLECTION_TYPE_DOING)，不修改单集进度。

        Returns:
            0: 无需变更（已在看或已看过）
            1: 已新增收藏为在看，或从想看/搁置改为在看
        """
        # API 不可达：直接抛 _PendingSyncQueued，让上层走"入队"分支
        if self.is_api_unreachable():
            raise _PendingSyncQueued(
                subject_id=subject_id,
                ep_id=None,
                reason="api_unreachable_movie_watching",
            )

        try:
            return self._do_ensure_subject_watching(subject_id)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            # 连接错误：标记不可达并触发入队
            self.mark_api_unreachable()
            raise _PendingSyncQueued(
                subject_id=subject_id,
                ep_id=None,
                reason="connect_error_movie_watching",
                cause=e,
            ) from e
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else 0
            # 5xx/429：服务端不可用，入队等重试
            if status in (429, 500, 502, 503, 504):
                self.mark_api_unreachable()
                raise _PendingSyncQueued(
                    subject_id=subject_id,
                    ep_id=None,
                    reason=f"http_{status}_movie_watching",
                    cause=e,
                ) from e
            # 4xx（除 401 已在 _check_auth_error 处理）：业务错误，不入队，正常抛出
            raise

    def _do_ensure_subject_watching(self, subject_id: int) -> int:
        """实际执行 ensure_subject_watching 的子步骤（原逻辑）"""
        data = self.get_subject_collection(subject_id)
        if not data:
            self.add_collection_subject(subject_id=subject_id, state=3)
            return 1
        if data.get("type") == COLLECTION_TYPE_DONE:
            return 0
        if data.get("type") in (COLLECTION_TYPE_WISH, COLLECTION_TYPE_ON_HOLD):
            self.change_collection_state(subject_id=subject_id, state=3)
            return 1
        return 0

    # ------------------------------------------------------------------
    # mark_episode_watched 降级策略：
    # - API 不可达时（_api_unreachable=True），不实际发请求，直接抛 _PendingSyncQueued
    #   通知上层（sync_service）走"入队 + 不报错"分支
    # - API 可达但请求失败（连接错误/5xx）时，http_layer 会自动设置不可达标记
    #   并重新抛异常；上层捕获后再决定入队
    # ------------------------------------------------------------------

    def mark_episode_watched(self, subject_id: int, ep_id: int) -> int:
        """标记单集为已看

        返回值：
            0: 已在看/看过（无变更）
            1: 已标记为看过
            2: 已新增收藏为在看并标记单集
            -1: API 不可达，已触发入队（上层应捕获 _PendingSyncQueued 走降级）
        """
        # API 不可达：直接抛 _PendingSyncQueued，让上层走"入队"分支
        if self.is_api_unreachable():
            raise _PendingSyncQueued(
                subject_id=subject_id,
                ep_id=ep_id,
                reason="api_unreachable",
            )

        try:
            return self._do_mark_episode_watched(subject_id, ep_id)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            # 连接错误：标记不可达并触发入队
            self.mark_api_unreachable()
            raise _PendingSyncQueued(
                subject_id=subject_id, ep_id=ep_id, reason="connect_error", cause=e
            ) from e
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else 0
            # 5xx/429：服务端不可用，入队等重试
            if status in (429, 500, 502, 503, 504):
                self.mark_api_unreachable()
                raise _PendingSyncQueued(
                    subject_id=subject_id,
                    ep_id=ep_id,
                    reason=f"http_{status}",
                    cause=e,
                ) from e
            # 4xx（除 401 已在 _check_auth_error 处理）：业务错误，不入队，正常抛出
            raise

    def _do_mark_episode_watched(self, subject_id: int, ep_id: int) -> int:
        """实际执行 mark_episode_watched 的子步骤（原逻辑）"""
        data = self.get_subject_collection(subject_id)

        # 如果未收藏，则先标记为在看，再点单集格子
        if not data:
            self.add_collection_subject(subject_id=subject_id)
            self.change_episode_state(ep_id=ep_id, state=2)
            return 2
        else:
            # 如果整部番已看过则跳过
            if data.get("type") == COLLECTION_TYPE_DONE:
                return 0
            #  如果条目状态是想看或搁置则调整为在看
            if (
                data.get("type") == COLLECTION_TYPE_WISH
                or data.get("type") == COLLECTION_TYPE_ON_HOLD
            ):
                self.change_collection_state(subject_id=subject_id, state=3)

        ep_data = self.get_ep_collection(ep_id)
        logger.debug(ep_data)
        # 如果单集已看过则跳过
        if ep_data.get("type") == COLLECTION_TYPE_DONE:
            return 0
        else:
            # 否则直接点单集格子
            self.change_episode_state(ep_id=ep_id, state=2)
            return 1

    def add_collection_subject(
        self, subject_id: int, private: bool | None = None, state: int = 3
    ) -> None:
        private = self.private if private is None else private
        self.post(
            f"users/-/collections/{subject_id}",
            _json={"type": state, "private": bool(private)},
        )

    def change_collection_state(
        self, subject_id: int, private: bool | None = None, state: int = 3
    ) -> None:
        private = self.private if private is None else private
        self.post(
            f"users/-/collections/{subject_id}",
            _json={"type": state, "private": bool(private)},
        )

    def change_episode_state(self, ep_id: int, state: int = 2) -> None:
        res = self.put(f"users/-/collections/-/episodes/{ep_id}", _json={"type": state})
        if 333 < res.status_code < 444:
            raise ValueError(f"{res.status_code=} {res.text}")
        return res


class _PendingSyncQueued(Exception):
    """写操作因 API 不可达被降级到待同步队列时抛出

    上层（sync_service._retry_mark_episode / sync_movie_watching）捕获后调用
    pending_sync_queue.enqueue 持久化任务，避免同步流程整体报错。

    Attributes:
        subject_id: 待标记的 Bangumi 条目 ID
        ep_id: 待标记的章节 ID；剧场版场景为 None（仅标记条目收藏，不点单集）
        reason: 触发降级的原因（api_unreachable / connect_error / http_503 等）
        cause: 原始异常（如有）
    """

    def __init__(
        self,
        subject_id: int,
        ep_id: Optional[int] = None,
        reason: str = "api_unreachable",
        cause: Optional[BaseException] = None,
    ) -> None:
        self.subject_id = subject_id
        self.ep_id = ep_id
        self.reason = reason
        self.cause = cause
        super().__init__(
            f"Bangumi API 不可达，已触发入队: subject={subject_id} ep={ep_id} reason={reason}"
        )


def is_replay_enabled() -> bool:
    """检查 replay（待同步队列补发）是否启用

    与 archive 解耦：直接读 [bangumi-replay] enabled（默认 true）。
    archive 是否启用不影响本开关；但完全实现「无网缓存请求 + 自动补发」
    仍需 archive 配合（archive 提供读降级，replay 提供写降级）。

    供 sync_service 判断是否走"入队"分支；未启用时直接抛错让原有重试逻辑生效。
    """
    return bool(config_manager.get("bangumi-replay", "enabled", fallback=True))


# ===== 用户"在看"列表缓存（供放送日历视图使用） =====
# 模块级 TTL 缓存：username -> (timestamp, subject_ids_set)
# 缓存命中避免每次访问日历都拉一遍收藏列表（Bangumi API 限流友好）
# 注意：get_watching_subject_ids 可能在 asyncio.to_thread 线程池中并发调用，
# 缓存读写需持锁，避免 dict 在 clear/get/pop 之间出现竞态。
_watching_cache: dict[str, tuple[float, set[int]]] = {}
_watching_cache_lock = threading.Lock()
_WATCHING_CACHE_TTL = 3600  # 1 小时


def get_watching_subject_ids(api: Any) -> set[int]:
    """获取用户"在看"番剧的 subject_id 集合（带 1 小时 TTL 缓存）

    同时拉取动画(2)和三次元(6)两类"在看"收藏，合并返回。
    - 缓存命中：直接返回缓存值
    - API 成功：写缓存并返回（可能为空集合，表示用户确实没在看）
    - API 失败且有缓存：返回缓存值并记录警告
    - API 失败且无缓存：抛异常，让调用方决定降级策略

    Args:
        api: BangumiApi 实例（需已配置 username）

    Returns:
        set[int]：在看条目的 subject_id 集合（可能为空）

    Raises:
        Exception: API 调用失败且无可用缓存时抛出
    """
    username = api.username
    if not username:
        raise ValueError("BangumiApi 未配置 username")

    now = time.time()
    with _watching_cache_lock:
        cached = _watching_cache.get(username)
        if cached and (now - cached[0]) < _WATCHING_CACHE_TTL:
            logger.debug(f"在看列表命中缓存: username={username}, {len(cached[1])} 部")
            return cached[1]

    try:
        # 动画(2) + 三次元(6) 的"在看"(type=3)
        # API 调用在锁外执行，避免长时间持锁阻塞其他线程的缓存读
        # max_total=2000：放送日历"我的追番"视图需要完整在看列表，
        # 默认 500 在重度用户场景会漏条目，提升到 2000 覆盖绝大多数用户
        # （动画/三次元各 2000 上限，合计 4000 部在看）
        anime_watching = api.list_user_collections(
            subject_type=2, collection_type=3, max_total=2000
        )
        real_watching = api.list_user_collections(
            subject_type=6, collection_type=3, max_total=2000
        )
        ids = {
            item.get("subject_id")
            for item in (anime_watching + real_watching)
            if item.get("subject_id")
        }
        # 写缓存
        with _watching_cache_lock:
            _watching_cache[username] = (now, ids)
        logger.debug(
            f"获取在看列表成功: username={username}, 动画 {len(anime_watching)} + 三次元 {len(real_watching)} = {len(ids)} 部"
        )
        return ids
    except Exception as e:
        logger.warning(f"获取用户在看列表失败: username={username}, error={e}")
        with _watching_cache_lock:
            cached = _watching_cache.get(username)
        if cached:
            logger.debug(f"使用缓存降级: username={username}, {len(cached[1])} 部")
            return cached[1]
        # 无缓存时抛出，让调用方降级（如改为全部放送）
        raise


def invalidate_watching_cache(username: Optional[str] = None) -> None:
    """失效在看列表缓存

    Args:
        username: 指定用户失效；None 则清空全部缓存
    """
    with _watching_cache_lock:
        if username is None:
            _watching_cache.clear()
        else:
            _watching_cache.pop(username, None)


# 配置变更（账号/令牌等）时清空在看缓存，避免旧账号数据残留：
# 与 BangumiApi 实例缓存按 dev 快照自动失效的思路统一，无需重启生效
config_manager.register_config_change_listener(lambda: invalidate_watching_cache())
