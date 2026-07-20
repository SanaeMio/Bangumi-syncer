"""SummaryScheduler — manages multiple cron jobs from [summary-N] configs.

Does NOT extend BaseScheduler (which is for single-job schedulers).
Follows the same start/stop/cron-parsing patterns for consistency.
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
    """APScheduler wrapper managing multiple summary cron jobs from [summary-N] configs."""

    def __init__(self) -> None:
        self.scheduler: AsyncIOScheduler | None = None
        self._scheduler_config = config_manager.get_scheduler_config()

    async def start(self) -> bool:
        """Create and start the scheduler, register all jobs."""
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
                # review 时区参考 Trakt 从环境变量中获取 TZ，否则默认使用 shanghai
                timezone="Asia/Shanghai",
            )
            self.scheduler.start()
            self._schedule_all_jobs()
            logger.info("Summary 调度器启动成功")
            return True
        except Exception as e:
            logger.error(f"启动 Summary 调度器失败: {e}")
            return False

    async def stop(self) -> bool:
        """Shutdown the scheduler."""
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
        """Sync APScheduler jobs with [summary-N] configs."""
        if not self.scheduler or not self.scheduler.running:
            return

        configs = config_manager.get_summary_configs()
        active_ids: set[str] = set()

        for cfg in configs:
            job_config = SummaryJobConfig.from_config_dict(cfg)
            if not job_config.enabled:
                continue
            # review 这里把所有 job_config 为 enabled 的 job 都重新添加 job，是否会存在重复调度的问题？
            # review 两种情况，如果用户配置的是间隔时间刷新，这里是否会导致无修改的任务时间刷新？
            # review 如果是已经在的 job_id 重复添加是否会有问题？
            # review 能否判断配置是否有修改并对有修改的 job 进行重新调度？
            job_id = f"summary_{job_config.id}"
            active_ids.add(job_id)

            trigger = self._parse_cron(job_config.cron)
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

        # Remove jobs whose configs no longer exist or are disabled
        all_job_ids = {job.id for job in self.scheduler.get_jobs()}
        for jid in all_job_ids:
            if jid.startswith("summary_") and jid not in active_ids:
                try:
                    self.scheduler.remove_job(jid)
                    logger.info(f"Summary job removed: {jid}")
                except Exception:
                    # review 异常不要静默
                    pass

    def _parse_cron(self, cron_expression: str) -> CronTrigger:
        """Parse 5-field cron expression. Falls back to default '0 21 * * *'."""
        parts = cron_expression.strip().split()
        # review 考虑到用户总是通过 API 去修改定时任务配置，并且修改完成后，会重新调度任务，
        # review 所以这里应该抛出异常，并且在接口层面处理异常，而不是令人迷惑的默认定时。
        # review 测试用例应该增加对于异常的测试
        if len(parts) != 5:
            logger.warning(
                f"Invalid cron: '{cron_expression}', using default '0 21 * * *'"
            )
            parts = ["0", "21", "*", "*", "*"]
        return CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

    async def _run_job(self, job_config: SummaryJobConfig) -> None:
        """Execute a single summary job with timeout protection."""
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
        """Refresh jobs after config change (called from Web UI save flow)."""
        if self.scheduler and self.scheduler.running:
            self._schedule_all_jobs()

    async def apply_config_after_save(self) -> None:
        """Sync scheduler state after config.ini save."""
        if self.scheduler and self.scheduler.running:
            self._schedule_all_jobs()
        else:
            configs = config_manager.get_summary_configs()
            if any(SummaryJobConfig.from_config_dict(c).enabled for c in configs):
                await self.start()


# Singleton
summary_scheduler = SummaryScheduler()
