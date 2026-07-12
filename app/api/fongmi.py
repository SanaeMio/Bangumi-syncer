"""fongmi 局域网轮询同步 API"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.config import config_manager
from ..core.logging import logger
from ..services.fongmi.sync_service import FongmiSyncResult, fongmi_sync_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/fongmi", tags=["fongmi"])


class DebugSyncRequest(BaseModel):
    """调试单设备同步请求"""

    device_ip: str
    device_port: int = 9978
    device_name: str = ""


@router.get("/status")
async def fongmi_status(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    cfg = config_manager.get_fongmi_config()
    return {
        "status": "success",
        "data": {
            "enabled": bool(cfg.get("enabled")),
            "devices": cfg.get("devices"),
            "subnet": cfg.get("subnet"),
            "auto_scan": cfg.get("auto_scan"),
            "sync_interval": cfg.get("sync_interval"),
            "min_percent": cfg.get("min_percent"),
        },
    }


@router.post("/sync/manual")
async def fongmi_manual_sync(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    cfg = config_manager.get_fongmi_config()
    if not cfg.get("enabled"):
        raise HTTPException(status_code=400, detail="fongmi 同步未启用，请在配置中开启")

    try:
        result: FongmiSyncResult = await fongmi_sync_service.run_sync(
            ignore_enabled=False
        )
        return {
            "status": "success" if result.success else "error",
            "message": result.message,
            "data": {
                "synced_count": result.synced_count,
                "skipped_count": result.skipped_count,
                "error_count": result.error_count,
                "discovered_devices": result.discovered_devices,
            },
        }
    except Exception as e:
        logger.error(f"fongmi 手动同步失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/debug/scan")
async def fongmi_debug_scan(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """调试用：搜寻所有配置的设备并拉取当前 /media 状态（不过滤完成进度）。

    用于在调试工具中验证设备发现、/media 拉取、集数解析是否正常。
    服务端 20 秒超时，避免网段扫描无响应时长时间挂起。
    """
    try:
        data = await asyncio.wait_for(fongmi_sync_service.debug_scan(), timeout=20.0)
        return {
            "status": "success",
            "message": f"发现 {data['discovered_devices']} 台设备",
            "data": data,
        }
    except asyncio.TimeoutError:
        logger.warning("fongmi 调试扫描超时（20s）")
        raise HTTPException(
            status_code=504,
            detail="扫描超时（20秒），请检查网段配置是否过大或设备是否在线",
        ) from None
    except Exception as e:
        logger.error(f"fongmi 调试扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/debug/sync")
async def fongmi_debug_sync_one(
    req: DebugSyncRequest,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """调试用：对指定设备当前播放内容执行一次 Bangumi 同步，返回前后对比。

    会实际调用 Bangumi API 标记看过（幂等），用于调试匹配是否正确。
    服务端 30 秒超时（Bangumi API 可能较慢）。
    """
    try:
        data = await asyncio.wait_for(
            fongmi_sync_service.debug_sync_one(
                device_ip=req.device_ip,
                device_port=req.device_port,
                device_name=req.device_name,
            ),
            timeout=30.0,
        )
        return {
            "status": "success",
            "message": data.get("after", {}).get("message", ""),
            "data": data,
        }
    except asyncio.TimeoutError:
        logger.warning(f"fongmi 调试同步超时（30s）：{req.device_ip}")
        raise HTTPException(
            status_code=504,
            detail="同步超时（30秒），Bangumi API 响应过慢或网络异常",
        ) from None
    except Exception as e:
        logger.error(f"fongmi 调试同步失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
