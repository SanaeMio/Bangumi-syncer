"""
同步记录 CRUD 仓库
"""

import json
import time
from datetime import datetime
from typing import Any, Optional

from ..logging import logger
from .base_repository import BaseRepository


class SyncRecordsRepository(BaseRepository):
    """同步记录的增删改查"""

    def __init__(self, conn, inbox_repository=None):
        super().__init__(conn)
        self._inbox = inbox_repository
        self._heatmap_cache: Optional[list] = None
        self._heatmap_cache_time: float = 0

    def log_sync_record(
        self,
        user_name: str,
        title: str,
        ori_title: Optional[str],
        season: int,
        episode: int,
        subject_id: Optional[str] = None,
        episode_id: Optional[str] = None,
        status: str = "success",
        message: str = "",
        source: str = "custom",
        media_type: str = "episode",
        bgm_title: str = "",
        match_method: str = "",
        match_score: Optional[float] = None,
        match_platform: str = "",
        match_trace: Optional[dict] = None,
    ) -> Optional[int]:
        """记录同步日志到数据库，返回新记录 id（失败时 None）

        匹配追踪相关字段：
        - match_method: 匹配方式（custom_mapping/bangumi_data/api_search/failed）
        - match_score: 最佳匹配置信度（0-1）
        - match_platform: 命中条目的 platform
        - match_trace: 完整匹配过程字典（序列化为 JSON 存储）
        """

        def _ensure_schema(cursor):
            self._conn._ensure_sync_records_media_type(cursor)
            self._conn._ensure_sync_records_bgm_title(cursor)
            self._conn._ensure_sync_records_match_fields(cursor)

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            trace_json = (
                json.dumps(match_trace, ensure_ascii=False) if match_trace else ""
            )
            cursor = conn.execute(
                """
                INSERT INTO sync_records
                (timestamp, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source, media_type, bgm_title, match_method, match_score, match_platform, match_trace)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    local_time,
                    user_name,
                    title,
                    ori_title,
                    season,
                    episode,
                    subject_id,
                    episode_id,
                    status,
                    message,
                    source,
                    media_type or "episode",
                    bgm_title or "",
                    match_method or "",
                    match_score,
                    match_platform or "",
                    trace_json,
                ),
            )
            record_id = cursor.lastrowid
            if status == "error" and record_id:
                ep_label = (
                    f"S{season}E{episode}"
                    if (media_type or "episode") == "episode"
                    else "剧场版"
                )
                notif_title = f"同步失败：{title} {ep_label}"
                conn.execute(
                    """
                    INSERT INTO in_app_notifications
                    (type, title, body, ref_id, created_at, read_at)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        "sync_failed",
                        notif_title,
                        message or "",
                        record_id,
                        local_time,
                    ),
                )
            return record_id

        return self._run_write(
            _write,
            error_msg="记录同步日志失败",
            default=None,
            ensure_schema=_ensure_schema,
        )

    def get_sync_records(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        user_name: Optional[str] = None,
        source: Optional[str] = None,
        source_prefix: Optional[str] = None,
        skip_count: bool = False,
    ) -> dict[str, Any]:
        """获取同步记录"""

        def _ensure_schema(cursor):
            self._conn._ensure_sync_records_media_type(cursor)
            self._conn._ensure_sync_records_bgm_title(cursor)
            self._conn._ensure_sync_records_match_fields(cursor)

        def _read(conn):
            cursor = conn.cursor()

            where_conditions = []
            params = []

            if status:
                where_conditions.append("status = ?")
                params.append(status)

            if user_name:
                where_conditions.append("user_name = ?")
                params.append(user_name)

            if source:
                where_conditions.append("source = ?")
                params.append(source)

            if source_prefix:
                where_conditions.append("source LIKE ?")
                params.append(f"{source_prefix}%")

            where_clause = (
                " WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            )

            if skip_count:
                total = -1
            else:
                count_query = f"SELECT COUNT(*) FROM sync_records{where_clause}"
                cursor.execute(count_query, params)
                total = cursor.fetchone()[0]

            # 列表查询不 SELECT match_trace（JSON 体积大，列表页前端不使用）
            # 完整 trace 通过 get_sync_record_by_id 或 /api/match-records/{id}/trace 获取
            query = f"""
                SELECT id, timestamp, user_name, title, ori_title, season, episode,
                       subject_id, episode_id, status, message, source, media_type, bgm_title,
                       match_method, match_score, match_platform
                FROM sync_records{where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """
            cursor.execute(query, params + [limit, offset])

            records = []
            for row in cursor.fetchall():
                records.append(
                    {
                        "id": row[0],
                        "timestamp": row[1],
                        "user_name": row[2],
                        "title": row[3],
                        "ori_title": row[4],
                        "season": row[5],
                        "episode": row[6],
                        "subject_id": row[7],
                        "episode_id": row[8],
                        "status": row[9],
                        "message": row[10],
                        "source": row[11],
                        "media_type": row[12] or "episode",
                        "bgm_title": row[13] or "",
                        "match_method": row[14] or "",
                        "match_score": row[15],
                        "match_platform": row[16] or "",
                    }
                )

            return {
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            }

        return self._run_read(
            _read,
            error_msg="获取同步记录失败",
            reraise=True,
            ensure_schema=_ensure_schema,
        )

    def get_sync_record_by_id(self, record_id: int) -> Optional[dict[str, Any]]:
        """根据ID获取单个同步记录"""

        def _ensure_schema(cursor):
            self._conn._ensure_sync_records_media_type(cursor)
            self._conn._ensure_sync_records_bgm_title(cursor)
            self._conn._ensure_sync_records_match_fields(cursor)

        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, timestamp, user_name, title, ori_title, season, episode,
                       subject_id, episode_id, status, message, source, media_type, bgm_title,
                       match_method, match_score, match_platform, match_trace
                FROM sync_records
                WHERE id = ?
            """,
                (record_id,),
            )
            return cursor.fetchone()

        row = self._run_read(
            _read,
            error_msg="获取同步记录详情失败",
            reraise=True,
            ensure_schema=_ensure_schema,
        )
        if row:
            return {
                "id": row[0],
                "timestamp": row[1],
                "user_name": row[2],
                "title": row[3],
                "ori_title": row[4],
                "season": row[5],
                "episode": row[6],
                "subject_id": row[7],
                "episode_id": row[8],
                "status": row[9],
                "message": row[10],
                "source": row[11],
                "media_type": row[12] or "episode",
                "bgm_title": row[13] or "",
                "match_method": row[14] or "",
                "match_score": row[15],
                "match_platform": row[16] or "",
                "match_trace": row[17] or "",
            }
        return None

    def update_sync_record_status(
        self, record_id: int, status: str, message: str = ""
    ) -> bool:
        """更新同步记录的状态"""

        def _write(conn):
            cursor = conn.execute(
                """
                UPDATE sync_records
                SET status = ?, message = ?
                WHERE id = ?
            """,
                (status, message, record_id),
            )
            return cursor.rowcount

        affected_rows = self._run_write(
            _write,
            error_msg="更新同步记录状态失败",
            default=False,
        )
        if affected_rows > 0:
            if status in ("success", "retried"):
                self._inbox.mark_notifications_read_by_ref_id(record_id)
            return True
        else:
            logger.warning(f"记录 {record_id} 不存在，无法更新")
            return False

    def get_match_records(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        match_method: Optional[str] = None,
        match_platform: Optional[str] = None,
    ) -> dict[str, Any]:
        """获取匹配记录（含匹配追踪字段）"""

        def _ensure_schema(cursor):
            self._conn._ensure_sync_records_media_type(cursor)
            self._conn._ensure_sync_records_bgm_title(cursor)
            self._conn._ensure_sync_records_match_fields(cursor)

        def _read(conn):
            cursor = conn.cursor()

            where_conditions = []
            params = []

            if status:
                where_conditions.append("status = ?")
                params.append(status)

            if match_method:
                where_conditions.append("match_method = ?")
                params.append(match_method)

            if match_platform:
                where_conditions.append("match_platform = ?")
                params.append(match_platform)

            where_clause = (
                " WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            )

            count_query = f"SELECT COUNT(*) FROM sync_records{where_clause}"
            cursor.execute(count_query, params)
            total = cursor.fetchone()[0]

            # 列表查询不 SELECT match_trace（JSON 体积大，列表页前端不使用）
            # 完整 trace 通过 get_sync_record_by_id 或 /api/match-records/{id}/trace 获取
            query = f"""
                SELECT id, timestamp, user_name, title, ori_title, season, episode,
                       subject_id, episode_id, status, message, source, media_type, bgm_title,
                       match_method, match_score, match_platform
                FROM sync_records{where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """
            cursor.execute(query, params + [limit, offset])

            records = []
            for row in cursor.fetchall():
                records.append(
                    {
                        "id": row[0],
                        "timestamp": row[1],
                        "user_name": row[2],
                        "title": row[3],
                        "ori_title": row[4],
                        "season": row[5],
                        "episode": row[6],
                        "subject_id": row[7],
                        "episode_id": row[8],
                        "status": row[9],
                        "message": row[10],
                        "source": row[11],
                        "media_type": row[12] or "episode",
                        "bgm_title": row[13] or "",
                        "match_method": row[14] or "",
                        "match_score": row[15],
                        "match_platform": row[16] or "",
                    }
                )

            return {
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            }

        return self._run_read(
            _read,
            error_msg="获取匹配记录失败",
            reraise=True,
            ensure_schema=_ensure_schema,
        )

    def get_sync_stats(self) -> dict[str, Any]:
        """获取同步统计信息"""

        def _read(conn):
            cursor = conn.cursor()

            # 合并 3 条 COUNT 查询为 1 条，减少数据库往返
            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                    SUM(CASE WHEN DATE(timestamp) = DATE('now') THEN 1 ELSE 0 END) AS today
                FROM sync_records
            """)
            row = cursor.fetchone()
            total_syncs, success_syncs, error_syncs, today_syncs = row

            cursor.execute(
                "SELECT user_name, COUNT(*) FROM sync_records GROUP BY user_name ORDER BY COUNT(*) DESC"
            )
            user_stats = [{"user": r[0], "count": r[1]} for r in cursor.fetchall()]

            cursor.execute("""
                SELECT DATE(timestamp) as date, COUNT(*) as count
                FROM sync_records
                WHERE timestamp >= datetime('now', '-7 days')
                GROUP BY DATE(timestamp)
                ORDER BY date
            """)
            daily_stats = [{"date": r[0], "count": r[1]} for r in cursor.fetchall()]

            return {
                "total_syncs": total_syncs,
                "success_syncs": success_syncs,
                "error_syncs": error_syncs,
                "today_syncs": today_syncs,
                "success_rate": round(success_syncs / total_syncs * 100, 2)
                if total_syncs > 0
                else 0,
                "user_stats": user_stats,
                "daily_stats": daily_stats,
            }

        return self._run_read(
            _read,
            error_msg="获取统计信息失败",
            reraise=True,
        )

    def get_heatmap_stats(self) -> list[dict[str, Any]]:
        """获取热力图数据（过去365天每天同步数），带5分钟缓存"""
        now = time.time()
        if self._heatmap_cache is not None and now - self._heatmap_cache_time < 300:
            return self._heatmap_cache

        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DATE(timestamp) as date, COUNT(*) as count
                FROM sync_records
                WHERE timestamp >= datetime('now', '-365 days')
                GROUP BY DATE(timestamp)
                ORDER BY date
                """
            )
            return [{"date": row[0], "count": row[1]} for row in cursor.fetchall()]

        result = self._run_read(
            _read,
            error_msg="获取热力图数据失败",
            reraise=True,
        )
        self._heatmap_cache = result
        self._heatmap_cache_time = now
        return result

    def cleanup_old_records(self, retention_days: int) -> int:
        """清理超过保留天数的同步记录，返回删除行数。

        retention_days <= 0 时不清理（永不清理语义）。
        """
        if retention_days <= 0:
            return 0

        def _write(conn):
            cursor = conn.execute(
                "DELETE FROM sync_records WHERE timestamp < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
            return cursor.rowcount

        try:
            deleted = self._run_write(_write, error_msg="清理旧同步记录失败")
            if deleted > 0:
                # 热力图缓存可能过期，清空让下次查询重新加载
                self._heatmap_cache = None
                logger.info(f"已清理 {deleted} 条超过 {retention_days} 天的同步记录")
            return deleted
        except Exception as e:
            logger.warning(f"清理旧同步记录失败（不影响主流程）: {e}")
            return 0
