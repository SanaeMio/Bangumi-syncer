"""
同步相关数据模型
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class CustomItem(BaseModel):
    """自定义同步项目模型"""

    media_type: str = Field(
        "episode",
        description="媒体类型 episode 或 movie；省略时按剧集处理（兼容旧版自定义 Webhook）",
    )
    title: str = Field(..., description="番剧标题")
    ori_title: Optional[str] = Field(None, description="原始标题")
    season: int = Field(..., description="季度")
    episode: int = Field(..., description="集数")
    release_date: str = Field(..., description="发行日期")
    user_name: str = Field(..., description="用户名")
    source: Optional[str] = Field(None, description="来源")
    sync_action: Optional[str] = Field(
        None,
        description='可选：如 "mark_watching" 时仅将剧场版条目标为在看（用于 Tautulli 等 /Custom）',
    )
    raw_payload: Optional[dict[str, Any]] = Field(
        None,
        description="驱动获取到的原始数据（webhook payload / /media response 等），"
        "用于在同步记录详情的「接收请求」步骤展示驱动原始输入",
    )


class SyncResponse(BaseModel):
    """同步响应模型"""

    status: str = Field(..., description="状态")
    message: str = Field(..., description="消息")
    data: Optional[dict] = Field(None, description="数据")


# ===== Webhook 数据模型已迁移至各驱动子包，此处重新导出以向后兼容 =====
from ..services.emby.models import EmbyWebhookData  # noqa: E402, F401
from ..services.jellyfin.models import JellyfinWebhookData  # noqa: E402, F401
from ..services.plex.models import PlexWebhookData  # noqa: E402, F401


class SyncRecord(BaseModel):
    """同步记录模型"""

    id: int
    timestamp: str
    user_name: str
    title: str
    ori_title: Optional[str]
    season: int
    episode: int
    subject_id: Optional[str]
    episode_id: Optional[str]
    status: str
    message: str
    source: str
    media_type: str = "episode"


class SyncStats(BaseModel):
    """同步统计模型"""

    total_syncs: int
    success_syncs: int
    error_syncs: int
    today_syncs: int
    success_rate: float
    user_stats: list
    daily_stats: list


class HeatmapDay(BaseModel):
    """热力图单日数据"""

    date: str
    count: int


class TestSyncRequest(BaseModel):
    """测试同步请求模型"""

    title: str = Field(..., description="番剧标题")
    ori_title: Optional[str] = Field(None, description="原始标题")
    season: int = Field(1, description="季度")
    episode: int = Field(1, description="集数")
    release_date: Optional[str] = Field(None, description="发行日期")
    user_name: str = Field("test_user", description="用户名")
    source: str = Field("test", description="来源")
    media_type: str = Field("episode", description="媒体类型 episode 或 movie")
