"""控制台收件箱 Pydantic 模型。"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析模型字段的 ``str | None`` 会失败，此处保留 Optional

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

InboxCategory = Literal["announcement", "notification", "all"]
AnnouncementLevel = Literal["info", "warning", "important"]


class InboxSummary(BaseModel):
    announcements: int = 0
    notifications: int = 0
    total: int = 0
    remote_error: Optional[str] = None


class AnnouncementItem(BaseModel):
    id: str
    title: str
    level: str = "info"
    published_at: str = ""
    read: bool = False
    body_html: str = ""


class NotificationItem(BaseModel):
    id: int
    type: str
    title: str
    body: str = ""
    ref_id: Optional[int] = None
    created_at: str = ""
    read: bool = False
    count: int = 1
    notification_ids: list[int] = Field(default_factory=list)


class InboxListData(BaseModel):
    announcements: list[AnnouncementItem] = Field(default_factory=list)
    notifications: list[NotificationItem] = Field(default_factory=list)
    remote_loaded: bool = False
    remote_error: Optional[str] = None


class ReadAllRequest(BaseModel):
    category: Optional[InboxCategory] = "all"
