"""
依赖注入模块
"""
from typing import Optional
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..core.security import security_manager
from ..core.config import config_manager


# Bearer token认证
security = HTTPBearer(auto_error=False)


def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """获取当前用户（用于依赖注入）"""
    auth_config = security_manager.get_auth_config()
    
    # 如果认证被禁用，直接通过
    if not auth_config['enabled']:
        return {'username': 'admin', 'auth_disabled': True}
    
    # 清理过期会话
    security_manager.cleanup_expired_sessions()
    
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    session = security_manager.validate_session(credentials.credentials)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return session


def get_current_user_from_cookie(request: Request):
    """从Cookie获取当前用户（用于Web页面）"""
    auth_config = security_manager.get_auth_config()
    
    # 如果认证被禁用，直接通过
    if not auth_config['enabled']:
        return {'username': 'admin', 'auth_disabled': True}
    
    # 清理过期会话
    security_manager.cleanup_expired_sessions()
    
    token = request.cookies.get('session_token')
    if not token:
        return None
    
    session = security_manager.validate_session(token)
    return session


async def get_current_user_flexible(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """灵活的用户认证（支持Cookie和Bearer token）"""
    auth_config = security_manager.get_auth_config()
    
    # 如果认证被禁用，直接通过
    if not auth_config['enabled']:
        return {'username': 'admin', 'auth_disabled': True}
    
    # 清理过期会话
    security_manager.cleanup_expired_sessions()
    
    # 首先尝试从Cookie获取（Web界面）
    token = request.cookies.get('session_token')
    if token:
        session = security_manager.validate_session(token)
        if session:
            return session
    
    # 然后尝试从Bearer token获取（API调用）
    if credentials:
        session = security_manager.validate_session(credentials.credentials)
        if session:
            return session
    
    # 如果都没有有效的认证信息
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未提供有效的认证信息",
        headers={"WWW-Authenticate": "Bearer"},
    ) 