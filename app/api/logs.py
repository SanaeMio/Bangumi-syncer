"""
日志相关API
"""
import os
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import JSONResponse

from ..core.config import config_manager
from ..core.logging import logger
from .deps import get_current_user_flexible


router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
async def get_logs(
    request: Request,
    level: Optional[str] = None,
    search: Optional[str] = None,
    limit: str = Query("100"),
    current_user: dict = Depends(get_current_user_flexible)
):
    """获取日志内容"""
    try:
        # 从配置文件中获取日志文件路径
        log_file_path = config_manager.get_config('dev', 'log_file', fallback='./log.txt')
        
        # 处理相对路径
        if log_file_path.startswith('./'):
            cwd = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            log_file_path = os.path.join(cwd, log_file_path.split('./', 1)[1])
        
        if not os.path.exists(log_file_path):
            return {
                "status": "success",
                "data": {
                    "content": "",
                    "stats": {
                        "size": 0,
                        "lines": 0,
                        "modified": None,
                        "errors": 0
                    }
                }
            }
        
        # 获取文件统计信息
        file_stats = os.stat(log_file_path)
        file_size = file_stats.st_size
        file_modified = file_stats.st_mtime
        
        # 读取日志内容
        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        # 统计错误数量
        error_count = sum(1 for line in lines if 'ERROR' in line.upper())
        
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
        
        content = ''.join(lines)
        
        return {
            "status": "success",
            "data": {
                "content": content,
                "stats": {
                    "size": file_size,
                    "lines": len(lines) if not level and not search else len(lines),
                    "modified": file_modified * 1000,  # 转换为毫秒
                    "errors": error_count
                }
            }
        }
    except Exception as e:
        logger.error(f"获取日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取日志失败: {str(e)}")


@router.post("/logs/clear")
async def clear_logs(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """清空日志文件"""
    try:
        # 从配置文件中获取日志文件路径
        log_file_path = config_manager.get_config('dev', 'log_file', fallback='./log.txt')
        
        # 处理相对路径
        if log_file_path.startswith('./'):
            cwd = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            log_file_path = os.path.join(cwd, log_file_path.split('./', 1)[1])
        
        if os.path.exists(log_file_path):
            # 清空日志文件
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write('')
        
        return {"status": "success", "message": "日志清空成功"}
    except Exception as e:
        logger.error(f"清空日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}") 