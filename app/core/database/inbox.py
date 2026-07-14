"""
收件箱通知与公告已读状态仓库
"""

from datetime import datetime
from typing import Any, Optional

from ..logging import logger
from .connection import INBOX_ERROR_BACKFILL_META_KEY


class InboxRepository:
    """收件箱通知与公告已读状态的增删改查"""

    def __init__(self, conn, feiniu_repository=None):
        self._conn = conn
        self._feiniu = feiniu_repository

    def list_in_app_notifications(
        self,
        limit: int = 50,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        """获取本地收件箱通知列表（新在前）"""
        try:

            def _read(conn):
                where = "WHERE read_at IS NULL" if unread_only else ""
                cursor = conn.execute(
                    f"""
                    SELECT id, type, title, body, ref_id, created_at, read_at
                    FROM in_app_notifications
                    {where}
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]

            return self._conn._execute_with_lock(_read) or []
        except Exception as e:
            logger.error(f"获取收件箱通知失败: {e}")
            return []

    def get_in_app_notification_by_id(
        self, notification_id: int
    ) -> Optional[dict[str, Any]]:
        try:

            def _read(conn):
                cursor = conn.execute(
                    """
                    SELECT id, type, title, body, ref_id, created_at, read_at
                    FROM in_app_notifications WHERE id = ?
                    """,
                    (notification_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cursor.description]
                return dict(zip(cols, row))

            return self._conn._execute_with_lock(_read)
        except Exception as e:
            logger.error(f"获取通知详情失败: {e}")
            return None

    def mark_notification_read(self, notification_id: int) -> bool:
        try:
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _write(conn):
                cursor = conn.execute(
                    """
                    UPDATE in_app_notifications
                    SET read_at = ?
                    WHERE id = ? AND read_at IS NULL
                    """,
                    (local_time, notification_id),
                )
                conn.commit()
                return cursor.rowcount > 0

            return bool(self._conn._execute_with_lock(_write))
        except Exception as e:
            logger.error(f"标记通知已读失败: {e}")
            return False

    def mark_notifications_read_by_ref_id(self, ref_id: int) -> int:
        """同步记录恢复成功时，将关联通知标为已读。"""
        try:
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _write(conn):
                cursor = conn.execute(
                    """
                    UPDATE in_app_notifications
                    SET read_at = ?
                    WHERE ref_id = ? AND read_at IS NULL
                    """,
                    (local_time, ref_id),
                )
                conn.commit()
                return cursor.rowcount

            return int(self._conn._execute_with_lock(_write) or 0)
        except Exception as e:
            logger.error(f"按同步记录标记通知已读失败: {e}")
            return 0

    def mark_notification_group_read(self, notification_id: int) -> int:
        """将同一番剧聚合组内的未读通知全部标为已读。"""
        from ...utils.inbox_notifications import notification_group_key

        row = self.get_in_app_notification_by_id(notification_id)
        if not row:
            return 0
        group_key = notification_group_key(str(row.get("title") or ""))
        try:
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _write(conn):
                cursor = conn.execute(
                    """
                    SELECT id, title FROM in_app_notifications
                    WHERE read_at IS NULL
                    """
                )
                ids = [
                    int(r[0])
                    for r in cursor.fetchall()
                    if notification_group_key(str(r[1] or "")) == group_key
                ]
                if not ids:
                    return 0
                for nid in ids:
                    conn.execute(
                        """
                        UPDATE in_app_notifications
                        SET read_at = ?
                        WHERE id = ? AND read_at IS NULL
                        """,
                        (local_time, nid),
                    )
                conn.commit()
                return len(ids)

            return int(self._conn._execute_with_lock(_write) or 0)
        except Exception as e:
            logger.error(f"按组标记通知已读失败: {e}")
            return 0

    def mark_all_notifications_read(self) -> int:
        try:
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _write(conn):
                cursor = conn.execute(
                    """
                    UPDATE in_app_notifications
                    SET read_at = ?
                    WHERE read_at IS NULL
                    """,
                    (local_time,),
                )
                conn.commit()
                return cursor.rowcount

            return int(self._conn._execute_with_lock(_write) or 0)
        except Exception as e:
            logger.error(f"全部通知已读失败: {e}")
            return 0

    def count_unread_notifications(self) -> int:
        try:

            def _read(conn):
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM in_app_notifications WHERE read_at IS NULL"
                )
                return int(cursor.fetchone()[0])

            return int(self._conn._execute_with_lock(_read) or 0)
        except Exception as e:
            logger.error(f"统计未读通知失败: {e}")
            return 0

    def get_read_announcement_ids(self) -> set[str]:
        try:

            def _read(conn):
                cursor = conn.execute(
                    "SELECT announcement_id FROM announcement_read_state"
                )
                return {row[0] for row in cursor.fetchall()}

            return self._conn._execute_with_lock(_read) or set()
        except Exception as e:
            logger.error(f"获取公告已读状态失败: {e}")
            return set()

    def mark_announcement_read(self, announcement_id: str) -> bool:
        try:
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _write(conn):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO announcement_read_state
                    (announcement_id, read_at) VALUES (?, ?)
                    """,
                    (announcement_id, local_time),
                )
                conn.commit()
                return True

            return bool(self._conn._execute_with_lock(_write))
        except Exception as e:
            logger.error(f"标记公告已读失败: {e}")
            return False

    def mark_all_announcements_read(self, announcement_ids: list[str]) -> int:
        if not announcement_ids:
            return 0
        try:
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _write(conn):
                count = 0
                for aid in announcement_ids:
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO announcement_read_state
                        (announcement_id, read_at) VALUES (?, ?)
                        """,
                        (aid, local_time),
                    )
                    if cursor.rowcount > 0:
                        count += 1
                conn.commit()
                return count

            return int(self._conn._execute_with_lock(_write) or 0)
        except Exception as e:
            logger.error(f"全部公告已读失败: {e}")
            return 0

    def backfill_historical_error_notifications(self) -> int:
        """将历史 error 同步记录回填为已读通知（仅执行一次）。"""
        if self._feiniu.get_feiniu_meta(INBOX_ERROR_BACKFILL_META_KEY):
            return 0
        try:

            def _write(conn):
                cursor = conn.execute(
                    """
                    SELECT id, title, season, episode, message, timestamp, media_type
                    FROM sync_records
                    WHERE status = 'error'
                    ORDER BY id ASC
                    """
                )
                rows = cursor.fetchall()
                inserted = 0
                for row in rows:
                    (
                        record_id,
                        title,
                        season,
                        episode,
                        message,
                        timestamp,
                        media_type,
                    ) = row
                    exists = conn.execute(
                        "SELECT 1 FROM in_app_notifications WHERE ref_id = ? LIMIT 1",
                        (record_id,),
                    ).fetchone()
                    if exists:
                        continue
                    mt = media_type or "episode"
                    ep_label = f"S{season}E{episode}" if mt == "episode" else "剧场版"
                    notif_title = f"同步失败：{title} {ep_label}"
                    created_at = timestamp or datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    conn.execute(
                        """
                        INSERT INTO in_app_notifications
                        (type, title, body, ref_id, created_at, read_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "sync_failed",
                            notif_title,
                            message or "",
                            record_id,
                            created_at,
                            created_at,
                        ),
                    )
                    inserted += 1
                conn.execute(
                    """
                    INSERT OR REPLACE INTO feiniu_meta (key, value) VALUES (?, ?)
                    """,
                    (INBOX_ERROR_BACKFILL_META_KEY, "1"),
                )
                conn.commit()
                return inserted

            count = int(self._conn._execute_with_lock(_write) or 0)
            if count:
                logger.info(f"历史同步失败通知已回填（已读）: {count} 条")
            return count
        except Exception as e:
            logger.error(f"历史同步失败通知回填失败: {e}")
            return 0
