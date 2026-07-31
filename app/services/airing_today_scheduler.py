"""今日放送提醒调度器

继承 BaseScheduler，按 [notify-airing-today] cron 每日定时触发，
查询今日放送并触发 airing_today 通知。

默认 cron: "0 9 * * *"（每日 09:00）。

前置条件：Bangumi Archive 已启用且已导入数据。
"仅我在追"模式需配置 Bangumi 账号。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.bangumi_api.collection import get_watching_subject_ids
from ..utils.bangumi_api.factory import (
    build_bangumi_api_from_active_config as _build_bangumi_api,
)
from ..utils.bangumi_archive import bangumi_archive
from ..utils.bangumi_archive._store import archive_store
from .base.notifier_helpers import notify_airing_today
from .base.scheduler import BaseScheduler


class AiringTodayScheduler(BaseScheduler):
    """今日放送提醒调度器"""

    JOB_ID = "airing_today_remind"
    DEFAULT_CRON = "0 9 * * *"  # 每日 09:00
    DRIVER_NAME = "AiringToday"

    def _is_enabled(self) -> bool:
        """notify-airing-today enabled=true 且 Archive 已启用"""
        if not bool(
            config_manager.get("notify-airing-today", "enabled", fallback=True)
        ):
            return False
        # reload_config 可能因配置非法或目录创建失败抛异常，
        # 此时视为 Archive 未启用，跳过本轮任务而非让调度器报错
        try:
            bangumi_archive.reload_config()
        except Exception as e:
            logger.warning(
                f"AiringToday: bangumi_archive.reload_config 失败，视为未启用: {e}"
            )
            return False
        return bangumi_archive.enabled

    def _get_driver_config(self) -> dict:
        """返回含 sync_interval 的配置（sync_interval 字段名复用为 cron）"""
        return {
            "sync_interval": config_manager.get(
                "notify-airing-today", "cron", fallback=self.DEFAULT_CRON
            ),
        }

    async def _run_sync_job(self) -> None:
        """查询今日放送并触发通知"""
        if not self._is_enabled():
            logger.debug("AiringToday 未启用或 Archive 未开启，跳过")
            return

        # 仅在追过滤
        only_watching = bool(
            config_manager.get("notify-airing-today", "only_watching", fallback=True)
        )

        timeout = self._scheduler_config.get("job_timeout", 120)
        try:
            await asyncio.wait_for(
                self._fetch_and_notify(only_watching), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"AiringToday 定时任务超时 ({timeout} 秒)")
        except Exception as e:
            logger.error(f"AiringToday 定时任务失败: {e}")

    async def _fetch_and_notify(self, only_watching: bool) -> None:
        """查询今日放送并发通知（可被 await 包装超时）"""
        active_db = bangumi_archive.get_active_db_path()
        if not active_db.exists():
            logger.warning("AiringToday: Archive 数据未导入，跳过")
            return

        # 使用调度器配置时区的"今日"，与 cron 调度保持同一日期边界
        today_str = config_manager.today_in_scheduler_tz().isoformat()
        subject_ids: set[int] | None = None
        actual_only_watching = False

        if only_watching:
            api = _build_bangumi_api()
            if api is not None:
                try:
                    # 在线程中执行同步 IO 调用
                    subject_ids = await asyncio.to_thread(get_watching_subject_ids, api)
                    actual_only_watching = True
                except Exception as e:
                    logger.warning(
                        f"AiringToday: 获取在看列表失败，降级为全部放送: {e}"
                    )
                    subject_ids = None
                finally:
                    # 临时构造的 BangumiApi 持有 httpx.Client 连接池，需显式释放
                    api.close()

        # 查询今日放送（在线程中执行 SQLite 查询）
        rows = await asyncio.to_thread(
            archive_store.get_episodes_by_airdate,
            start_date=today_str,
            end_date=today_str,
            subject_ids=subject_ids,
        )

        if not rows:
            logger.info(f"AiringToday: {today_str} 无放送数据，不触发通知")
            return

        # 构造通知 payload（精简字段，避免过大）
        episodes_payload: list[dict[str, Any]] = []
        for row in rows[:50]:  # 通知最多带 50 条，避免 payload 过大
            episodes_payload.append(
                {
                    "subject_id": row.get("subject_id"),
                    "subject_name": row.get("subject_name") or "",
                    "subject_name_cn": row.get("subject_name_cn") or "",
                    "subject_type": row.get("subject_type", 0),
                    "ep_sort": row.get("ep_sort"),
                    "ep_name": row.get("ep_name") or "",
                    "ep_name_cn": row.get("ep_name_cn") or "",
                }
            )

        logger.info(
            f"AiringToday: {today_str} 共 {len(rows)} 集放送"
            f"（only_watching={actual_only_watching}），触发通知"
        )
        notify_airing_today(
            airdate=today_str,
            total=len(rows),
            episodes=episodes_payload,
            only_watching=actual_only_watching,
        )


airing_today_scheduler = AiringTodayScheduler()
