"""收件箱服务（API 层入口，封装数据库访问）

将原 ``app/api/inbox.py`` 直接调用 ``database_manager`` 的收件箱相关操作
集中到本服务，使 API 层仅依赖 service 层，避免跨层直访数据库。
"""

from __future__ import annotations

from typing import Any

from ..core.database import database_manager


class InboxService:
    """收件箱服务：封装本地通知与公告已读状态的数据库操作。"""

    # ------------------------------------------------------------------
    # 本地通知
    # ------------------------------------------------------------------

    def list_in_app_notifications(
        self, limit: int = 50, unread_only: bool = False
    ) -> list[dict[str, Any]]:
        """获取本地收件箱通知列表（新在前）"""
        return database_manager.list_in_app_notifications(limit, unread_only)

    def get_in_app_notification_by_id(
        self, notification_id: int
    ) -> dict[str, Any] | None:
        """根据 ID 获取单个通知"""
        return database_manager.get_in_app_notification_by_id(notification_id)

    def mark_notification_read(self, notification_id: int) -> bool:
        """标记单条通知为已读"""
        return database_manager.mark_notification_read(notification_id)

    def mark_notifications_read_by_ref_id(self, ref_id: int) -> int:
        """同步记录恢复成功时，将关联通知标为已读。"""
        return database_manager.mark_notifications_read_by_ref_id(ref_id)

    def mark_notification_group_read(self, notification_id: int) -> int:
        """将同一番剧聚合组内的未读通知全部标为已读。"""
        return database_manager.mark_notification_group_read(notification_id)

    def mark_all_notifications_read(self) -> int:
        """将所有本地通知标记为已读"""
        return database_manager.mark_all_notifications_read()

    def count_unread_notifications(self) -> int:
        """未读通知计数（精确，基于 read_at IS NULL）"""
        return database_manager.count_unread_notifications()

    # ------------------------------------------------------------------
    # 公告已读状态
    # ------------------------------------------------------------------

    def get_read_announcement_ids(self) -> set[str]:
        """获取已读公告 ID 集合"""
        return database_manager.get_read_announcement_ids()

    def mark_announcement_read(self, announcement_id: str) -> bool:
        """标记单条公告为已读"""
        return database_manager.mark_announcement_read(announcement_id)

    def mark_all_announcements_read(self, announcement_ids: list[str]) -> int:
        """将给定公告 ID 列表全部标记为已读"""
        return database_manager.mark_all_announcements_read(announcement_ids)


# 全局收件箱服务实例
inbox_service = InboxService()
