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
    ) -> Optional[int]:
        """入队一条待同步任务，返回记录 id（失败时 None）。

        去重规则：同一 (user_name, subject_id, episode_id, source) 已有 pending 行时
        更新 payload 与 last_error，刷新 created_at，不重复插入。
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
                        last_error = ?, attempts = 0, last_attempt_at = NULL
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
                        existing_id,
                    ),
                )
                return existing_id

            cursor = conn.execute(
                """
                INSERT INTO pending_sync_queue
                (created_at, user_name, title, season, episode, subject_id,
                 episode_id, source, media_type, payload_json, status,
                 attempts, last_attempt_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, ?)
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
                ),
            )
            return cursor.lastrowid

        return self._run_write(_write, error_msg="入队待同步任务失败", default=None)

    def fetch_pending(
        self, limit: int = 20, max_attempts: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """拉取一批 pending 任务（按 created_at 升序，先入先补发）"""

        def _read(conn):
            sql = """
                SELECT id, created_at, user_name, title, season, episode,
                       subject_id, episode_id, source, media_type, payload_json,
                       attempts, last_attempt_at, last_error
                FROM pending_sync_queue
                WHERE status = 'pending'
            """
            params: list[Any] = []
            if max_attempts is not None:
                sql += " AND attempts < ?"
                params.append(int(max_attempts))
            sql += " ORDER BY created_at ASC LIMIT ?"
            params.append(int(limit))
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

        return self._run_read(_read, error_msg="拉取待同步任务失败", default=[])

    def mark_synced(self, record_id: int) -> bool:
        """标记为已同步"""

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                "UPDATE pending_sync_queue SET status = 'synced', last_attempt_at = ? WHERE id = ?",
                (local_time, record_id),
            )
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="标记已同步失败", default=False)

    def increment_attempts(self, record_id: int, error: str) -> bool:
        """重试失败时累加 attempts 并记录错误"""

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                """
                UPDATE pending_sync_queue
                SET attempts = attempts + 1, last_attempt_at = ?, last_error = ?
                WHERE id = ?
                """,
                (local_time, error, record_id),
            )
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="累加重试次数失败", default=False)

    def mark_abandoned(self, record_id: int, reason: str = "") -> bool:
        """标记为放弃（重试次数耗尽等）"""

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                """
                UPDATE pending_sync_queue
                SET status = 'abandoned', last_attempt_at = ?, last_error = ?
                WHERE id = ?
                """,
                (local_time, reason or "exceeded max attempts", record_id),
            )
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="标记放弃失败", default=False)

    def delete_record(self, record_id: int) -> bool:
        """删除一条记录（用户手动清理）"""

        def _write(conn):
            cursor = conn.execute(
                "DELETE FROM pending_sync_queue WHERE id = ?", (record_id,)
            )
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="删除待同步任务失败", default=False)

    def get_queue(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        """获取待同步队列列表，返回 {records, total, limit, offset}"""

        def _read(conn):
            where_conditions = []
            params: list[Any] = []
            if status:
                where_conditions.append("status = ?")
                params.append(status)
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
                       attempts, last_attempt_at, last_error
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

    def get_record_by_id(self, record_id: int) -> Optional[dict[str, Any]]:
        """获取单条记录详情（含 payload_json）"""

        def _read(conn):
            cursor = conn.execute(
                """
                SELECT id, created_at, user_name, title, season, episode,
                       subject_id, episode_id, source, media_type, payload_json,
                       status, attempts, last_attempt_at, last_error
                FROM pending_sync_queue WHERE id = ?
                """,
                (record_id,),
            )
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
