"""
日志相关API
"""

import asyncio
import os
from collections import deque
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..core.config import config_manager
from ..core.logging import logger, resolved_dev_log_file_path
from ..utils.log_grouping import group_log_lines
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api", tags=["logs"])


def _read_lines_tail(log_file_path: str, limit: str) -> list[str]:
    """读取日志行；有 limit 时仅保留尾部行（deque）。"""
    with open(log_file_path, encoding="utf-8", errors="ignore") as f:
        if limit == "all":
            return f.readlines()
        try:
            limit_num = int(limit)
        except ValueError:
            return f.readlines()
        if limit_num <= 0:
            return []
        return list(deque(f, maxlen=limit_num))


def _filter_lines(
    lines: list[str],
    level: Optional[str],
    search: Optional[str],
) -> list[str]:
    """按级别与关键词筛选日志行。"""
    if level:
        level_upper = level.upper()
        lines = [
            line
            for line in lines
            if level_upper in line.upper()
            or (level_upper == "WARNING" and "WARN" in line.upper())
        ]
    if search:
        search_lower = search.lower()
        lines = [line for line in lines if search_lower in line.lower()]
    return lines


def _read_log_file(
    log_file_path: str,
    level: Optional[str],
    search: Optional[str],
    limit: str,
    grouped: bool = False,
) -> dict:
    """同步读取日志文件并应用筛选（阻塞 I/O，需在线程中执行）"""
    empty_stats = {"size": 0, "lines": 0, "modified": None, "errors": 0}
    if not os.path.exists(log_file_path):
        result: dict[str, Any] = {
            "stats": empty_stats,
        }
        if grouped:
            result["groups"] = []
            result["orphans"] = []
            result["debug_mode"] = config_manager.get("dev", "debug", fallback=False)
        else:
            result["content"] = ""
        return result

    file_stats = os.stat(log_file_path)
    file_size = file_stats.st_size
    file_modified = file_stats.st_mtime

    all_lines = _read_lines_tail(log_file_path, limit)
    error_count = sum(1 for line in all_lines if "ERROR" in line.upper())

    filtered = _filter_lines(all_lines, level, search)

    stats = {
        "size": file_size,
        "lines": len(filtered),
        "modified": file_modified * 1000,
        "errors": error_count,
    }

    if grouped:
        grouping = group_log_lines(filtered)
        return {
            "groups": grouping["groups"],
            "orphans": grouping["orphans"],
            "debug_mode": config_manager.get("dev", "debug", fallback=False),
            "stats": stats,
        }

    return {
        "content": "".join(filtered),
        "stats": stats,
    }


def _clear_log_file(log_file_path: str) -> None:
    """同步清空日志文件（阻塞 I/O，需在线程中执行）"""
    if os.path.exists(log_file_path):
        with open(log_file_path, "w", encoding="utf-8") as f:
            f.write("")


@router.get("/logs")
async def get_logs(
    request: Request,
    level: Optional[str] = None,
    search: Optional[str] = None,
    limit: str = Query("100"),
    grouped: bool = Query(False),
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """获取日志内容"""
    try:
        log_path = resolved_dev_log_file_path(config_manager)
        if log_path is None:
            empty = {
                "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
            }
            if grouped:
                empty["groups"] = []
                empty["orphans"] = []
                empty["debug_mode"] = config_manager.get("dev", "debug", fallback=False)
            else:
                empty["content"] = ""
            return {"status": "success", "data": empty}

        log_file_path = os.fspath(log_path)

        result = await asyncio.to_thread(
            _read_log_file, log_file_path, level, search, limit, grouped
        )

        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取日志失败: {str(e)}")


@router.post("/logs/clear")
async def clear_logs(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """清空日志文件"""
    try:
        log_path = resolved_dev_log_file_path(config_manager)
        if log_path is None:
            return {"status": "success", "message": "日志清空成功"}

        log_file_path = os.fspath(log_path)

        await asyncio.to_thread(_clear_log_file, log_file_path)

        return {"status": "success", "message": "日志清空成功"}
    except Exception as e:
        logger.error(f"清空日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}")
