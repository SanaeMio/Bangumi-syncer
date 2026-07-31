"""Bangumi Archive WebUI API

提供：
- GET  /api/bangumi_archive/status          状态查询（含当前进度）
- POST /api/bangumi_archive/trigger         手动触发更新（返回 task_id）
- POST /api/bangumi_archive/import_local    上传本地 zip 导入（返回 task_id）
- GET  /api/bangumi_archive/progress        SSE 进度流（支持刷新恢复）
- GET  /api/bangumi_archive/progress_log    获取进度历史日志
"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析模型字段的 ``str | None`` 会失败，此处保留 Optional

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..core.background_tasks import register_background_task
from ..utils.bangumi_archive import bangumi_archive
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/bangumi_archive", tags=["bangumi-archive"])

# 上传文件大小限制：1GB（dump zip 约 400MB，留余量）
_MAX_UPLOAD_SIZE = 1024 * 1024 * 1024


class ArchiveStatusResponse(BaseModel):
    enabled: bool = Field(description="是否启用")
    active: str = Field(description="当前 active 库名（a/b）")
    active_db_path: str = Field(description="active 库路径")
    db_size_bytes: int = Field(description="active 库大小")
    last_import_at: Optional[str] = Field(description="上次导入时间 ISO8601")
    last_import_duration_sec: Optional[float] = Field(description="上次导入耗时秒")
    dump_date: Optional[str] = Field(description="dump 文件 created_at")
    dump_filename: Optional[str] = Field(description="dump 文件名")
    dump_size_bytes: Optional[int] = Field(description="dump 文件大小")
    row_counts: dict[str, int] = Field(default_factory=dict, description="各表行数")
    last_error: Optional[str] = Field(description="上次错误信息")
    last_error_at: Optional[str] = Field(description="上次错误时间")
    import_in_progress: bool = Field(description="是否正在导入")
    current_task_id: Optional[str] = Field(description="当前任务 ID")
    current_progress: Optional[dict[str, Any]] = Field(
        None, description="当前任务最近一次进度"
    )
    update_cron: str = Field(description="更新 cron 表达式")
    next_run_at: Optional[str] = Field(description="预计下次导入时间 ISO8601")
    data_dir: str = Field(description="数据目录")


class TriggerResponse(BaseModel):
    status: str
    task_id: Optional[str] = None
    detail: Optional[str] = None


class ProgressResponse(BaseModel):
    task_id: str
    stage: str
    percent: int
    message: str
    error: Optional[str] = None
    timestamp: Optional[float] = None


@router.get("/status", response_model=ArchiveStatusResponse)
async def get_status(
    user: dict = Depends(get_current_user_flexible),
) -> ArchiveStatusResponse:
    """获取 Archive 当前状态（含当前进度，刷新页面可恢复）"""
    bangumi_archive.reload_config()
    status = bangumi_archive.get_status()
    next_run_at = _compute_next_run(bangumi_archive.update_cron)
    return ArchiveStatusResponse(
        enabled=status["enabled"],
        active=status["active"],
        active_db_path=status["active_db_path"],
        db_size_bytes=status["db_size_bytes"],
        last_import_at=status["last_import_at"],
        last_import_duration_sec=status["last_import_duration_sec"],
        dump_date=status["dump_date"],
        dump_filename=status["dump_filename"],
        dump_size_bytes=status["dump_size_bytes"],
        row_counts=status["row_counts"],
        last_error=status["last_error"],
        last_error_at=status["last_error_at"],
        import_in_progress=status["import_in_progress"],
        current_task_id=status["current_task_id"],
        current_progress=status["current_progress"],
        update_cron=status["update_cron"],
        next_run_at=next_run_at,
        data_dir=status["data_dir"],
    )


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_update(
    force: bool = False,
    user: dict = Depends(get_current_user_flexible),
) -> TriggerResponse:
    """手动触发更新（返回 task_id 供 SSE 订阅）"""
    bangumi_archive.reload_config()
    if not bangumi_archive.enabled:
        return TriggerResponse(
            status="error",
            detail="Archive 未启用，请在配置中开启 [bangumi-archive] enabled = true",
        )
    if bangumi_archive.is_import_in_progress:
        return TriggerResponse(
            status="error",
            detail="已有导入任务进行中",
            task_id=bangumi_archive.current_task_id,
        )

    async def _run() -> None:
        try:
            await bangumi_archive.run_update(force=force)
        except Exception:
            # 错误已在 run_update 内部记录到 meta
            pass

    register_background_task(_run())
    # 等待 task_id 被赋值（最多 1 秒）
    for _ in range(10):
        if bangumi_archive.current_task_id:
            return TriggerResponse(
                status="started",
                task_id=bangumi_archive.current_task_id,
                detail="更新任务已启动",
            )
        await asyncio.sleep(0.1)
    return TriggerResponse(status="started", detail="更新任务已启动")


@router.post("/import_local", response_model=TriggerResponse)
async def import_local_zip(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user_flexible),
) -> TriggerResponse:
    """上传本地 zip 文件并导入

    适用场景：用户在网络受限环境手动下载 dump zip 后上传导入。
    跳过下载阶段，直接进入解压导入流程。
    """
    bangumi_archive.reload_config()
    if not bangumi_archive.enabled:
        return TriggerResponse(
            status="error",
            detail="Archive 未启用，请在配置中开启 [bangumi-archive] enabled = true",
        )
    if bangumi_archive.is_import_in_progress:
        return TriggerResponse(
            status="error",
            detail="已有导入任务进行中",
            task_id=bangumi_archive.current_task_id,
        )

    # 校验文件名（剥离路径前缀，防止 CWE-22 路径穿越）
    filename = Path(file.filename or "upload.zip").name
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 文件")
    if not filename:
        raise HTTPException(status_code=400, detail="文件名无效")

    # 提前检查磁盘空间：在保存上传文件之前就失败，避免用户上传数百 MB 后才报错
    # 与下载流程对称（下载在 _do_update 起始处检查，上传在此处检查）
    try:
        bangumi_archive.check_disk_space()
    except RuntimeError as e:
        raise HTTPException(status_code=507, detail=str(e)) from e

    # 保存到 data_dir/.tmp/<task_id>/ 下的临时文件
    # 与下载流程统一，避免使用系统 temp 导致跨磁盘空间检查盲区
    tmp_root = bangumi_archive.get_tmp_dir()
    tmp_dir = tmp_root / f"upload_{int(time.time() * 1000)}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / filename

    def _cleanup_tmp() -> None:
        """清理临时文件与目录（幂等，多次调用安全）"""
        try:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    background_scheduled = False
    try:
        size = 0
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(8192):
                f.write(chunk)
                size += len(chunk)
                if size > _MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件过大，超过 {_MAX_UPLOAD_SIZE // (1024 * 1024)}MB 限制",
                    )
    except Exception:
        # 任何写入异常（磁盘满、超限等）都清理临时文件，避免泄漏
        if not background_scheduled:
            _cleanup_tmp()
        raise
    finally:
        await file.close()

    # 后台执行导入
    async def _run() -> None:
        try:
            await bangumi_archive.import_local_zip(tmp_path)
        except Exception:
            pass
        finally:
            _cleanup_tmp()

    register_background_task(_run())
    background_scheduled = True
    # 等待 task_id 被赋值
    for _ in range(10):
        if bangumi_archive.current_task_id:
            return TriggerResponse(
                status="started",
                task_id=bangumi_archive.current_task_id,
                detail=f"已接收上传文件 {filename}（{size // 1024}KB），导入中",
            )
        await asyncio.sleep(0.1)
    return TriggerResponse(status="started", detail="导入任务已启动")


@router.get("/progress")
async def progress_stream(
    task_id: str,
    user: dict = Depends(get_current_user_flexible),
):
    """SSE 推送导入进度

    支持刷新恢复：若 task 已结束但缓存未清理，会立即推送最终进度后关闭。
    """
    # 优先获取持久化缓存（任务可能已结束）
    cached = bangumi_archive.get_cached_progress(task_id)
    queue = bangumi_archive.get_progress_queue(task_id)

    if not queue and not cached:
        raise HTTPException(status_code=404, detail="任务不存在或进度已清理")

    # 先推送历史日志（让前端恢复完整进度）
    history = bangumi_archive.get_progress_log(task_id)

    async def event_generator() -> AsyncGenerator[dict[str, Any], None]:
        # 1. 推送历史日志（刷新恢复用）
        # 若历史最后一条已是终态，说明任务已结束，推完历史即返回
        last_stage: Optional[str] = None
        # 记录已推送的最大 timestamp，用于跳过 queue 中已存在于 history 的事件
        # 避免 history + queue 重复推送（如 checking/fetching_latest 出现两次）
        last_ts = 0.0
        if history:
            last_stage = history[-1].get("stage")
            last_ts = history[-1].get("timestamp", 0)
            yield {
                "event": "history",
                "data": json.dumps({"events": history}, ensure_ascii=False),
            }
            if last_stage in ("done", "error", "skipped"):
                return

        # 2. 推送缓存中的最新进度（可能比 history 最后一条更新）
        # 用 timestamp 判断是否已推送，避免同 stage 不同 percent 被跳过
        # (如 downloading 30% → 45%，stage 相同但 percent 更新)
        if cached:
            if cached.timestamp > last_ts:
                last_ts = cached.timestamp
                yield {
                    "event": "progress",
                    "data": json.dumps(cached.to_dict(), ensure_ascii=False),
                }
            # 终态判断独立于 stage 是否变化，避免 cached 为终态但与 history
            # 最后一条 stage 相同时漏掉 return
            if cached.stage in ("done", "error", "skipped"):
                return

        # 3. 实时监听队列（任务进行中）
        if queue is None:
            return

        while True:
            try:
                p = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": ""}
                continue

            # 跳过已推送的事件（按 timestamp 去重，避免 history 与 queue 重复）
            if p.get("timestamp", 0) <= last_ts:
                continue

            yield {
                "event": "progress",
                "data": json.dumps(p, ensure_ascii=False),
            }

            if p.get("stage") in ("done", "error", "skipped"):
                return

    return EventSourceResponse(event_generator())


@router.get("/progress_log", response_model=list[ProgressResponse])
async def get_progress_log(
    task_id: str,
    user: dict = Depends(get_current_user_flexible),
) -> list[ProgressResponse]:
    """获取任务进度历史日志"""
    logs = bangumi_archive.get_progress_log(task_id)
    if not logs:
        raise HTTPException(status_code=404, detail="任务不存在或日志已清理")
    return [ProgressResponse(**log) for log in logs]


def _compute_next_run(cron_expr: str) -> Optional[str]:
    """计算下次导入时间"""
    try:
        from apscheduler.triggers.cron import CronTrigger

        parts = cron_expr.split()
        if len(parts) != 5:
            return None
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone="Asia/Shanghai",
        )
        next_fire = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
        if next_fire is None:
            return None
        return next_fire.isoformat()
    except Exception:
        return None
