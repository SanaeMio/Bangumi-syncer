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


# ======================================================================
# 屏蔽关键词（统一黑名单）
#
# 合并了历史 [sync] blocked_keywords（INI）与 title_blacklist（DB subject_id）
# 两套机制：现全部存 DB、按标题关键词判定、匹配前生效，由本组接口管理。
# 自定义映射优先级高于屏蔽关键词（命中映射时即使含屏蔽词也照常同步）。
# ======================================================================


@router.get("/blocked-keywords")
async def get_blocked_keywords(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """获取全部屏蔽关键词（含来源：manual 手填 / reject 拒绝候选时自动记录）"""
    try:
        from ..core.database import database_manager

        return {
            "status": "success",
            "data": {"keywords": database_manager.list_blocked_keywords()},
        }
    except Exception as e:
        logger.error(f"获取屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取屏蔽关键词失败: {str(e)}")


@router.post("/blocked-keywords")
async def add_blocked_keyword(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """新增屏蔽关键词（幂等：已存在不报错，返回 added=False）"""
    try:
        from ..core.database import database_manager

        data = await request.json()
        keyword = (data.get("keyword") or "").strip()
        if not keyword:
            raise HTTPException(status_code=400, detail="关键词不能为空")

        added = database_manager.add_blocked_keyword(keyword, source="manual")
        return {
            "status": "success",
            "data": {"added": bool(added), "keyword": keyword},
            "message": "已添加" if added else "该关键词已存在",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"新增屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"新增屏蔽关键词失败: {str(e)}")


@router.delete("/blocked-keywords/{keyword}")
async def delete_blocked_keyword(
    keyword: str,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """删除单个屏蔽关键词"""
    try:
        from ..core.database import database_manager

        if not database_manager.remove_blocked_keyword(keyword):
            raise HTTPException(status_code=404, detail="关键词不存在")
        return {"status": "success", "message": "已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除屏蔽关键词失败: {str(e)}")


@router.delete("/blocked-keywords")
async def clear_blocked_keywords(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """清空全部屏蔽关键词（批量保存时的整体覆盖入口）"""
    try:
        from ..core.database import database_manager

        removed = database_manager.clear_blocked_keywords()
        return {
            "status": "success",
            "data": {"removed": removed},
            "message": f"已清空 {removed} 条",
        }
    except Exception as e:
        logger.error(f"清空屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空屏蔽关键词失败: {str(e)}")


@router.put("/blocked-keywords")
async def replace_blocked_keywords(
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """整体覆盖屏蔽关键词（清空后批量写入，供配置页一次保存）

    ``source='reject'`` 的记录（用户拒绝候选时自动记录的）**会被保留** ——
    它们不是用户在配置页里手写的，整体覆盖不应误删。
    """
    try:
        from ..core.database import database_manager

        data = await request.json()
        keywords = data.get("keywords") or []
        if not isinstance(keywords, list):
            raise HTTPException(status_code=400, detail="keywords 必须是数组")

        # 只清理手动添加的，保留 reject 自动记录的
        existing = database_manager.list_blocked_keywords()
        for item in existing:
            if item.get("source") != "reject":
                database_manager.remove_blocked_keyword(item.get("keyword", ""))

        added = database_manager.bulk_add_blocked_keywords(
            [str(k).strip() for k in keywords if str(k).strip()],
            source="manual",
        )
        return {
            "status": "success",
            "data": {"added": added},
            "message": "屏蔽关键词已保存",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存屏蔽关键词失败: {str(e)}")
