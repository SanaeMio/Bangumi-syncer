"""
Trakt.tv 数据模型定义
"""

import time
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TraktHistoryItem(BaseModel):
    """Trakt 观看历史项"""

    id: int = Field(..., description="条目ID")
    watched_at: str = Field(..., description="观看时间")
    action: str = Field(..., description="动作类型")
    type: str = Field(..., description="媒体类型")
    movie: Optional[dict[str, Any]] = Field(None, description="电影信息")
    show: Optional[dict[str, Any]] = Field(None, description="剧集信息")
    episode: Optional[dict[str, Any]] = Field(None, description="剧集详情")

    @property
    def media_type(self) -> str:
        """获取媒体类型"""
        return self.type

    @property
    def trakt_item_id(self) -> str:
        """获取 Trakt 条目ID"""
        if self.type == "movie" and self.movie:
            return f"movie:{self.movie.get('ids', {}).get('trakt')}"
        elif self.type == "episode" and self.episode:
            return f"episode:{self.episode.get('ids', {}).get('trakt')}"
        else:
            return f"{self.type}:{self.id}"

    @property
    def watched_timestamp(self) -> int:
        """获取观看时间戳"""
        try:
            # 解析 ISO 8601 时间字符串
            dt = datetime.fromisoformat(self.watched_at.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except (ValueError, AttributeError):
            return int(time.time())


class TraktRatingItem(BaseModel):
    """Trakt 评分项"""

    rating: int = Field(..., description="评分 (1-10)")
    rated_at: str = Field(..., description="评分时间")
    type: str = Field(..., description="媒体类型")
    movie: Optional[dict[str, Any]] = Field(None, description="电影信息")
    show: Optional[dict[str, Any]] = Field(None, description="剧集信息")
    episode: Optional[dict[str, Any]] = Field(None, description="剧集详情")

    @property
    def media_type(self) -> str:
        """获取媒体类型"""
        return self.type


class TraktCollectionItem(BaseModel):
    """Trakt 收藏项"""

    collected_at: str = Field(..., description="收藏时间")
    type: str = Field(..., description="媒体类型")
    movie: Optional[dict[str, Any]] = Field(None, description="电影信息")
    show: Optional[dict[str, Any]] = Field(None, description="剧集信息")
    episode: Optional[dict[str, Any]] = Field(None, description="剧集详情")

    @property
    def media_type(self) -> str:
        """获取媒体类型"""
        return self.type


# ===== 数据转换相关模型 =====


class TraktSyncResult(BaseModel):
    """Trakt 同步结果"""

    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果消息")
    synced_count: int = Field(0, description="同步数量")
    skipped_count: int = Field(0, description="跳过数量")
    error_count: int = Field(0, description="错误数量")
    details: Optional[dict] = Field(None, description="详细结果")


class TraktSyncStats(BaseModel):
    """Trakt 同步统计"""

    total_items: int = Field(0, description="总项目数")
    movies: int = Field(0, description="电影数量")
    episodes: int = Field(0, description="剧集数量")
    start_time: Optional[float] = Field(None, description="开始时间")
    end_time: Optional[float] = Field(None, description="结束时间")
    duration: Optional[float] = Field(None, description="持续时间（秒）")
