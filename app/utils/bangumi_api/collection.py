"""BangumiApi 收藏状态管理（mixin）"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

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
