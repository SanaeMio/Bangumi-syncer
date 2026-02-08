"""
Trakt.tv 相关数据模型
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TraktConfig(BaseModel):
    """Trakt 配置模型（Pydantic）"""

    user_id: str = Field(..., description="用户ID", min_length=1)
    access_token: str = Field(..., description="访问令牌", min_length=1)
    refresh_token: Optional[str] = Field(None, description="刷新令牌")
    expires_at: Optional[int] = Field(None, description="令牌过期时间戳")
    enabled: bool = Field(True, description="是否启用 Trakt 同步")
    sync_interval: str = Field("0 */6 * * *", description="同步间隔 (Cron 表达式)")
    last_sync_time: Optional[int] = Field(None, description="最后同步时间戳")
    created_at: int = Field(
        default_factory=lambda: int(datetime.now().timestamp()),
        description="创建时间戳",
    )
    updated_at: int = Field(
        default_factory=lambda: int(datetime.now().timestamp()),
        description="更新时间戳",
    )

    def is_token_expired(self) -> bool:
        """检查访问令牌是否已过期"""
        if not self.expires_at:
            return True
        return datetime.now().timestamp() > self.expires_at

    def refresh_if_needed(self) -> bool:
        """检查是否需要刷新令牌"""
        if not self.expires_at:
            return True
        # 在令牌过期前5分钟就认为需要刷新
        return datetime.now().timestamp() > (self.expires_at - 300)

    def to_dict(self) -> dict:
        """转换为字典，用于数据库存储"""
        return {
            "user_id": self.user_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "enabled": 1 if self.enabled else 0,
            "sync_interval": self.sync_interval,
            "last_sync_time": self.last_sync_time,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Optional["TraktConfig"]:
        """从字典创建实例，用于从数据库加载"""
        if data is None:
            return None

        # 转换数据库中的布尔值
        enabled = bool(data.get("enabled")) if data.get("enabled") is not None else True

        return cls(
            user_id=data["user_id"],
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            enabled=enabled,
            sync_interval=data.get("sync_interval", "0 */6 * * *"),
            last_sync_time=data.get("last_sync_time"),
            created_at=data.get("created_at", int(datetime.now().timestamp())),
            updated_at=data.get("updated_at", int(datetime.now().timestamp())),
        )


class TraktSyncHistory(BaseModel):
    """Trakt 同步历史模型（Pydantic）"""

    user_id: str = Field(..., description="用户ID", min_length=1)
    trakt_item_id: str = Field(..., description="Trakt 条目ID", min_length=1)
    media_type: str = Field(..., description="媒体类型", pattern="^(movie|episode)$")
    watched_at: int = Field(..., description="观看时间戳")
    synced_at: int = Field(
        default_factory=lambda: int(datetime.now().timestamp()),
        description="同步时间戳",
    )

    def to_dict(self) -> dict:
        """转换为字典，用于数据库存储"""
        return {
            "user_id": self.user_id,
            "trakt_item_id": self.trakt_item_id,
            "media_type": self.media_type,
            "watched_at": self.watched_at,
            "synced_at": self.synced_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Optional["TraktSyncHistory"]:
        """从字典创建实例，用于从数据库加载"""
        if data is None:
            return None

        return cls(
            user_id=data["user_id"],
            trakt_item_id=data["trakt_item_id"],
            media_type=data["media_type"],
            watched_at=data["watched_at"],
            synced_at=data.get("synced_at", int(datetime.now().timestamp())),
        )


class TraktAuthRequest(BaseModel):
    """Trakt 授权请求模型"""

    user_id: str = Field(..., description="用户ID")


class TraktAuthResponse(BaseModel):
    """Trakt 授权响应模型"""

    auth_url: str = Field(..., description="授权URL")
    state: str = Field(..., description="状态参数")


class TraktCallbackRequest(BaseModel):
    """Trakt 回调请求模型"""

    code: str = Field(..., description="授权码")
    state: str = Field(..., description="状态参数")


class TraktCallbackResponse(BaseModel):
    """Trakt 回调响应模型"""

    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")


class TraktConfigResponse(BaseModel):
    """Trakt 配置响应模型"""

    user_id: str = Field(..., description="用户ID")
    enabled: bool = Field(..., description="是否启用")
    sync_interval: str = Field(..., description="同步间隔")
    last_sync_time: Optional[int] = Field(None, description="最后同步时间")
    is_connected: bool = Field(..., description="是否已连接 Trakt")
    token_expires_at: Optional[int] = Field(None, description="令牌过期时间")
    client_id: str = Field("", description="Trakt Client ID")
    client_secret: str = Field("", description="Trakt Client Secret")
    redirect_uri: str = Field("", description="OAuth 回调 URL")


class TraktConfigUpdateRequest(BaseModel):
    """Trakt 配置更新请求模型"""

    enabled: Optional[bool] = Field(None, description="是否启用")
    sync_interval: Optional[str] = Field(None, description="同步间隔")


class TraktApiConfigUpdateRequest(BaseModel):
    """Trakt API 配置更新请求模型"""

    client_id: Optional[str] = Field(None, description="Trakt Client ID")
    client_secret: Optional[str] = Field(None, description="Trakt Client Secret")
    redirect_uri: Optional[str] = Field(None, description="OAuth 回调 URL")


class TraktSyncStatusResponse(BaseModel):
    """Trakt 同步状态响应模型"""

    is_running: bool = Field(..., description="是否正在运行")
    last_sync_time: Optional[int] = Field(None, description="最后同步时间")
    next_sync_time: Optional[int] = Field(None, description="下次同步时间")
    success_count: int = Field(0, description="成功同步数量")
    error_count: int = Field(0, description="失败同步数量")
    total_count: int = Field(0, description="总同步数量")


class TraktManualSyncRequest(BaseModel):
    """Trakt 手动同步请求模型"""

    user_id: str = Field(..., description="用户ID")
    full_sync: bool = Field(False, description="是否全量同步")


class TraktManualSyncResponse(BaseModel):
    """Trakt 手动同步响应模型"""

    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")
    job_id: Optional[str] = Field(None, description="任务ID")
