"""Bangumi Archive 定时更新：超时边界与失败通知。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.bangumi_archive_scheduler import (
    ARCHIVE_UPDATE_TIMEOUT_SECONDS,
    BangumiArchiveScheduler,
)


def _capture_wait_for(captured: dict) -> object:
    """替换 asyncio.wait_for，记录超时值后继续等待被包装的协程。"""

    async def _fake_wait_for(awaitable, timeout):
        captured["timeout"] = timeout
        return await awaitable

    return _fake_wait_for


@pytest.mark.asyncio
async def test_scheduled_update_timeout_covers_archive_workload():
    """定时更新不得复用 [scheduler] 的通用 job_timeout（默认 300 秒）。

    Archive 更新包含 dump 下载、解压与导入，耗时远超普通同步任务；按 300 秒
    取消会在下载阶段中断，使定时更新稳定失败。
    """
    scheduler = BangumiArchiveScheduler()
    scheduler._scheduler_config = {"job_timeout": 300}
    captured: dict = {}

    with (
        patch.object(scheduler, "_is_enabled", return_value=True),
        patch(
            "app.services.bangumi_archive_scheduler.bangumi_archive.run_update",
            new=AsyncMock(return_value="task-id"),
        ),
        patch(
            "app.services.bangumi_archive_scheduler.asyncio.wait_for",
            new=_capture_wait_for(captured),
        ),
    ):
        await scheduler._run_sync_job()

    assert captured["timeout"] == ARCHIVE_UPDATE_TIMEOUT_SECONDS
    assert captured["timeout"] > 300


@pytest.mark.asyncio
async def test_scheduled_update_allows_higher_configured_timeout():
    """显式调高的 [scheduler] job_timeout 仍应生效。"""
    scheduler = BangumiArchiveScheduler()
    scheduler._scheduler_config = {"job_timeout": ARCHIVE_UPDATE_TIMEOUT_SECONDS + 600}
    captured: dict = {}

    with (
        patch.object(scheduler, "_is_enabled", return_value=True),
        patch(
            "app.services.bangumi_archive_scheduler.bangumi_archive.run_update",
            new=AsyncMock(return_value="task-id"),
        ),
        patch(
            "app.services.bangumi_archive_scheduler.asyncio.wait_for",
            new=_capture_wait_for(captured),
        ),
    ):
        await scheduler._run_sync_job()

    assert captured["timeout"] == ARCHIVE_UPDATE_TIMEOUT_SECONDS + 600


@pytest.mark.asyncio
async def test_scheduled_update_timeout_notifies_failure():
    """超时仍走既有失败通知，不向调度器抛出异常。"""
    scheduler = BangumiArchiveScheduler()
    scheduler._scheduler_config = {"job_timeout": 300}

    with (
        patch.object(scheduler, "_is_enabled", return_value=True),
        patch(
            "app.services.bangumi_archive_scheduler.bangumi_archive.run_update",
            new=AsyncMock(return_value="task-id"),
        ),
        patch(
            "app.services.bangumi_archive_scheduler.asyncio.wait_for",
            new=AsyncMock(side_effect=asyncio.TimeoutError),
        ),
        patch(
            "app.services.bangumi_archive_scheduler.notify_scheduler_failure"
        ) as notify,
    ):
        await scheduler._run_sync_job()

    notify.assert_called_once()
    assert notify.call_args.kwargs["timeout"] is True


@pytest.mark.asyncio
async def test_disabled_skips_update():
    """未启用时不触发更新任务。"""
    scheduler = BangumiArchiveScheduler()
    scheduler._scheduler_config = {"job_timeout": 300}
    run_update = AsyncMock(return_value="task-id")

    with (
        patch.object(scheduler, "_is_enabled", return_value=False),
        patch(
            "app.services.bangumi_archive_scheduler.bangumi_archive.run_update",
            new=run_update,
        ),
    ):
        await scheduler._run_sync_job()

    run_update.assert_not_called()
