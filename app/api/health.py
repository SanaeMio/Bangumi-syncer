"""
健康检查API
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..core.app_version import get_version

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """健康检查接口"""
    return {"status": "healthy", "version": get_version()}
