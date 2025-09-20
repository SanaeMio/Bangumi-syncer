"""
代理配置API
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from ..core.logging import logger
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api")


class ProxyTestRequest(BaseModel):
    proxy_url: str


@router.get("/proxy/suggestions")
async def get_proxy_suggestions(
    request: Request,
    port: Optional[int] = 7890,
    current_user: dict = Depends(get_current_user_flexible)
):
    """获取代理配置建议"""
    try:
        # 导入docker_helper
        from ..utils.docker_helper import docker_helper
        
        # 获取环境信息
        env_info = docker_helper.get_environment_info()
        
        # 获取代理建议
        suggestions = docker_helper.get_proxy_suggestions(port)
        
        return {
            "status": "success",
            "data": {
                "environment": env_info,
                "suggestions": suggestions
            }
        }
    except Exception as e:
        logger.error(f"获取代理建议失败: {e}")
        return {
            "status": "error",
            "message": f"获取代理建议失败: {str(e)}"
        }


@router.post("/proxy/test")
async def test_proxy_connectivity(
    request: ProxyTestRequest,
    current_user: dict = Depends(get_current_user_flexible)
):
    """测试代理连通性"""
    try:
        # 导入docker_helper
        from ..utils.docker_helper import docker_helper
        
        # 测试代理连通性
        result = docker_helper.test_proxy_connectivity(request.proxy_url)
        
        return {
            "status": "success",
            "data": result
        }
    except Exception as e:
        logger.error(f"代理测试失败: {e}")
        return {
            "status": "error",
            "message": f"代理测试失败: {str(e)}"
        }


@router.get("/proxy/environment")
async def get_environment_info(
    request: Request,
    current_user: dict = Depends(get_current_user_flexible)
):
    """获取环境信息"""
    try:
        # 导入docker_helper
        from ..utils.docker_helper import docker_helper
        
        env_info = docker_helper.get_environment_info()
        
        return {
            "status": "success",
            "data": env_info
        }
    except Exception as e:
        logger.error(f"获取环境信息失败: {e}")
        return {
            "status": "error",
            "message": f"获取环境信息失败: {str(e)}"
        }


class HostConnectivityRequest(BaseModel):
    host: str
    port: int = 80
    timeout: int = 5


@router.post("/proxy/test-host")
async def test_host_connectivity(
    request: HostConnectivityRequest,
    current_user: dict = Depends(get_current_user_flexible)
):
    """测试到指定主机的连通性"""
    try:
        from ..utils.docker_helper import docker_helper
        result = docker_helper.test_host_connectivity(
            request.host, 
            request.port, 
            request.timeout
        )
        return {
            "status": "success",
            "data": result
        }
    except Exception as e:
        logger.error(f"主机连通性测试失败: {e}")
        return {
            "status": "error",
            "message": f"主机连通性测试失败: {str(e)}"
        }
