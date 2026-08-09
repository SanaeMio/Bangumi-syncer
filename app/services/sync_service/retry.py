"""
标记重试 Mixin：同步/异步标记剧集为已看，带指数退避重试。
"""

import asyncio
import time
from typing import Any, Optional

from ...core.logging import logger
from ...utils.bangumi_api import BangumiApi
from ...utils.bangumi_api.collection import _PendingSyncQueued, is_replay_enabled

# 标记方法返回值约定：
#   0: 已在看/看过（无变更）
#   1: 已标记为看过
#   2: 已新增收藏为在看并标记单集
#  -1: API 不可达，已入待同步队列（replay 模式专用，不视为错误）
MARK_QUEUED = -1


class RetryMixin:
    """标记剧集重试逻辑（同步 + 异步）。"""

    # 实例属性类型声明（实际在 __init__.py 的 SyncService.__init__ 中初始化）
    _executor: Any  # ThreadPoolExecutor，供异步版本使用

    def _retry_mark_episode(
        self,
        bgm_api: BangumiApi,
        subject_id: str,
        ep_id: str,
        max_retries: int = 3,
        *,
        queue_payload: Optional[dict] = None,
        sync_record_id: Optional[int] = None,
    ) -> int:
        """带重试机制的标记剧集方法（优化版，减少阻塞时间）

        Args:
            queue_payload: 入队时携带的完整 payload（CustomItem 序列化），
                供补发时重新走完整同步流程。未传则只存 subject_id+ep_id。
            sync_record_id: 关联的 sync_records 行 id，补发成功/放弃时回写状态用。

        当 API 不可达且 [bangumi-replay] enabled=true 时，
        捕获 _PendingSyncQueued 后入队，返回 MARK_QUEUED(-1)。
        上层据此跳过失败分支，把同步记录标记为 queued 而非 error。
        """
        for attempt in range(max_retries + 1):
            try:
                mark_status = bgm_api.mark_episode_watched(
                    subject_id=subject_id, ep_id=ep_id
                )
                if attempt > 0:
                    logger.debug(f"重试成功，第 {attempt + 1} 次尝试标记成功")
                return mark_status
            except _PendingSyncQueued as e:
                # API 不可达：入队并返回 MARK_QUEUED，不再重试
                if is_replay_enabled() and queue_payload is not None:
                    logger.warning(
                        f"📚 API 不可达，已入待同步队列: subject={e.subject_id} "
                        f"ep={e.ep_id} reason={e.reason}"
                    )
                    self._enqueue_pending_sync(
                        bgm_api=bgm_api,
                        subject_id=e.subject_id,
                        ep_id=e.ep_id,
                        reason=e.reason,
                        last_error=str(e.cause) if e.cause else e.reason,
                        payload=queue_payload,
                        sync_record_id=sync_record_id,
                    )
                    return MARK_QUEUED
                # 未启用补发：按原行为抛错
                raise
            except Exception as e:
                if attempt < max_retries:
                    # 优化延迟策略：减少最大延迟时间
                    delay = min(2**attempt, 3)  # 最大延迟3秒: 1, 2, 3秒
                    logger.error(
                        f"标记剧集失败: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )

                    # 使用非阻塞方式等待（在线程池中执行时不会阻塞主线程）
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"标记剧集失败，已达到最大重试次数 {max_retries}: {str(e)}"
                    )
                    raise e
        # This line should never be reached due to the loop logic
        return 0  # pragma: no cover

    async def _retry_mark_episode_async(
        self,
        bgm_api: BangumiApi,
        subject_id: str,
        ep_id: str,
        max_retries: int = 3,
        *,
        queue_payload: Optional[dict] = None,
        sync_record_id: Optional[int] = None,
    ) -> int:
        """异步版本的重试标记剧集方法"""
        for attempt in range(max_retries + 1):
            try:
                # 在线程池中执行同步操作
                loop = asyncio.get_running_loop()
                mark_status = await loop.run_in_executor(
                    self._executor, bgm_api.mark_episode_watched, subject_id, ep_id
                )
                if attempt > 0:
                    logger.debug(f"异步重试成功，第 {attempt + 1} 次尝试标记成功")
                return mark_status
            except _PendingSyncQueued as e:
                if is_replay_enabled() and queue_payload is not None:
                    logger.warning(
                        f"📚 API 不可达，已入待同步队列: subject={e.subject_id} "
                        f"ep={e.ep_id} reason={e.reason}"
                    )
                    self._enqueue_pending_sync(
                        bgm_api=bgm_api,
                        subject_id=e.subject_id,
                        ep_id=e.ep_id,
                        reason=e.reason,
                        last_error=str(e.cause) if e.cause else e.reason,
                        payload=queue_payload,
                        sync_record_id=sync_record_id,
                    )
                    return MARK_QUEUED
                raise
            except Exception as e:
                if attempt < max_retries:
                    delay = min(2**attempt, 3)  # 最大延迟3秒
                    logger.error(
                        f"异步标记剧集失败: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )

                    # 使用异步等待，不阻塞事件循环
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"异步标记剧集失败，已达到最大重试次数 {max_retries}: {str(e)}"
                    )
                    raise e
        # This line should never be reached due to the loop logic
        return 0  # pragma: no cover

    # ------------------------------------------------------------------
    # 待同步队列入队辅助（延迟 import 避免循环依赖）
    # ------------------------------------------------------------------

    @staticmethod
    def _enqueue_pending_sync(
        bgm_api: BangumiApi,
        subject_id: Any,
        ep_id: Any,
        reason: str,
        last_error: str,
        payload: dict,
        sync_record_id: Optional[int] = None,
    ) -> None:
        """把一条标记任务写入 pending_sync_queue 表"""
        from ...core.database import database_manager

        # user_name 必须用媒体库用户名（payload 里的），与 _get_bangumi_api_for_user
        # 和 WebUI 用户过滤一致；bgm_api.username 是 Bangumi 账号名，多用户模式下
        # 会与 [bangumi-*] 映射 key 不匹配，导致补发找不到配置、队列对用户不可见
        user_name = str(payload.get("user_name", "") or "")
        title = str(payload.get("title", ""))
        season = int(payload.get("season", 1) or 1)
        episode = int(payload.get("episode", 0) or 0)
        source = str(payload.get("source", "") or "")
        media_type = str(payload.get("media_type", "episode") or "episode")

        database_manager.enqueue_pending_sync(
            user_name=user_name,
            title=title,
            season=season,
            episode=episode,
            subject_id=str(subject_id),
            episode_id=str(ep_id) if ep_id else None,
            source=source,
            media_type=media_type,
            payload=payload,
            reason=reason,
            last_error=last_error,
            sync_record_id=sync_record_id,
        )

        # 入队后立即触发补发（健康度检查 + 同步）。
        # 调度器未启动 / 队列未启用时 trigger_immediate_run 内部会自动跳过，
        # 失败也无所谓——下一轮 cron 会兜底。
        try:
            from ..bangumi_replay_scheduler import bangumi_replay_scheduler

            bangumi_replay_scheduler.trigger_immediate_run()
        except Exception:
            # 触发失败不影响入队本身，cron 会兜底
            pass
