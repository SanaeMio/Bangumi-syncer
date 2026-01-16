"""
配置相关数据模型
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class BangumiConfig(BaseModel):
    """Bangumi配置模型"""

    username: str = Field("", description="用户名")
    access_token: str = Field("", description="访问令牌")
    private: bool = Field(False, description="是否私有")


class SyncConfig(BaseModel):
    """同步配置模型"""

    mode: str = Field("single", description="同步模式")
    single_username: str = Field("", description="单用户模式用户名")
    blocked_keywords: str = Field("", description="屏蔽关键词")


class DevConfig(BaseModel):
    """开发配置模型"""

    script_proxy: str = Field("", description="脚本代理")
    debug: bool = Field(False, description="调试模式")


class BangumiDataConfig(BaseModel):
    """Bangumi数据配置模型"""

    enabled: bool = Field(True, description="是否启用")
    use_cache: bool = Field(True, description="是否使用缓存")
    cache_ttl_days: int = Field(7, description="缓存有效期（天）")
    data_url: str = Field(
        "https://unpkg.com/bangumi-data@0.3/dist/data.json", description="数据URL"
    )
    local_cache_path: str = Field(
        "./bangumi_data_cache.json", description="本地缓存路径"
    )


class AuthConfig(BaseModel):
    """认证配置模型"""

    enabled: bool = Field(True, description="是否启用认证")
    username: str = Field("admin", description="管理员用户名")
    session_timeout: int = Field(3600, description="会话超时时间（秒）")
    https_only: bool = Field(False, description="是否仅HTTPS")
    max_login_attempts: int = Field(5, description="最大登录尝试次数")
    lockout_duration: int = Field(900, description="锁定时间（秒）")


class ConfigData(BaseModel):
    """配置数据模型"""

    bangumi: BangumiConfig
    sync: SyncConfig
    dev: DevConfig
    bangumi_data: BangumiDataConfig
    auth: AuthConfig
    multi_accounts: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="多账号配置"
    )


class ConfigResponse(BaseModel):
    """配置响应模型"""

    status: str = Field(..., description="状态")
    data: ConfigData = Field(..., description="配置数据")


class ConfigUpdateRequest(BaseModel):
    """配置更新请求模型"""

    bangumi: Optional[BangumiConfig] = None
    sync: Optional[SyncConfig] = None
    dev: Optional[DevConfig] = None
    bangumi_data: Optional[BangumiDataConfig] = None
    auth: Optional[AuthConfig] = None
    multi_accounts: Optional[dict[str, dict[str, Any]]] = None


class ConfigUpdateResponse(BaseModel):
    """配置更新响应模型"""

    status: str = Field(..., description="状态")
    message: str = Field(..., description="消息")
