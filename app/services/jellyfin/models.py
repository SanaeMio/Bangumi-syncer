"""Jellyfin Webhook 数据模型"""

from typing import Optional

from pydantic import BaseModel, Field


class JellyfinWebhookData(BaseModel):
    """Jellyfin Webhook数据模型"""

    # 允许额外字段
    model_config = {"extra": "allow"}

    NotificationType: str = Field(..., description="通知类型", example="PlaybackStop")
    PlayedToCompletion: str = Field(..., description="是否播放完成", example="True")
    media_type: str = Field(..., description="媒体类型", example="episode")
    title: str = Field(..., description="番剧标题", example="番剧名称")
    ori_title: str = Field(..., description="原始标题", example="Original Title")
    season: int = Field(..., description="季数", example=1)
    episode: int = Field(..., description="集数", example=1)
    user_name: str = Field(..., description="用户名", example="用户名")
    release_date: Optional[str] = Field(
        None, description="发行日期", example="2024-01-01"
    )
