"""待同步队列仓库

当 Bangumi API 不可达、写操作（mark_episode_watched 等）失败时，
将已匹配 subject_id+episode_id 的同步请求持久化到 pending_sync_queue 表，
待 API 恢复后由补发调度器自动重放。
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from ..logging import logger
from .base_repository import BaseRepository


class PendingSyncQueueRepository(BaseRepository):
    """待同步队列的增删改查"""

    def enqueue(
        self,
        user_name: str,
        title: str,
        season: int,
        episode: int,
        subject_id: str,
        episode_id: Optional[str],
        source: str,
        media_type: str,
        payload: dict[str, Any],
        reason: str = "api_unreachable",
        last_error: str = "",
        sync_record_id: Optional[int] = None,
    ) -> Optional[int]:
        """入队一条待同步任务，返回记录 id（失败时 None）。

        去重规则：同一 (user_name, subject_id, episode_id, source) 已有 pending 行时
        更新 payload 与 last_error，刷新 created_at，不重复插入。

        sync_record_id：关联的 sync_records 行 id，用于补发成功/放弃时回写状态。
        去重 UPDATE 时也会刷新为最新值（同一集多次入队时，以最新 queued 记录为准）。
        """

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload_json = json.dumps(payload, ensure_ascii=False)

            # 先查是否已有 pending 行（按 user+subject+episode+source 去重，
            # episode_id 为空时按 NULL 处理，等价于按 subject_id 去重）
            cursor = conn.execute(
                """
                SELECT id FROM pending_sync_queue
                WHERE user_name = ? AND subject_id = ?
                  AND COALESCE(episode_id, '') = COALESCE(?, '')
                  AND source = ? AND status = 'pending'
                """,
                (user_name, str(subject_id), episode_id or "", source),
            )
            row = cursor.fetchone()

            if row:
                existing_id = row[0]
                conn.execute(
                    """
                    UPDATE pending_sync_queue
                    SET created_at = ?, title = ?, season = ?, episode = ?,
                        episode_id = ?, media_type = ?, payload_json = ?,
                        last_error = ?, attempts = 0, last_attempt_at = NULL,
                        sync_record_id = ?
                    WHERE id = ?
                    """,
                    (
                        local_time,
                        title,
                        int(season),
                        int(episode),
                        episode_id,
                        media_type,
                        payload_json,
                        last_error or reason,
                        sync_record_id,
                        existing_id,
                    ),
                )
                return existing_id

            cursor = conn.execute(
                """
                INSERT INTO pending_sync_queue
                (created_at, user_name, title, season, episode, subject_id,
                 episode_id, source, media_type, payload_json, status,
                 attempts, last_attempt_at, last_error, sync_record_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, ?, ?)
                """,
                (
                    local_time,
                    user_name,
                    title,
                    int(season),
                    int(episode),
                    str(subject_id),
                    episode_id,
                    source,
                    media_type,
                    payload_json,
                    last_error or reason,
                    sync_record_id,
                ),
            )
            return cursor.lastrowid

        return self._run_write(_write, error_msg="入队待同步任务失败", default=None)

    def fetch_pending(
        self,
        limit: int = 20,
        max_attempts: Optional[int] = None,
        user_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """拉取一批 pending 任务（按 created_at 升序，先入先补发）

        user_name 非 None 时按用户过滤（多用户隔离）。
        """

        def _read(conn):
            sql = """
                SELECT id, created_at, user_name, title, season, episode,
                       subject_id, episode_id, source, media_type, payload_json,
                       attempts, last_attempt_at, last_error, sync_record_id
                FROM pending_sync_queue
                WHERE status = 'pending'
            """
            params: list[Any] = []
            if max_attempts is not None:
                sql += " AND attempts < ?"
                params.append(int(max_attempts))
            if user_name is not None:
                sql += " AND user_name = ?"
                params.append(user_name)
            sql += " ORDER BY created_at ASC LIMIT ?"
            params.append(int(limit))
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

        return self._run_read(_read, error_msg="拉取待同步任务失败", default=[])

    def mark_synced(self, record_id: int, user_name: Optional[str] = None) -> bool:
        """标记为已同步

        user_name 非 None 时校验归属，不匹配不更新（返回 False）。
        """

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sql = (
                "UPDATE pending_sync_queue "
                "SET status = 'synced', last_attempt_at = ? WHERE id = ?"
            )
            params: list[Any] = [local_time, record_id]
            if user_name is not None:
                sql += " AND user_name = ?"
                params.append(user_name)
            cursor = conn.execute(sql, params)
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="标记已同步失败", default=False)

    def mark_synced_by_sync_record_id(self, sync_record_id: int) -> int:
        """按 sync_record_id 反查 pending 行并标记为 synced，返回受影响行数。

        场景：手动重试一条 queued 的 sync_record 成功后，需要清理背后等待补发的
        pending_sync_queue 行，避免补发调度器重复捞起导致重复标记。
        仅清理 status='pending' 的行，已 synced/abandoned 的不受影响。
        """

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                "UPDATE pending_sync_queue "
                "SET status = 'synced', last_attempt_at = ? "
                "WHERE sync_record_id = ? AND status = 'pending'",
                (local_time, int(sync_record_id)),
            )
            return cursor.rowcount

        return self._run_write(
            _write, error_msg="按 sync_record_id 清理 pending 行失败", default=0
        )

    def link_sync_record_id(
        self,
        user_name: str,
        subject_id: str,
        episode_id: Optional[str],
        source: str,
        sync_record_id: int,
    ) -> bool:
        """按四元组软匹配，把最近一条 pending 行的 sync_record_id 回填为给定值。

        场景：入队时（_retry_mark_episode 内部）sync_record_id 尚未产生，
        主流程在随后 log_sync_record(status="queued") 拿到 id 后调用本方法回填关联。
        部分唯一索引保证 (user, subject, ep, source) 至多一条 pending 行，更新安全。
        """

        def _write(conn):
            sql = """
                UPDATE pending_sync_queue
                SET sync_record_id = ?
                WHERE user_name = ? AND subject_id = ?
                  AND COALESCE(episode_id, '') = COALESCE(?, '')
                  AND source = ? AND status = 'pending'
            """
            cursor = conn.execute(
                sql,
                (
                    int(sync_record_id),
                    user_name,
                    str(subject_id),
                    episode_id or "",
                    source,
                ),
            )
            return cursor.rowcount > 0

        return self._run_write(
            _write, error_msg="回填 sync_record_id 失败", default=False
        )

    def increment_attempts(
        self, record_id: int, error: str, user_name: Optional[str] = None
    ) -> bool:
        """重试失败时累加 attempts 并记录错误

        user_name 非 None 时校验归属，不匹配不更新（返回 False）。
        """

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sql = """
                UPDATE pending_sync_queue
                SET attempts = attempts + 1, last_attempt_at = ?, last_error = ?
                WHERE id = ?
            """
            params: list[Any] = [local_time, error, record_id]
            if user_name is not None:
                sql += " AND user_name = ?"
                params.append(user_name)
            cursor = conn.execute(sql, params)
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="累加重试次数失败", default=False)

    def update_error_message(
        self, record_id: int, message: str, user_name: Optional[str] = None
    ) -> bool:
        """仅更新错误消息，不累加 attempts（用于手动补发失败场景）

        手动补发是用户显式触发的操作，理应给予更高的成功机会：
        只记录失败信息供查看，避免重试几次后被自动补发标记为 abandoned。

        user_name 非 None 时校验归属，不匹配不更新（返回 False）。
        """

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sql = (
                "UPDATE pending_sync_queue "
                "SET last_error = ?, last_attempt_at = ? WHERE id = ?"
            )
            params: list[Any] = [message, local_time, record_id]
            if user_name is not None:
                sql += " AND user_name = ?"
                params.append(user_name)
            cursor = conn.execute(sql, params)
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="更新错误消息失败", default=False)

    def mark_abandoned(
        self, record_id: int, reason: str = "", user_name: Optional[str] = None
    ) -> bool:
        """标记为放弃（重试次数耗尽等）

        user_name 非 None 时校验归属，不匹配不更新（返回 False）。
        """

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sql = """
                UPDATE pending_sync_queue
                SET status = 'abandoned', last_attempt_at = ?, last_error = ?
                WHERE id = ?
            """
            params: list[Any] = [
                local_time,
                reason or "exceeded max attempts",
                record_id,
            ]
            if user_name is not None:
                sql += " AND user_name = ?"
                params.append(user_name)
            cursor = conn.execute(sql, params)
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="标记放弃失败", default=False)

    def delete_record(self, record_id: int, user_name: Optional[str] = None) -> bool:
        """删除一条记录（用户手动清理）

        user_name 非 None 时校验归属，不匹配不删除（返回 False）。
        """

        def _write(conn):
            sql = "DELETE FROM pending_sync_queue WHERE id = ?"
            params: list[Any] = [record_id]
            if user_name is not None:
                sql += " AND user_name = ?"
                params.append(user_name)
            cursor = conn.execute(sql, params)
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="删除待同步任务失败", default=False)

    def get_queue(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        user_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """获取待同步队列列表，返回 {records, total, limit, offset}

        user_name 非 None 时按用户过滤（多用户隔离）。
        """

        def _read(conn):
            where_conditions = []
            params: list[Any] = []
            if status:
                where_conditions.append("status = ?")
                params.append(status)
            if user_name is not None:
                where_conditions.append("user_name = ?")
                params.append(user_name)
            where_clause = (
                f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""
            )

            cursor = conn.execute(
                f"SELECT COUNT(*) AS total FROM pending_sync_queue {where_clause}",
                params,
            )
            total = cursor.fetchone()[0]

            cursor = conn.execute(
                f"""
                SELECT id, created_at, user_name, title, season, episode,
                       subject_id, episode_id, source, media_type, status,
                       attempts, last_attempt_at, last_error, sync_record_id
                FROM pending_sync_queue
                {where_clause}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            cols = [d[0] for d in cursor.description]
            records = [dict(zip(cols, row)) for row in cursor.fetchall()]
            return {
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            }

        return self._run_read(
            _read,
            error_msg="获取待同步队列失败",
            default={"records": [], "total": 0, "limit": limit, "offset": offset},
        )

    def get_record_by_id(
        self, record_id: int, user_name: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """获取单条记录详情（含 payload_json）

        user_name 非 None 时校验归属，不匹配返回 None。
        """

        def _read(conn):
            sql = """
                SELECT id, created_at, user_name, title, season, episode,
                       subject_id, episode_id, source, media_type, payload_json,
                       status, attempts, last_attempt_at, last_error, sync_record_id
                FROM pending_sync_queue WHERE id = ?
            """
            params: list[Any] = [record_id]
            if user_name is not None:
                sql += " AND user_name = ?"
                params.append(user_name)
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

        return self._run_read(_read, error_msg="获取待同步任务详情失败", default=None)

    def count_pending(self) -> int:
        """当前 pending 任务总数（供状态查询用）"""

        def _read(conn):
            cursor = conn.execute(
                "SELECT COUNT(*) FROM pending_sync_queue WHERE status = 'pending'"
            )
            return int(cursor.fetchone()[0])

        return self._run_read(_read, error_msg="统计 pending 任务失败", default=0)

    def get_stats(self) -> dict[str, int]:
        """返回各状态计数"""

        def _read(conn):
            cursor = conn.execute(
                """
                SELECT status, COUNT(*) AS cnt
                FROM pending_sync_queue
                GROUP BY status
                """
            )
            stats = {"pending": 0, "synced": 0, "abandoned": 0}
            for status, cnt in cursor.fetchall():
                stats[status] = int(cnt)
            return stats

        return self._run_read(
            _read,
            error_msg="统计队列状态失败",
            default={"pending": 0, "synced": 0, "abandoned": 0},
        )

    def cleanup_old_records(
        self,
        retention_days: int = 30,
        statuses: Optional[list[str]] = None,
    ) -> int:
        """清理超过保留天数的 synced/abandoned 历史记录，返回删除行数。

        绝不删除 pending 状态的记录（那是待处理任务）。

        Args:
            retention_days: 保留天数；<=0 时不清理（永不清理语义）。
            statuses: 待清理的状态集合，默认 ['synced', 'abandoned']。
                      传入空列表等异常值时回退到默认。
        """
        if retention_days <= 0:
            return 0

        # 严格限制可清理状态：永不包含 pending
        allowed = {"synced", "abandoned"}
        if not statuses:
            statuses = ["synced", "abandoned"]
        targets = [s for s in statuses if s in allowed]
        if not targets:
            return 0

        placeholders = ",".join("?" for _ in targets)

        def _write(conn):
            cursor = conn.execute(
                f"DELETE FROM pending_sync_queue "
                f"WHERE status IN ({placeholders}) "
                f"AND created_at < datetime('now', ?)",
                (*targets, f"-{retention_days} days"),
            )
            return cursor.rowcount

        try:
            deleted = self._run_write(_write, error_msg="清理待同步队列历史记录失败")
            if deleted > 0:
                logger.info(
                    f"已清理 {deleted} 条超过 {retention_days} 天的待同步队列历史记录"
                    f"（状态: {','.join(targets)}）"
                )
            return deleted
        except Exception as e:
            logger.warning(f"清理待同步队列历史记录失败（不影响主流程）: {e}")
            return 0
