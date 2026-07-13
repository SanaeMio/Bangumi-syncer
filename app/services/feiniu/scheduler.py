"""飞牛 trimmedia 定时同步（单任务，Cron 来自 config.ini [feiniu]）"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ...core.config import config_manager
from ...core.logging import logger
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

    def _default_feiniu_cron_trigger(self):
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
            await asyncio.wait_for(feiniu_sync_service.run_sync(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"飞牛定时同步超时 ({timeout} 秒)")
        except Exception as e:
            logger.error(f"飞牛定时同步失败: {e}")


feiniu_scheduler = FeiniuScheduler()
