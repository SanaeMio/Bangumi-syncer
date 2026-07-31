"""调度器通知辅助

为各调度器（archive / replay / feiniu / fongmi / trakt / summary）提供统一的事件通知入口：

- `notify_scheduler_failure(driver, error, *, timeout=False, **ctx)`：调度任务异常/超时
  时触发 `scheduler_job_failed`
- `notify_batch_sync_summary(driver, total, succeeded, failed, skipped=0, **ctx)`：批量同步
  完成时触发 `batch_sync_summary`

调用方只需提供 driver 名与统计字段，其他细节（冷却、路由、站内信）由 NotificationService 处理。
"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ..notification_service import notification_service


def notify_scheduler_failure(
    driver: str,
    error: str,
    *,
    timeout: bool = False,
    **ctx: Any,
) -> None:
    """调度任务失败/超时通知

    Args:
        driver: 调度器标识（如 "BangumiArchive" / "feiniu" / "trakt"）
        error: 错误信息
        timeout: 是否为超时
        **ctx: 附加上下文字段（如 user_id、job_name、task_id）
    """
    try:
        notification_service.notify(
            "scheduler_job_failed",
            source=f"scheduler:{driver}",
            error_message=error,
            driver=driver,
            is_timeout=timeout,
            **ctx,
        )
    except Exception as e:
        logger.debug(f"发送 scheduler_job_failed 通知失败（可忽略）: {e}")


def notify_batch_sync_summary(
    driver: str,
    total: int,
    succeeded: int,
    failed: int,
    *,
    skipped: int = 0,
    **ctx: Any,
) -> None:
    """批量同步汇总通知

    Args:
        driver: 数据源标识（如 "trakt" / "feiniu" / "fongmi" / "bangumi-replay"）
        total: 总处理数
        succeeded: 成功数
        failed: 失败数
        skipped: 跳过/仍不可达数
        **ctx: 附加上下文字段（如 duration、user_id、discovered_devices）
    """
    try:
        notification_service.notify(
            "batch_sync_summary",
            source=f"batch:{driver}",
            total=total,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            **ctx,
        )
    except Exception as e:
        logger.debug(f"发送 batch_sync_summary 通知失败（可忽略）: {e}")


def notify_queue_size_warning(pending_count: int, threshold: int) -> None:
    """待同步队列堆积预警

    Args:
        pending_count: 当前 pending 任务数
        threshold: 触发阈值
    """
    try:
        notification_service.notify(
            "queue_size_warning",
            source="bangumi-replay",
            pending_count=pending_count,
            threshold=threshold,
        )
    except Exception as e:
        logger.debug(f"发送 queue_size_warning 通知失败（可忽略）: {e}")


def notify_source_event(
    source: str,
    event: str,
    *,
    error_message: str = "",
    **ctx: Any,
) -> None:
    """数据源拉取事件通知

    Args:
        source: 数据源标识（如 "trakt" / "feiniu" / "fongmi"）
        event: "failed" 或 "empty"
        error_message: 失败时的错误信息
        **ctx: 附加上下文字段（如 user_id、message）
    """
    notification_type = (
        "source_fetch_failed" if event == "failed" else "source_fetch_empty"
    )
    try:
        notification_service.notify(
            notification_type,
            source=source,
            error_message=error_message,
            **ctx,
        )
    except Exception as e:
        logger.debug(f"发送 {notification_type} 通知失败（可忽略）: {e}")


def notify_airing_today(
    airdate: str,
    total: int,
    episodes: list[dict[str, Any]],
    *,
    only_watching: bool = True,
) -> None:
    """今日放送提醒通知

    Args:
        airdate: 日期 YYYY-MM-DD
        total: 当日放送总集数
        episodes: 放送章节列表（每项含 subject_name / ep_sort 等字段）
        only_watching: 是否仅展示在追番剧
    """
    try:
        notification_service.notify(
            "airing_today",
            source="scheduler:airing_today",
            airdate=airdate,
            total=total,
            episodes=episodes,
            only_watching=only_watching,
        )
    except Exception as e:
        logger.debug(f"发送 airing_today 通知失败（可忽略）: {e}")
