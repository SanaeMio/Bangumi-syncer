"""
一键升级 API

仅支持直装模式（非 Docker）。
"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析模型字段的 ``str | None`` 会失败，此处保留 Optional

from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..core.app_version import get_version
from ..services.upgrade_service import upgrade_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api", tags=["app"])


class UpgradeStatusResponse(BaseModel):
    environment: str = Field(description="docker 或 direct")
    upgrade_capable: bool
    upgrade_in_progress: bool
    current_version: str


class UpgradeRequest(BaseModel):
    target_version: Optional[str] = Field(
        default=None, description="目标版本，None 表示最新"
    )


class UpgradeTriggerResponse(BaseModel):
    status: str
    upgrade_id: Optional[str] = None
    detail: Optional[str] = None


class UpgradeProgressResponse(BaseModel):
    upgrade_id: str
    stage: str
    percent: int
    message: str
    error: Optional[str] = None


@router.get("/app/upgrade/status", response_model=UpgradeStatusResponse)
async def upgrade_status(
    user: dict = Depends(get_current_user_flexible),
):
    """获取升级状态"""
    from ..utils.docker_helper import docker_helper

    env = "docker" if docker_helper.is_docker else "direct"
    return UpgradeStatusResponse(
        environment=env,
        upgrade_capable=upgrade_service.is_upgrade_capable(),
        upgrade_in_progress=upgrade_service.is_upgrade_in_progress,
        current_version=get_version(),
    )


@router.post("/app/upgrade", response_model=UpgradeTriggerResponse)
async def trigger_upgrade(
    req: UpgradeRequest,
    user: dict = Depends(get_current_user_flexible),
):
    """触发一键升级"""
    if not upgrade_service.is_upgrade_capable():
        raise HTTPException(
            status_code=400,
            detail="当前环境不支持一键升级（仅支持直装模式）",
        )

    if upgrade_service.is_upgrade_in_progress:
        raise HTTPException(status_code=409, detail="已有升级任务进行中")

    try:
        upgrade_id = await upgrade_service.start_upgrade(req.target_version)
        return UpgradeTriggerResponse(status="started", upgrade_id=upgrade_id)
    except RuntimeError as e:
        return UpgradeTriggerResponse(status="error", detail=str(e))


@router.get("/app/upgrade/progress")
async def upgrade_progress(
    upgrade_id: str,
    user: dict = Depends(get_current_user_flexible),
):
    """SSE 推送升级进度（队列模式，实时推送每个阶段变化）"""
    queue = upgrade_service.get_progress_queue(upgrade_id)
    if not queue:
        # 队列不存在：可能是任务已完成或不存在，尝试返回当前状态
        progress = upgrade_service.get_progress(upgrade_id)
        if not progress:
            raise HTTPException(status_code=404, detail="升级任务不存在")

        # 任务已结束，直接返回最终状态
        async def final_event():
            data = {
                "stage": progress.stage.value,
                "percent": progress.percent,
                "message": progress.message,
                "error": progress.error,
            }
            yield {"event": "progress", "data": json.dumps(data, ensure_ascii=False)}

        return EventSourceResponse(final_event())

    async def event_generator():
        while True:
            try:
                p = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # 超时无更新，发送心跳保持连接
                yield {"event": "ping", "data": ""}
                continue

            data = {
                "stage": p.stage.value,
                "percent": p.percent,
                "message": p.message,
                "error": p.error,
            }
            yield {"event": "progress", "data": json.dumps(data, ensure_ascii=False)}

            if p.stage.value in ("done", "error"):
                return

    return EventSourceResponse(event_generator())


@router.post("/app/upgrade/restart")
async def restart_after_upgrade(
    user: dict = Depends(get_current_user_flexible),
):
    """升级完成后重启应用"""

    async def delayed_restart():
        await asyncio.sleep(1)
        from ..services.upgrade_service import restart_application

        restart_application()

    asyncio.create_task(delayed_restart())
    return {"status": "restarting"}
