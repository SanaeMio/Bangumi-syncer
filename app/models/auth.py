"""
认证相关数据模型
"""
from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """登录请求模型"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class LoginResponse(BaseModel):
    """登录响应模型"""
    status: str = Field(..., description="状态")
    message: str = Field(..., description="消息")
    data: Optional[dict] = Field(None, description="数据")


class AuthStatus(BaseModel):
    """认证状态模型"""
    authenticated: bool = Field(..., description="是否已认证")
    username: Optional[str] = Field(None, description="用户名")
    auth_enabled: bool = Field(..., description="是否启用认证")
    session_timeout: int = Field(..., description="会话超时时间")


class LogoutResponse(BaseModel):
    """登出响应模型"""
    status: str = Field(..., description="状态")
    message: str = Field(..., description="消息") 