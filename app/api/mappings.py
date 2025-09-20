"""
映射相关API
"""
import json
from typing import Dict, Any
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

from ..services.mapping_service import mapping_service
from ..core.logging import logger
from .deps import get_current_user_flexible


router = APIRouter(prefix="/api", tags=["mappings"])


@router.get("/mappings")
async def get_custom_mappings(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """获取自定义映射"""
    try:
        mappings = mapping_service.get_all_mappings()
        return {
            "status": "success",
            "data": {
                "mappings": mappings
            }
        }
    except Exception as e:
        logger.error(f"获取自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取自定义映射失败: {str(e)}")


@router.post("/mappings")
async def update_custom_mappings(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """更新自定义映射"""
    try:
        data = await request.json()
        mappings = data.get("mappings", {})
        
        # 更新映射
        mapping_service.update_mappings(mappings)
        
        return {"status": "success", "message": "映射更新成功"}
    except Exception as e:
        logger.error(f"更新自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新自定义映射失败: {str(e)}")


@router.delete("/mappings/{title}")
async def delete_custom_mapping(title: str, request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """删除单个自定义映射"""
    try:
        # 获取当前所有映射
        mappings = mapping_service.get_all_mappings()
        
        # 检查映射是否存在
        if title not in mappings:
            raise HTTPException(status_code=404, detail="映射不存在")
        
        # 删除指定映射
        del mappings[title]
        
        # 更新映射
        mapping_service.update_mappings(mappings)
        
        return {"status": "success", "message": "映射删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除自定义映射失败: {str(e)}") 