"""飞牛 trimmedia 定时同步（单任务，Cron 来自 config.ini [feiniu]）"""

from __future__ import annotations

import asyncio

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ...core.config import config_manager
from ...core.logging import logger
from .sync_service import feiniu_sync_service


class FeiniuScheduler:
    JOB_ID = "feiniu_trimmedia_sync"

    def __init__(self) -> None:
        self.scheduler: AsyncIOScheduler | None = None
        self._scheduler_config = config_manager.get_scheduler_config()

    def _feiniu_enabled_with_db(self) -> bool:
        cfg = config_manager.get_feiniu_config()
        if not cfg.get("enabled"):
            return False
        dbp = (cfg.get("db_path") or "").strip()
        if not dbp:
            return False
        from pathlib import Path

        return Path(dbp).is_file()

    async def start(self) -> bool:
        try:
            if self.scheduler and self.scheduler.running:
                if self._feiniu_enabled_with_db():
                    self._schedule_or_refresh_job()
                else:
                    try:
                        self.scheduler.remove_job(self.JOB_ID)
                    except Exception:
                        pass
                return True

            if not self._feiniu_enabled_with_db():
                logger.info(
                    "飞牛同步未启用或数据库路径无效，本次不注册飞牛定时任务（APScheduler 未创建）"
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
                timezone="Asia/Shanghai",
            )
            self.scheduler.start()
            self._schedule_or_refresh_job()
            logger.info("飞牛调度器启动成功")
            return True
        except Exception as e:
            logger.error(f"启动飞牛调度器失败: {e}")
            return False

    async def stop(self) -> bool:
        try:
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
                self.scheduler = None
                logger.info("飞牛调度器已停止")
            return True
        except Exception as e:
            logger.error(f"停止飞牛调度器失败: {e}")
            return False

    def _default_feiniu_cron_trigger(self) -> CronTrigger:
        """与 config.ini / get_feiniu_config 默认一致：每 15 分钟"""
        return CronTrigger(
            minute="*/15",
            hour="*",
            day="*",
            month="*",
            day_of_week="*",
        )

    def _parse_cron(self, cron_expression: str) -> CronTrigger:
        parts = cron_expression.split()
        if len(parts) != 5:
            logger.warning(f"飞牛 Cron 无效，使用默认每 15 分钟: {cron_expression}")
            return self._default_feiniu_cron_trigger()
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
        cfg = config_manager.get_feiniu_config()
        cron_expr = cfg.get("sync_interval") or "*/15 * * * *"
        trigger = self._parse_cron(str(cron_expr))

        try:
            self.scheduler.remove_job(self.JOB_ID)
        except Exception:
            pass

        self.scheduler.add_job(
            func=self._run_sync_job,
            trigger=trigger,
            id=self.JOB_ID,
            name="Feiniu trimmedia sync",
            replace_existing=True,
        )
        logger.info(f"飞牛定时任务已注册: {cron_expr}")

    async def _run_sync_job(self) -> None:
        if not self._feiniu_enabled_with_db():
            logger.debug("飞牛未启用或数据库不可用，跳过定时同步")
            return
        timeout = self._scheduler_config.get("job_timeout", 300)
        try:
            await asyncio.wait_for(feiniu_sync_service.run_sync(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"飞牛定时同步超时 ({timeout} 秒)")
        except Exception as e:
            logger.error(f"飞牛定时同步失败: {e}")

    def reload_job_if_running(self) -> None:
        """配置保存后若调度器在运行，则按新 Cron 重建任务（无需整进程重启）"""
        if self.scheduler and self.scheduler.running and self._feiniu_enabled_with_db():
            self._schedule_or_refresh_job()

    async def apply_config_after_save(self) -> None:
        """保存 config.ini 后同步调度状态（与 Web 配置联动）"""
        if self._feiniu_enabled_with_db():
            if not self.scheduler or not self.scheduler.running:
                await self.start()
            else:
                self._schedule_or_refresh_job()
        else:
            await self.stop()


feiniu_scheduler = FeiniuScheduler()
