"""
健康检查API
"""

from fastapi import APIRouter

from ..core.app_version import get_version

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "healthy", "version": get_version()}
