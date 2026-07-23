"""
SummaryScheduler —— 管理 [summary-N] 配置中的多个 cron 任务。

不继承 BaseScheduler（后者用于单任务调度器），
但遵循相同的 start/stop/cron 解析模式以保持一致。
"""

from __future__ import annotations

import asyncio

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ...core.config import config_manager
from ...core.logging import logger
from .models import SummaryJobConfig
from .service import summary_service


class SummaryScheduler:
    """管理 [summary-N] 配置中多个 cron 任务的 APScheduler 封装。"""

    def __init__(self) -> None:
        self.scheduler: AsyncIOScheduler | None = None
        self._scheduler_config = config_manager.get_scheduler_config()
        self._timezone: str = self._scheduler_config.get("timezone", "Asia/Shanghai")

    async def start(self) -> bool:
        """创建并启动调度器，注册所有启用的任务。"""
        try:
            if self.scheduler and self.scheduler.running:
                self._schedule_all_jobs()
                return True

            jobstores = {"default": MemoryJobStore()}
            executors = {"default": AsyncIOExecutor()}
            job_defaults = {
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 60,
            }

            self.scheduler = AsyncIOScheduler(
                jobstores=jobstores,
                executors=executors,
                job_defaults=job_defaults,
                timezone=self._timezone,
            )
            self.scheduler.start()
            self._schedule_all_jobs()
            logger.info("Summary 调度器启动成功")
            return True
        except Exception as e:
            logger.error(f"启动 Summary 调度器失败: {e}")
            return False

    async def stop(self) -> bool:
        """关闭调度器。"""
        try:
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
                self.scheduler = None
                logger.info("Summary 调度器已停止")
            return True
        except Exception as e:
            logger.error(f"停止 Summary 调度器失败: {e}")
            return False

    def _schedule_all_jobs(self) -> None:
        """从 [summary-N] 配置同步 APScheduler 任务。

        所有任务均为 CronTrigger，配合 replace_existing=True，
        重复调用不会产生重复调度或重置计时问题。
        """
        if not self.scheduler or not self.scheduler.running:
            return

        configs = config_manager.get_summary_configs()
        active_ids: set[str] = set()

        for cfg in configs:
            job_config = SummaryJobConfig.from_config_dict(cfg)
            if not job_config.enabled:
                continue

            job_id = f"summary_{job_config.id}"
            active_ids.add(job_id)

            try:
                trigger = self._parse_cron(job_config.cron)
            except ValueError as e:
                logger.warning(f"Summary job '{job_config.name}' cron 无效，跳过: {e}")
                continue

            self.scheduler.add_job(
                func=self._run_job,
                trigger=trigger,
                id=job_id,
                args=[job_config],
                name=f"Summary: {job_config.name}",
                replace_existing=True,
            )
            logger.info(
                f"Summary job registered: {job_config.name} (cron: {job_config.cron})"
            )

        # 移除配置中已删除或禁用的任务
        all_job_ids = {job.id for job in self.scheduler.get_jobs()}
        for jid in all_job_ids:
            if jid.startswith("summary_") and jid not in active_ids:
                try:
                    self.scheduler.remove_job(jid)
                    logger.info(f"Summary job removed: {jid}")
                except Exception as e:
                    logger.warning(f"移除 Summary 任务 {jid} 失败: {e}")

    def _parse_cron(self, cron_expression: str) -> CronTrigger:
        """解析 5 字段 cron 表达式，无效时抛出 ValueError。"""
        parts = cron_expression.strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"无效的 cron 表达式: '{cron_expression}'（需要 5 个字段）"
            )
        return CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone=self._timezone,
        )

    async def _run_job(self, job_config: SummaryJobConfig) -> None:
        """执行单个摘要任务，带超时保护。"""
        timeout = self._scheduler_config.get("job_timeout", 300)
        try:
            await asyncio.wait_for(
                summary_service.execute_job(job_config),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error(f"Summary job '{job_config.name}' timed out ({timeout}s)")
        except Exception as e:
            logger.error(f"Summary job '{job_config.name}' failed: {e}")

    def reload_job_if_running(self) -> None:
        """配置变更后刷新任务（由 Web UI 保存流程调用）。"""
        if self.scheduler and self.scheduler.running:
            self._schedule_all_jobs()

    async def apply_config_after_save(self) -> None:
        """config.ini 保存后同步调度器状态。"""
        if self.scheduler and self.scheduler.running:
            self._schedule_all_jobs()
        else:
            configs = config_manager.get_summary_configs()
            if any(SummaryJobConfig.from_config_dict(c).enabled for c in configs):
                await self.start()


# 全局单例
summary_scheduler = SummaryScheduler()
