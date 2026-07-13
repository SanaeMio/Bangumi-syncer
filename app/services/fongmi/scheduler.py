"""fongmi 局域网轮询定时同步（单任务，Cron 来自 config.ini [fongmi]）"""

from __future__ import annotations

import asyncio

from ..base.scheduler import BaseScheduler
from ...core.config import config_manager
from ...core.logging import logger
from .sync_service import fongmi_sync_service


class FongmiScheduler(BaseScheduler):
    """fongmi 局域网轮询同步调度器

    继承 BaseScheduler，仅需实现启用判定与同步任务执行。
    """

    JOB_ID = "fongmi_media_sync"
    DEFAULT_CRON = "*/5 * * * *"
    DRIVER_NAME = "fongmi"

    def _is_enabled(self) -> bool:
        """fongmi 仅需 enabled=True（设备在同步时再发现）"""
        cfg = config_manager.get_fongmi_config()
        return bool(cfg.get("enabled"))

    # 兼容旧测试与外部调用
    def _fongmi_enabled(self) -> bool:
        return self._is_enabled()

    def _get_driver_config(self) -> dict:
        return config_manager.get_fongmi_config()

    async def _run_sync_job(self) -> None:
        if not self._is_enabled():
            logger.debug("fongmi 未启用，跳过定时同步")
            return
        timeout = self._scheduler_config.get("job_timeout", 300)
        try:
            await asyncio.wait_for(fongmi_sync_service.run_sync(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"fongmi 定时同步超时 ({timeout} 秒)")
        except Exception as e:
            logger.error(f"fongmi 定时同步失败: {e}")


fongmi_scheduler = FongmiScheduler()
