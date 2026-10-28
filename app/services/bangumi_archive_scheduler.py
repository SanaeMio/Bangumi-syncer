"""Bangumi Archive 周更调度器

继承 BaseScheduler，按 [bangumi-archive] update_cron 定时触发更新。
默认 cron: "0 6 * * 3"（每周三 06:00，晚于官方 05:00 发布）。
"""

from __future__ import annotations

import asyncio

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.bangumi_archive import bangumi_archive
from .base.notifier_helpers import notify_scheduler_failure
from .base.scheduler import BaseScheduler

# Archive 更新包含全量 dump 下载（约 400MB，含镜像回退与重试）、解压与导入，
# 耗时远高于普通同步任务。[scheduler] job_timeout 默认 300 秒，按该值取消会在
# 下载或导入阶段中断更新；故以本常量作为下限，配置了更大的 job_timeout 时沿用。
ARCHIVE_UPDATE_TIMEOUT_SECONDS = 7200


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
        timeout = max(
            self._scheduler_config.get("job_timeout", 300),
            ARCHIVE_UPDATE_TIMEOUT_SECONDS,
        )
        try:
            await asyncio.wait_for(bangumi_archive.run_update(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"BangumiArchive 定时更新超时 ({timeout} 秒)")
            notify_scheduler_failure(
                "BangumiArchive",
                f"定时更新超时 ({timeout} 秒)",
                timeout=True,
            )
        except Exception as e:
            logger.error(f"BangumiArchive 定时更新失败: {e}")
            notify_scheduler_failure("BangumiArchive", str(e))


bangumi_archive_scheduler = BangumiArchiveScheduler()
