"""Emby Webhook 数据模型"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class EmbyWebhookData(BaseModel):
    """Emby通知数据模型"""

    # 使用model_config来允许额外字段
    model_config = {"extra": "allow"}

    # 核心必需字段
    Event: str = Field(
        ..., description="事件类型", json_schema_extra={"example": "item.markplayed"}
    )
    User: dict[str, Any] = Field(
        ...,
        description="用户信息",
        json_schema_extra={"example": {"Name": "用户名", "Id": "user-id"}},
    )
    Item: dict[str, Any] = Field(
        ...,
        description="媒体项目信息",
        json_schema_extra={
            "example": {
                "Type": "Episode",
                "SeriesName": "番剧名称",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "PremiereDate": "2024-01-01T00:00:00.0000000Z",
                "Name": "剧集名称",
            }
        },
    )

    # 可选字段
    Title: Optional[str] = Field(None, description="通知标题")
    Description: Optional[str] = Field(None, description="通知描述")
    Date: Optional[str] = Field(None, description="通知日期")
    Server: Optional[dict[str, Any]] = Field(None, description="服务器信息")
    PlaybackInfo: Optional[dict[str, Any]] = Field(
        None,
        description="播放信息",
        json_schema_extra={"example": {"PlayedToCompletion": True}},
    )
