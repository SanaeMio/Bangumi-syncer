"""Plex Webhook 数据模型"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class PlexWebhookData(BaseModel):
    """Plex Webhook数据模型"""

    # 允许额外字段
    model_config = {"extra": "allow"}

    event: str = Field(
        ..., description="事件类型", json_schema_extra={"example": "media.scrobble"}
    )
    Account: dict[str, Any] = Field(
        ...,
        description="账户信息",
        json_schema_extra={"example": {"title": "用户名"}},
    )
    Metadata: dict[str, Any] = Field(
        ...,
        description="媒体元数据",
        json_schema_extra={
            "example": {
                "type": "episode",
                "title": "第01话",
                "grandparentTitle": "番剧名称",
                "originalTitle": "Original Title",
                "parentIndex": 1,
                "index": 1,
                "originallyAvailableAt": "2024-01-01",
            }
        },
    )

    # 可选字段
    user: Optional[bool] = Field(None, description="是否为用户事件")
    owner: Optional[bool] = Field(None, description="是否为所有者事件")
    Server: Optional[dict[str, Any]] = Field(None, description="服务器信息")
    Player: Optional[dict[str, Any]] = Field(None, description="播放器信息")
