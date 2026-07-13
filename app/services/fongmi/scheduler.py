"""fongmi 局域网轮询定时同步（单任务，Cron 来自 config.ini [fongmi]）"""

from __future__ import annotations

import asyncio

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ...core.config import config_manager
from ...core.logging import logger
from .sync_service import fongmi_sync_service


class FongmiScheduler:
    JOB_ID = "fongmi_media_sync"

    def __init__(self) -> None:
        self.scheduler: AsyncIOScheduler | None = None
        self._scheduler_config = config_manager.get_scheduler_config()

    def _fongmi_enabled(self) -> bool:
        cfg = config_manager.get_fongmi_config()
        return bool(cfg.get("enabled"))

    async def start(self) -> bool:
        try:
            if self.scheduler and self.scheduler.running:
                if self._fongmi_enabled():
                    self._schedule_or_refresh_job()
                else:
                    try:
                        self.scheduler.remove_job(self.JOB_ID)
                    except Exception:
                        pass
                return True

            if not self._fongmi_enabled():
                logger.info(
                    "fongmi 同步未启用，本次不注册定时任务（APScheduler 未创建）"
                )
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
                timezone=self._scheduler_config.get("timezone", "Asia/Shanghai"),
            )
            self.scheduler.start()
            self._schedule_or_refresh_job()
            logger.info("fongmi 调度器启动成功")
            return True
        except Exception as e:
            logger.error(f"启动 fongmi 调度器失败: {e}")
            return False

    async def stop(self) -> bool:
        try:
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
                self.scheduler = None
                logger.info("fongmi 调度器已停止")
            return True
        except Exception as e:
            logger.error(f"停止 fongmi 调度器失败: {e}")
            return False

    def _default_cron_trigger(self) -> CronTrigger:
        """与 config.ini / get_fongmi_config 默认一致：每 5 分钟"""
        return CronTrigger(
            minute="*/5",
            hour="*",
            day="*",
            month="*",
            day_of_week="*",
        )

    def _parse_cron(self, cron_expression: str) -> CronTrigger:
        parts = cron_expression.split()
        if len(parts) != 5:
            logger.warning(f"fongmi Cron 无效，使用默认每 5 分钟: {cron_expression}")
            return self._default_cron_trigger()
        return CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

    def _schedule_or_refresh_job(self) -> None:
        if not self.scheduler or not self.scheduler.running:
            return
        cfg = config_manager.get_fongmi_config()
        cron_expr = cfg.get("sync_interval") or "*/5 * * * *"
        trigger = self._parse_cron(str(cron_expr))

        try:
            self.scheduler.remove_job(self.JOB_ID)
        except Exception:
            pass

        self.scheduler.add_job(
            func=self._run_sync_job,
            trigger=trigger,
            id=self.JOB_ID,
            name="fongmi media sync",
            replace_existing=True,
        )
        logger.info(f"fongmi 定时任务已注册: {cron_expr}")

    async def _run_sync_job(self) -> None:
        if not self._fongmi_enabled():
            logger.debug("fongmi 未启用，跳过定时同步")
            return
        timeout = self._scheduler_config.get("job_timeout", 300)
        try:
            await asyncio.wait_for(fongmi_sync_service.run_sync(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"fongmi 定时同步超时 ({timeout} 秒)")
        except Exception as e:
            logger.error(f"fongmi 定时同步失败: {e}")

    def reload_job_if_running(self) -> None:
        """配置保存后若调度器在运行，则按新 Cron 重建任务（无需整进程重启）"""
        if self.scheduler and self.scheduler.running and self._fongmi_enabled():
            self._schedule_or_refresh_job()

    async def apply_config_after_save(self) -> None:
        """保存 config.ini 后同步调度状态（与 Web 配置联动）"""
        if self._fongmi_enabled():
            if not self.scheduler or not self.scheduler.running:
                await self.start()
            else:
                self._schedule_or_refresh_job()
        else:
            await self.stop()


fongmi_scheduler = FongmiScheduler()
