"""
日志相关API
"""

import asyncio
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..core.config import config_manager
from ..core.logging import logger, resolved_dev_log_file_path
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api", tags=["logs"])


def _read_log_file(
    log_file_path: str,
    level: Optional[str],
    search: Optional[str],
    limit: str,
) -> dict:
    """同步读取日志文件并应用筛选（阻塞 I/O，需在线程中执行）"""
    if not os.path.exists(log_file_path):
        return {
            "content": "",
            "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
        }

    # 获取文件统计信息
    file_stats = os.stat(log_file_path)
    file_size = file_stats.st_size
    file_modified = file_stats.st_mtime

    # 读取日志内容
    with open(log_file_path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    # 统计错误数量
    error_count = sum(1 for line in lines if "ERROR" in line.upper())

    # 应用筛选
    if level:
        lines = [line for line in lines if level.upper() in line.upper()]

    if search:
        lines = [line for line in lines if search.lower() in line.lower()]

    # 限制行数
    if limit != "all":
        try:
            limit_num = int(limit)
            lines = lines[-limit_num:] if len(lines) > limit_num else lines
        except ValueError:
            pass

    content = "".join(lines)

    return {
        "content": content,
        "stats": {
            "size": file_size,
            "lines": len(lines) if not level and not search else len(lines),
            "modified": file_modified * 1000,  # 转换为毫秒
            "errors": error_count,
        },
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
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """获取日志内容"""
    try:
        log_path = resolved_dev_log_file_path(config_manager)
        if log_path is None:
            return {
                "status": "success",
                "data": {
                    "content": "",
                    "stats": {"size": 0, "lines": 0, "modified": None, "errors": 0},
                },
            }

        log_file_path = os.fspath(log_path)

        # 文件 I/O 放入线程避免阻塞事件循环
        result = await asyncio.to_thread(
            _read_log_file, log_file_path, level, search, limit
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

        # 文件 I/O 放入线程避免阻塞事件循环
        await asyncio.to_thread(_clear_log_file, log_file_path)

        return {"status": "success", "message": "日志清空成功"}
    except Exception as e:
        logger.error(f"清空日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}")
