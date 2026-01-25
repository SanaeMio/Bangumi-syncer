"""
同步相关数据模型
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class CustomItem(BaseModel):
    """自定义同步项目模型"""

    media_type: str = Field(..., description="媒体类型")
    title: str = Field(..., description="番剧标题")
    ori_title: Optional[str] = Field(None, description="原始标题")
    season: int = Field(..., description="季度")
    episode: int = Field(..., description="集数")
    release_date: str = Field(..., description="发行日期")
    user_name: str = Field(..., description="用户名")
    source: Optional[str] = Field(None, description="来源")


class SyncResponse(BaseModel):
    """同步响应模型"""

    status: str = Field(..., description="状态")
    message: str = Field(..., description="消息")
    data: Optional[dict] = Field(None, description="数据")


class PlexWebhookData(BaseModel):
    """Plex Webhook数据模型"""

    # 允许额外字段
    model_config = {"extra": "allow"}

    event: str = Field(
        ..., description="事件类型", json_schema_extra={"example": "media.scrobble"}
    )
    Account: dict[str, Any] = Field(
        ..., description="账户信息", json_schema_extra={"example": {"title": "用户名"}}
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


class JellyfinWebhookData(BaseModel):
    """Jellyfin Webhook数据模型"""

    # 允许额外字段
    model_config = {"extra": "allow"}

    NotificationType: str = Field(
        ..., description="通知类型", json_schema_extra={"example": "PlaybackStop"}
    )
    PlayedToCompletion: str = Field(
        ..., description="是否播放完成", json_schema_extra={"example": "True"}
    )
    media_type: str = Field(
        ..., description="媒体类型", json_schema_extra={"example": "episode"}
    )
    title: str = Field(
        ..., description="番剧标题", json_schema_extra={"example": "番剧名称"}
    )
    ori_title: str = Field(
        ..., description="原始标题", json_schema_extra={"example": "Original Title"}
    )
    season: int = Field(..., description="季数", json_schema_extra={"example": 1})
    episode: int = Field(..., description="集数", json_schema_extra={"example": 1})
    user_name: str = Field(
        ..., description="用户名", json_schema_extra={"example": "用户名"}
    )
    release_date: Optional[str] = Field(
        None, description="发行日期", json_schema_extra={"example": "2024-01-01"}
    )


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


class SyncStats(BaseModel):
    """同步统计模型"""

    total_syncs: int
    success_syncs: int
    error_syncs: int
    today_syncs: int
    success_rate: float
    user_stats: list
    daily_stats: list


class TestSyncRequest(BaseModel):
    """测试同步请求模型"""

    title: str = Field(..., description="番剧标题")
    ori_title: Optional[str] = Field(None, description="原始标题")
    season: int = Field(1, description="季度")
    episode: int = Field(1, description="集数")
    release_date: Optional[str] = Field(None, description="发行日期")
    user_name: str = Field("test_user", description="用户名")
    source: str = Field("test", description="来源")
