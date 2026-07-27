"""Bangumi Replay WebUI API

待同步队列的查看、手动补发、删除等管理接口。

提供：
- GET  /api/bangumi_replay/status          调度器状态 + 队列统计
- GET  /api/bangumi_replay/queue           待同步任务列表（分页）
- GET  /api/bangumi_replay/queue/{id}      单条任务详情
- POST /api/bangumi_replay/replay          手动触发补发（批量）
- POST /api/bangumi_replay/replay/{id}     手动补发单条
- DELETE /api/bangumi_replay/queue/{id}    删除单条任务
- POST /api/bangumi_replay/probe           手动探测 API 可达性
"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析模型字段的 ``str | None`` 会失败，此处保留 Optional

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..core.database import database_manager
from ..core.logging import logger
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/bangumi_replay", tags=["bangumi-replay"])


class ReplayStatusResponse(BaseModel):
    enabled: bool = Field(description="是否启用补发调度器")
    cron: str = Field(description="补发 cron 表达式")
    running: bool = Field(description="调度器是否运行中")
    queue_stats: dict[str, int] = Field(
        default_factory=dict, description="队列状态计数 {pending,synced,abandoned}"
    )


class QueueListResponse(BaseModel):
    status: str
    data: dict[str, Any]


class ReplayBatchResponse(BaseModel):
    status: str
    data: dict[str, Any]


@router.get("/status", response_model=ReplayStatusResponse)
async def get_replay_status(
    current_user: dict = Depends(get_current_user_flexible),
) -> ReplayStatusResponse:
    """获取补发调度器状态与队列统计"""
    try:
        from ..services.bangumi_replay_scheduler import bangumi_replay_scheduler

        status = bangumi_replay_scheduler.get_status()
        return ReplayStatusResponse(**status)
    except Exception as e:
        logger.error(f"获取 replay 状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue", response_model=QueueListResponse)
async def get_queue(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    status: Optional[str] = Query(
        None, description="按状态过滤：pending/synced/abandoned"
    ),
    current_user: dict = Depends(get_current_user_flexible),
) -> QueueListResponse:
    """获取待同步队列列表（分页）"""
    try:
        offset = (page - 1) * limit
        result = database_manager.get_pending_sync_queue(
            limit=limit, offset=offset, status=status
        )
        return QueueListResponse(status="success", data=result)
    except Exception as e:
        logger.error(f"获取待同步队列失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue/{record_id}", response_model=QueueListResponse)
async def get_queue_item(
    record_id: int,
    current_user: dict = Depends(get_current_user_flexible),
) -> QueueListResponse:
    """获取单条待同步任务详情（含 payload_json）"""
    try:
        record = database_manager.get_pending_sync_record_by_id(record_id)
        if not record:
            raise HTTPException(status_code=404, detail="记录不存在")
        return QueueListResponse(status="success", data={"record": record})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取待同步任务详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/replay", response_model=ReplayBatchResponse)
async def replay_batch(
    limit: int = Query(20, ge=1, le=200),
    current_user: dict = Depends(get_current_user_flexible),
) -> ReplayBatchResponse:
    """手动触发批量补发"""
    try:
        from ..services.sync_service import sync_service

        stats = await asyncio.to_thread(sync_service.replay_pending_batch, limit)
        return ReplayBatchResponse(status="success", data=stats)
    except Exception as e:
        logger.error(f"手动批量补发失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/replay/{record_id}", response_model=ReplayBatchResponse)
async def replay_single(
    record_id: int,
    current_user: dict = Depends(get_current_user_flexible),
) -> ReplayBatchResponse:
    """手动补发单条待同步任务"""
    try:
        from ..services.sync_service import sync_service

        record = database_manager.get_pending_sync_record_by_id(record_id)
        if not record:
            raise HTTPException(status_code=404, detail="记录不存在")

        result = await asyncio.to_thread(sync_service.replay_pending_item, record)
        if result.get("should_mark_synced") and result["success"]:
            database_manager.mark_pending_sync_synced(record_id)
        elif not result["success"]:
            msg = (result.get("message") or "").lower()
            if "不可达" not in msg and "unreachable" not in msg:
                database_manager.increment_pending_sync_attempts(
                    record_id, result.get("message", "")
                )

        return ReplayBatchResponse(status="success", data=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"手动补发单条失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/queue/{record_id}")
async def delete_queue_item(
    record_id: int,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, str]:
    """删除单条待同步任务（用户手动清理）"""
    try:
        if not database_manager.delete_pending_sync_record(record_id):
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"status": "success", "message": "已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除待同步任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/probe", response_model=ReplayBatchResponse)
async def probe_api(
    current_user: dict = Depends(get_current_user_flexible),
) -> ReplayBatchResponse:
    """手动探测 Bangumi API 可达性"""
    try:
        from ..services.bangumi_replay_scheduler import bangumi_replay_scheduler

        reachable = await bangumi_replay_scheduler._probe_api()
        return ReplayBatchResponse(
            status="success",
            data={
                "reachable": reachable,
                "message": "API 可达" if reachable else "API 不可达",
            },
        )
    except Exception as e:
        logger.error(f"探测 API 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
