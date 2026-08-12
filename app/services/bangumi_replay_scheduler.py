"""待同步队列补发调度器

继承 BaseScheduler，按 [bangumi-replay] replay_cron 定时触发补发。
默认 cron: "*/10 * * * *"（每 10 分钟）。

流程：
1. [bangumi-replay] enabled=false 时不启动（默认 true，与 archive 解耦）
2. 探测 API 可达性：轻量调用 GET /v0/subjects/1，失败则等下一轮
3. 探测成功 → 调用 sync_service.replay_pending_batch 批量补发
4. 仍然不可达则立即跳出，避免浪费请求
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core.config import config_manager
from ..core.logging import logger
from .base.notifier_helpers import (
    notify_batch_sync_summary,
    notify_queue_size_warning,
    notify_scheduler_failure,
)
from .base.scheduler import BaseScheduler


class BangumiReplayScheduler(BaseScheduler):
    """待同步队列补发调度器（APScheduler 周期任务）"""

    JOB_ID = "bangumi_replay_pending"
    DEFAULT_CRON = "*/10 * * * *"  # 每 10 分钟
    DRIVER_NAME = "BangumiReplay"

    def _is_enabled(self) -> bool:
        """replay 启用条件：[bangumi-replay] enabled 非 false（默认 true）

        与 archive 解耦：archive 关闭时 replay 仍可独立工作。
        但「无网环境下匹配新条目并缓存待补发」完整流程仍需 archive 配合。
        """
        return bool(config_manager.get("bangumi-replay", "enabled", fallback=True))

    def _get_driver_config(self) -> dict:
        """返回含 sync_interval 的配置（sync_interval 字段名复用为 cron）"""
        return {
            "sync_interval": config_manager.get(
                "bangumi-replay", "replay_cron", fallback=self.DEFAULT_CRON
            )
        }

    async def _run_sync_job(self) -> None:
        """单轮补发：队列空直接跳过；非空则探测 → 批量补发"""
        if not self._is_enabled():
            return

        # 队列为空时直接跳过，避免无意义的 API 探测请求
        from ..core.database import database_manager

        try:
            pending_count = database_manager.count_pending_sync()
        except Exception as e:
            logger.debug(f"📚 统计待同步队列失败: {e}")
            pending_count = 0

        if pending_count <= 0:
            logger.debug("📚 待同步队列为空，本轮跳过")
            return

        # 队列堆积预警（默认阈值 100）
        queue_threshold = int(
            config_manager.get("bangumi-replay", "queue_size_threshold", fallback=100)
        )
        if queue_threshold > 0 and pending_count >= queue_threshold:
            logger.warning(
                f"📚 待同步队列堆积 {pending_count} 条，已超过阈值 {queue_threshold}"
            )
            notify_queue_size_warning(pending_count, queue_threshold)

        # 探测 API 可达性
        if not await self._probe_api():
            logger.debug("📚 Bangumi API 仍不可达，本轮补发跳过")
            return

        # API 已恢复：批量补发
        try:
            from .sync_service import sync_service

            batch_size = int(
                config_manager.get("bangumi-replay", "replay_batch_size", fallback=20)
            )
            timeout = self._scheduler_config.get("job_timeout", 300)
            stats = await asyncio.wait_for(
                asyncio.to_thread(sync_service.replay_pending_batch, batch_size),
                timeout=timeout,
            )
            if stats["total"] > 0:
                logger.info(
                    f"📚 待同步队列补发完成: 总计 {stats['total']}，"
                    f"成功 {stats['success']}，失败 {stats['failed']}，"
                    f"仍不可达 {stats['still_unreachable']}"
                )
                notify_batch_sync_summary(
                    "bangumi-replay",
                    total=stats["total"],
                    succeeded=stats["success"],
                    failed=stats["failed"],
                    skipped=stats["still_unreachable"],
                )
        except asyncio.TimeoutError:
            logger.error("📚 待同步队列补发超时")
            notify_scheduler_failure(
                "bangumi-replay", "待同步队列补发超时", timeout=True
            )
        except Exception as e:
            logger.error(f"📚 待同步队列补发异常: {e}")
            notify_scheduler_failure("bangumi-replay", str(e))

    def trigger_immediate_run(self) -> None:
        """队列有新条目入队时立即触发一次补发（异步、防抖）

        - 调度器未启动 / 未启用 → 直接 return，依赖下一轮 cron 兜底
        - 已有 job 在运行 → APScheduler 的 max_instances=1 会自动合并，
          本方法仅负责"提前唤醒"，不强制并发
        - 用 flag 做毫秒级防抖，避免高频入队时堆积 trigger 调用
        """
        if not self._is_enabled():
            return
        if not self.scheduler or not self.scheduler.running:
            return
        # 防抖：500ms 内的多次入队只触发一次立即执行
        # 用 time.monotonic() 而非 asyncio.get_event_loop().time()，
        # 因为本方法可能被 sync_service 同步流程从 ThreadPoolExecutor 工作线程
        # 调用（无 running loop），后者会抛 RuntimeError 或静默失效。
        now = time.monotonic()
        last = getattr(self, "_last_trigger_ts", 0.0)
        if now - last < 0.5:
            return
        self._last_trigger_ts = now

        try:
            self.scheduler.add_job(
                func=self._run_sync_job,
                trigger="date",
                run_date=None,  # 立即执行
                id=f"{self.JOB_ID}_immediate",
                name=f"{self.DRIVER_NAME} immediate",
                replace_existing=True,
            )
            logger.debug("📚 队列有新条目，已触发立即补发")
        except Exception as e:
            logger.debug(f"📚 触发立即补发失败: {e}")

    async def _probe_api(self) -> bool:
        """轻量探测 Bangumi API 是否恢复可达

        创建临时 BangumiApi 实例发请求；若无可用账号配置则跳过本轮。
        """
        try:
            # DB 为唯一真相源：取激活账号配置（与 sync_service._get_bangumi_config_for_user 一致）
            from app.core.accounts import get_active_bangumi_config

            from ..utils.bangumi_api import BangumiApi

            cfg = get_active_bangumi_config()
            if not cfg or not cfg.get("username") or not cfg.get("access_token"):
                logger.debug("📚 无可用账号配置用于探测 API")
                return False

            dev_snapshot = config_manager.get_dev_http_snapshot()
            probe_api = BangumiApi(
                username=cfg["username"],
                access_token=cfg["access_token"],
                private=cfg.get("private", False),
                http_proxy=dev_snapshot["script_proxy"],
                ssl_verify=dev_snapshot["ssl_verify"],
                bgm_api_proxy=dev_snapshot["bgm_api_proxy"],
                bgm_next_proxy=dev_snapshot["bgm_next_proxy"],
                ech_mode=dev_snapshot["ech_mode"],
            )
            # 清除不可达标记以强制探测
            probe_api.mark_api_reachable()

            try:
                # GET /v0/subjects/1：系统条目，几乎必然存在
                res = probe_api.get("subjects/1")
                # 2xx 视为可达；404 也算可达（说明 API 通了，只是 1 不存在）
                reachable = 200 <= res.status_code < 500
                if reachable:
                    # 探测成功 → 统一复位 sync_service 缓存实例的不可达标记：
                    # 一次探测通过即恢复全部用户实例，避免断网恢复后各实例
                    # 各自等 TTL 到期再逐条探测的无用重试堆积
                    try:
                        from .sync_service import sync_service

                        reset_count = sync_service.reset_all_api_unreachable_flags()
                        if reset_count:
                            logger.info(f"📚 探测成功，已统一复位 {reset_count} 个实例")
                    except Exception as reset_err:
                        logger.debug(f"📚 统一复位不可达标记失败: {reset_err}")
                return reachable
            finally:
                probe_api.req.close()
                probe_api._req_not_auth.close()
        except Exception as e:
            logger.debug(f"📚 API 探测失败: {e}")
            return False

    def get_status(self) -> dict[str, Any]:
        """供 API 查询调度器状态"""
        from ..core.database import database_manager

        running = bool(self.scheduler and self.scheduler.running)
        return {
            "enabled": self._is_enabled(),
            "cron": self._get_driver_config().get("sync_interval", self.DEFAULT_CRON),
            "running": running,
            "queue_stats": database_manager.get_pending_sync_stats(),
        }


# 全局单例
bangumi_replay_scheduler = BangumiReplayScheduler()
