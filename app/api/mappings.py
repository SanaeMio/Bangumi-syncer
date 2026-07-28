"""映射相关API"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.logging import logger
from ..services.mapping_service import mapping_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api", tags=["mappings"])


@router.get("/mappings")
async def get_custom_mappings(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """获取自定义映射（含正则规则）"""
    try:
        mappings = mapping_service.get_all_mappings()
        rules = mapping_service.get_all_rules()
        return {
            "status": "success",
            "data": {"mappings": mappings, "rules": rules},
        }
    except Exception as e:
        logger.error(f"获取自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取自定义映射失败: {str(e)}")


@router.post("/mappings")
async def update_custom_mappings(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """更新自定义映射（支持附带 rules）"""
    try:
        data = await request.json()
        mappings = data.get("mappings", {})
        rules = data.get("rules")  # None 表示保留现有 rules

        # 更新映射（rules=None 时保留现有）
        mapping_service.update_custom_mappings(mappings, rules=rules)

        return {"status": "success", "message": "映射更新成功"}
    except Exception as e:
        logger.error(f"更新自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新自定义映射失败: {str(e)}")


@router.delete("/mappings/{title}")
async def delete_custom_mapping(
    title: str,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """删除单个自定义映射"""
    try:
        # 检查映射是否存在（保留 404 语义）
        if title not in mapping_service.get_all_mappings():
            raise HTTPException(status_code=404, detail="映射不存在")

        # 删除并写回（读全量→删除→写回由 service 层封装）
        if not mapping_service.delete_single_mapping(title):
            raise HTTPException(status_code=500, detail="删除映射失败")

        return {"status": "success", "message": "映射删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除自定义映射失败: {str(e)}")
