"""Bangumi Archive 周更调度器

继承 BaseScheduler，按 [bangumi-archive] update_cron 定时触发更新。
默认 cron: "0 6 * * 3"（每周三 06:00，晚于官方 05:00 发布）。
"""

from __future__ import annotations

import asyncio

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.bangumi_archive import bangumi_archive
from .base.scheduler import BaseScheduler


class BangumiArchiveScheduler(BaseScheduler):
    """Archive 周更调度器

    与 feiniu/fongmi 调度器一致，继承 BaseScheduler 公共逻辑。
    子类只需实现 4 个抽象方法。
    """

    JOB_ID = "bangumi_archive_update"
    DEFAULT_CRON = "0 8 * * 3"  # 每周三 08:00
    DRIVER_NAME = "BangumiArchive"

    def _is_enabled(self) -> bool:
        """Archive 启用且配置 enabled=true 才注册定时任务"""
        return bool(config_manager.get("bangumi-archive", "enabled", fallback=False))

    def _get_driver_config(self) -> dict:
        """返回含 sync_interval 的配置（sync_interval 字段名复用为 cron）"""
        return {
            "sync_interval": bangumi_archive.update_cron,
        }

    async def _run_sync_job(self) -> None:
        """执行一次完整更新流程"""
        if not self._is_enabled():
            logger.debug("BangumiArchive 未启用，跳过定时更新")
            return
        timeout = self._scheduler_config.get("job_timeout", 1800)  # 默认 30 分钟
        try:
            await asyncio.wait_for(bangumi_archive.run_update(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"BangumiArchive 定时更新超时 ({timeout} 秒)")
        except Exception as e:
            logger.error(f"BangumiArchive 定时更新失败: {e}")


bangumi_archive_scheduler = BangumiArchiveScheduler()
