"""
健康检查API
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "healthy", "version": "2.0.0"}
