"""
认证相关API
"""
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import JSONResponse

from ..core.security import security_manager
from ..core.logging import logger
from .deps import get_current_user_flexible


router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login")
async def login(request: Request, response: Response):
    """用户登录"""
    try:
        data = await request.json()
        username = data.get("username")
        password = data.get("password")
        
        if not username or not password:
            raise HTTPException(status_code=400, detail="用户名和密码不能为空")
        
        # 验证用户凭据
        if security_manager.authenticate_user(username, password):
            # 创建会话
            session_token = security_manager.create_session(username)
            
            # 设置cookie
            response.set_cookie(
                key="session_token",
                value=session_token,
                httponly=True,
                max_age=3600,  # 1小时
                samesite="lax"
            )
            
            return {"status": "success", "message": "登录成功"}
        else:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"登录失败: {e}")
        raise HTTPException(status_code=500, detail="登录失败")


@router.post("/logout")
async def logout(request: Request, response: Response):
    """用户登出"""
    try:
        # 获取当前会话token
        token = request.cookies.get('session_token')
        if token:
            # 删除会话
            security_manager.remove_session(token)
        
        # 清除cookie
        response.delete_cookie("session_token")
        
        return {"status": "success", "message": "登出成功"}
        
    except Exception as e:
        logger.error(f"登出失败: {e}")
        raise HTTPException(status_code=500, detail="登出失败")


@router.get("/auth/status")
async def auth_status(request: Request, current_user: dict = Depends(get_current_user_flexible)):
    """获取认证状态"""
    if current_user:
        return {
            "status": "success",
            "data": {
                "authenticated": True,
                "user": current_user
            }
        }
    else:
        return {
            "status": "success",
            "data": {
                "authenticated": False,
                "user": None
            }
        } 