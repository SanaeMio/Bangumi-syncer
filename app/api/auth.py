"""
认证相关API
"""
import datetime
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import JSONResponse

from ..core.security import security_manager
from ..core.logging import logger
from .deps import get_current_user_flexible


router = APIRouter(prefix="/api", tags=["auth"])


def get_client_ip(request: Request) -> str:
    """获取客户端IP地址"""
    # 尝试从X-Forwarded-For获取（代理环境）
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    
    # 尝试从X-Real-IP获取
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip
    
    # 使用直接连接的IP
    return request.client.host if request.client else 'unknown'


@router.post("/login")
async def login(request: Request, response: Response):
    """用户登录"""
    try:
        data = await request.json()
        username = data.get("username")
        password = data.get("password")
        
        if not username or not password:
            raise HTTPException(status_code=400, detail="用户名和密码不能为空")
        
        # 获取客户端IP
        client_ip = get_client_ip(request)
        
        # 检查IP是否被锁定
        if security_manager.is_ip_locked(client_ip):
            lockout_info = security_manager.get_lockout_info(client_ip)
            lockout_time = lockout_info.get('locked_until', 0)
            lockout_str = datetime.datetime.fromtimestamp(lockout_time).strftime('%Y-%m-%d %H:%M:%S')
            logger.warning(f'IP {client_ip} 被锁定，拒绝登录请求')
            raise HTTPException(
                status_code=423,
                detail=f"IP已被锁定，请于 {lockout_str} 后重试"
            )
        
        # 验证用户凭据
        if security_manager.authenticate_user(username, password):
            # 登录成功，清除失败记录
            security_manager.reset_login_attempts(client_ip)
            
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
            
            logger.info(f'用户 {username} 登录成功，IP: {client_ip}')
            return {"status": "success", "message": "登录成功"}
        else:
            # 登录失败，记录失败次数
            security_manager.record_login_failure(client_ip)
            
            # 检查是否被锁定
            if security_manager.is_ip_locked(client_ip):
                lockout_info = security_manager.get_lockout_info(client_ip)
                lockout_time = lockout_info.get('locked_until', 0)
                lockout_str = datetime.datetime.fromtimestamp(lockout_time).strftime('%Y-%m-%d %H:%M:%S')
                logger.warning(f'IP {client_ip} 因登录失败次数过多被锁定')
                raise HTTPException(
                    status_code=423,
                    detail=f"登录失败次数过多，IP已被锁定，请于 {lockout_str} 后重试"
                )
            else:
                attempts_info = security_manager.get_login_attempts(client_ip)
                attempts = attempts_info.get('attempts', 0)
                auth_config = security_manager.get_auth_config()
                max_attempts = auth_config.get('max_login_attempts', 5)
                remaining_attempts = max_attempts - attempts
                logger.warning(f'用户 {username} 登录失败，IP: {client_ip}，剩余尝试次数: {remaining_attempts}')
                raise HTTPException(
                    status_code=401, 
                    detail=f"用户名或密码错误（剩余尝试次数: {remaining_attempts}）"
                )
            
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