"""飞牛影视 trimmedia 同步 API"""

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.config import config_manager
from ..core.logging import logger
from ..services.feiniu.reader import list_feiniu_users
from ..services.feiniu.sync_service import FeiniuSyncResult, feiniu_sync_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/feiniu", tags=["feiniu"])


@router.get("/status")
async def feiniu_status(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    cfg = config_manager.get_feiniu_config()
    dbp = (cfg.get("db_path") or "").strip()
    db_ok = bool(dbp) and Path(dbp).is_file()
    return {
        "status": "success",
        "data": {
            "enabled": bool(cfg.get("enabled")),
            "db_path": dbp,
            "db_ok": db_ok,
            "user_filter": cfg.get("user_filter"),
            "time_range": cfg.get("time_range"),
            "sync_interval": cfg.get("sync_interval"),
            "min_percent": cfg.get("min_percent"),
            "limit": cfg.get("limit"),
        },
    }


@router.get("/users")
async def feiniu_users(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    cfg = config_manager.get_feiniu_config()
    dbp = (cfg.get("db_path") or "").strip()
    if not dbp or not Path(dbp).is_file():
        return {
            "status": "success",
            "data": {"users": [], "message": "数据库未配置或不存在"},
        }
    users = list_feiniu_users(dbp)
    return {
        "status": "success",
        "data": {
            "users": [{"id": u.guid, "name": u.username} for u in users],
        },
    }


@router.post("/sync/manual")
async def feiniu_manual_sync(
    user: Optional[str] = Query(
        default=None,
        description="飞牛用户 guid；不传则使用配置中的 user_filter",
    ),
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    cfg = config_manager.get_feiniu_config()
    if not cfg.get("enabled"):
        raise HTTPException(status_code=400, detail="飞牛同步未启用，请在配置中开启")
    dbp = (cfg.get("db_path") or "").strip()
    if not dbp or not Path(dbp).is_file():
        raise HTTPException(status_code=400, detail="数据库路径无效或文件不存在")

    uf = user
    try:
        result: FeiniuSyncResult = await feiniu_sync_service.run_sync(
            user_filter=uf,
            ignore_enabled=False,
        )
        return {
            "status": "success" if result.success else "error",
            "message": result.message,
            "data": {
                "synced_count": result.synced_count,
                "skipped_count": result.skipped_count,
                "error_count": result.error_count,
            },
        }
    except Exception as e:
        logger.error(f"飞牛手动同步失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
