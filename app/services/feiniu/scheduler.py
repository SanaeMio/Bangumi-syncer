"""飞牛 trimmedia 定时同步（单任务，Cron 来自 config.ini [feiniu]）"""

from __future__ import annotations

import asyncio
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger

from ...core.config import config_manager
from ...core.logging import logger
from ..base.notifier_helpers import (
    notify_batch_sync_summary,
    notify_scheduler_failure,
)
from ..base.scheduler import BaseScheduler
from .sync_service import feiniu_sync_service


class FeiniuScheduler(BaseScheduler):
    """飞牛 trimmedia.db 同步调度器

    继承 BaseScheduler，仅需实现启用判定与同步任务执行。
    """

    JOB_ID = "feiniu_trimmedia_sync"
    DEFAULT_CRON = "*/15 * * * *"
    DRIVER_NAME = "飞牛"

    def _is_enabled(self) -> bool:
        """飞牛需要 enabled=True 且 db_path 指向存在的文件"""
        cfg = config_manager.get_feiniu_config()
        if not cfg.get("enabled"):
            return False
        dbp = (cfg.get("db_path") or "").strip()
        if not dbp:
            return False
        return Path(dbp).is_file()

    # 兼容旧测试与外部调用
    def _feiniu_enabled_with_db(self) -> bool:
        return self._is_enabled()

    def _default_feiniu_cron_trigger(self) -> CronTrigger:
        """兼容旧测试"""
        return self._default_cron_trigger()

    def _get_driver_config(self) -> dict:
        return config_manager.get_feiniu_config()

    async def _run_sync_job(self) -> None:
        if not self._is_enabled():
            logger.debug("飞牛未启用或数据库不可用，跳过定时同步")
            return
        timeout = self._scheduler_config.get("job_timeout", 300)
        try:
            result = await asyncio.wait_for(
                feiniu_sync_service.run_sync(), timeout=timeout
            )
            notify_batch_sync_summary(
                "feiniu",
                total=result.synced_count + result.skipped_count + result.error_count,
                succeeded=result.synced_count,
                failed=result.error_count,
                skipped=result.skipped_count,
            )
        except asyncio.TimeoutError:
            logger.error(f"飞牛定时同步超时 ({timeout} 秒)")
            notify_scheduler_failure(
                "feiniu", f"定时同步超时 ({timeout} 秒)", timeout=True
            )
        except Exception as e:
            logger.error(f"飞牛定时同步失败: {e}")
            notify_scheduler_failure("feiniu", str(e))


feiniu_scheduler = FeiniuScheduler()
