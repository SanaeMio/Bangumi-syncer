"""
数据库管理模块
"""

import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .logging import logger

# 飞牛「启用后不同步历史」水位键（sync_records.db / feiniu_meta）
FEINIU_MIN_UPDATE_WATERMARK_META_KEY = "min_update_watermark_ms"
# 历史 error 同步记录回填收件箱通知（一次性，标为已读）
INBOX_ERROR_BACKFILL_META_KEY = "inbox_error_backfill_v1"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class DatabaseManager:
    """数据库管理器"""

    def __init__(self, db_path: Optional[str] = None):
        auto = db_path is None
        if auto:
            db_path = (
                "data/sync_records.db"
                if _env_flag("DOCKER_CONTAINER")
                else "sync_records.db"
            )
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if auto and _env_flag("DOCKER_CONTAINER"):
            legacy = Path("sync_records.db")
            if not self.db_path.exists() and legacy.is_file():
                shutil.move(str(legacy), str(self.db_path))
                logger.info(f"Docker: 已从旧路径迁移数据库 {legacy} -> {self.db_path}")

        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._media_type_migrated = False
        self._bgm_title_migrated = False
        self._trakt_filter_migrated = False
        self._heatmap_cache: Optional[list] = None
        self._heatmap_cache_time: float = 0
        self._init_database()

    def close(self) -> None:
        """关闭数据库连接"""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None

    def _get_connection(self) -> sqlite3.Connection:
        """获取持久化数据库连接（线程安全），自动重连"""
        if self._conn is not None:
            try:
                self._conn.execute("SELECT 1")
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
        if self._conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._conn = conn
        return self._conn

    def _execute_with_lock(self, fn):
        """在锁保护下执行数据库操作"""
        with self._lock:
            conn = self._get_connection()
            return fn(conn)

    def _ensure_sync_records_media_type(self, cursor) -> None:
        """旧库迁移：为 sync_records 增加 media_type（历史数据为 episode）。"""
        if self._media_type_migrated:
            return
        cursor.execute("PRAGMA table_info(sync_records)")
        cols = [row[1] for row in cursor.fetchall()]
        if "media_type" in cols:
            self._media_type_migrated = True
            return
        cursor.execute(
            "ALTER TABLE sync_records ADD COLUMN media_type TEXT DEFAULT 'episode'"
        )
        cursor.execute(
            """
            UPDATE sync_records
            SET media_type = 'episode'
            WHERE media_type IS NULL OR TRIM(COALESCE(media_type, '')) = ''
            """
        )
        self._media_type_migrated = True
        logger.info("sync_records 已迁移：增加 media_type 列并回填 episode")

    def _ensure_sync_records_bgm_title(self, cursor) -> None:
        """旧库迁移：为 sync_records 增加 bgm_title（Bangumi 平台标题）。"""
        if self._bgm_title_migrated:
            return
        cursor.execute("PRAGMA table_info(sync_records)")
        cols = [row[1] for row in cursor.fetchall()]
        if "bgm_title" in cols:
            self._bgm_title_migrated = True
            return
        cursor.execute("ALTER TABLE sync_records ADD COLUMN bgm_title TEXT DEFAULT ''")
        self._bgm_title_migrated = True
        logger.info("sync_records 已迁移：增加 bgm_title 列")

    def _ensure_trakt_config_sync_filter(self, cursor) -> None:
        """旧库迁移：为 trakt_config 增加 sync_filter_enabled（默认开启）。"""
        if self._trakt_filter_migrated:
            return
        cursor.execute("PRAGMA table_info(trakt_config)")
        cols = [row[1] for row in cursor.fetchall()]
        if "sync_filter_enabled" in cols:
            self._trakt_filter_migrated = True
            return
        cursor.execute(
            "ALTER TABLE trakt_config ADD COLUMN sync_filter_enabled BOOLEAN DEFAULT 1"
        )
        self._trakt_filter_migrated = True
        logger.info("trakt_config 已迁移：增加 sync_filter_enabled 列")

    def _init_database(self) -> None:
        """初始化数据库"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 创建同步记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_name TEXT NOT NULL,
                title TEXT NOT NULL,
                ori_title TEXT,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                subject_id TEXT,
                episode_id TEXT,
                status TEXT NOT NULL,
                message TEXT,
                source TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'episode',
                bgm_title TEXT DEFAULT ''
            )
        """)

        self._ensure_sync_records_media_type(cursor)

        # 创建 Trakt 配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trakt_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at INTEGER,
                enabled BOOLEAN DEFAULT 1,
                sync_interval TEXT DEFAULT '0 */6 * * *',
                sync_filter_enabled BOOLEAN DEFAULT 1,
                last_sync_time INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        # 创建 Trakt 同步历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trakt_sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                trakt_item_id TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'episode')),
                watched_at INTEGER NOT NULL,
                synced_at INTEGER NOT NULL,
                UNIQUE(user_id, trakt_item_id, watched_at)
            )
        """)

        # 飞牛影视 trimmedia 同步去重表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feiniu_sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fn_user_guid TEXT NOT NULL,
                item_guid TEXT NOT NULL,
                synced_at INTEGER NOT NULL,
                update_time_snapshot INTEGER,
                UNIQUE(fn_user_guid, item_guid)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feiniu_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS announcement_read_state (
                announcement_id TEXT PRIMARY KEY,
                read_at DATETIME NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS in_app_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL DEFAULT 'sync_failed',
                title TEXT NOT NULL,
                body TEXT,
                ref_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                read_at DATETIME
            )
        """)

        # 创建二级索引以加速常用查询
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_records_timestamp ON sync_records(timestamp)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_records_user_name ON sync_records(user_name)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_records_source ON sync_records(source)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_records_status ON sync_records(status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trakt_sync_history_user_id ON trakt_sync_history(user_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_in_app_notifications_unread ON in_app_notifications(read_at)"
        )

        conn.commit()
        logger.info(f"数据库初始化完成: {self.db_path}")
        self.backfill_historical_error_notifications()

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
    ) -> Optional[int]:
        """记录同步日志到数据库，返回新记录 id（失败时 None）"""
        try:

            def _write(conn):
                self._ensure_sync_records_media_type(conn.cursor())
                self._ensure_sync_records_bgm_title(conn.cursor())
                local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor = conn.execute(
                    """
                    INSERT INTO sync_records
                    (timestamp, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source, media_type, bgm_title)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                conn.commit()
                return record_id

            return self._execute_with_lock(_write)
        except Exception as e:
            logger.error(f"记录同步日志失败: {e}")
            return None

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
        try:

            def _read(conn):
                self._ensure_sync_records_media_type(conn.cursor())
                self._ensure_sync_records_bgm_title(conn.cursor())
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
                    " WHERE " + " AND ".join(where_conditions)
                    if where_conditions
                    else ""
                )

                if skip_count:
                    total = -1
                else:
                    count_query = f"SELECT COUNT(*) FROM sync_records{where_clause}"
                    cursor.execute(count_query, params)
                    total = cursor.fetchone()[0]

                query = f"""
                    SELECT id, timestamp, user_name, title, ori_title, season, episode,
                           subject_id, episode_id, status, message, source, media_type, bgm_title
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
                        }
                    )

                return {
                    "records": records,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }

            return self._execute_with_lock(_read)
        except Exception as e:
            logger.error(f"获取同步记录失败: {e}")
            raise

    def get_sync_record_by_id(self, record_id: int) -> Optional[dict[str, Any]]:
        """根据ID获取单个同步记录"""
        try:

            def _read(conn):
                self._ensure_sync_records_media_type(conn.cursor())
                self._ensure_sync_records_bgm_title(conn.cursor())
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, timestamp, user_name, title, ori_title, season, episode,
                           subject_id, episode_id, status, message, source, media_type, bgm_title
                    FROM sync_records
                    WHERE id = ?
                """,
                    (record_id,),
                )
                return cursor.fetchone()

            row = self._execute_with_lock(_read)
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
                }
            return None
        except Exception as e:
            logger.error(f"获取同步记录详情失败: {e}")
            raise

    def update_sync_record_status(
        self, record_id: int, status: str, message: str = ""
    ) -> bool:
        """更新同步记录的状态"""
        try:

            def _write(conn):
                cursor = conn.execute(
                    """
                    UPDATE sync_records
                    SET status = ?, message = ?
                    WHERE id = ?
                """,
                    (status, message, record_id),
                )
                conn.commit()
                return cursor.rowcount

            affected_rows = self._execute_with_lock(_write)
            if affected_rows > 0:
                if status in ("success", "retried"):
                    self.mark_notifications_read_by_ref_id(record_id)
                return True
            else:
                logger.warning(f"记录 {record_id} 不存在，无法更新")
                return False
        except Exception as e:
            logger.error(f"更新同步记录状态失败: {e}")
            return False

    def backfill_historical_error_notifications(self) -> int:
        """将历史 error 同步记录回填为已读通知（仅执行一次）。"""
        if self.get_feiniu_meta(INBOX_ERROR_BACKFILL_META_KEY):
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

            count = int(self._execute_with_lock(_write) or 0)
            if count:
                logger.info(f"历史同步失败通知已回填（已读）: {count} 条")
            return count
        except Exception as e:
            logger.error(f"历史同步失败通知回填失败: {e}")
            return 0

    def get_sync_stats(self) -> dict[str, Any]:
        """获取同步统计信息"""
        try:

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

            return self._execute_with_lock(_read)
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            raise

    def get_heatmap_stats(self) -> list[dict[str, Any]]:
        """获取热力图数据（过去365天每天同步数），带5分钟缓存"""
        now = time.time()
        if self._heatmap_cache is not None and now - self._heatmap_cache_time < 300:
            return self._heatmap_cache

        try:

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

            result = self._execute_with_lock(_read)
            self._heatmap_cache = result
            self._heatmap_cache_time = now
            return result
        except Exception as e:
            logger.error(f"获取热力图数据失败: {e}")
            raise

    # ===== Trakt 配置相关方法 =====

    def save_trakt_config(self, config: dict) -> bool:
        """保存或更新 Trakt 配置"""
        try:

            def _write(conn):
                self._ensure_trakt_config_sync_filter(conn.cursor())
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM trakt_config WHERE user_id = ?",
                    (config["user_id"],),
                )
                existing = cursor.fetchone()

                if existing:
                    cursor.execute(
                        """
                        UPDATE trakt_config SET
                            access_token = ?,
                            refresh_token = ?,
                            expires_at = ?,
                            enabled = ?,
                            sync_interval = ?,
                            sync_filter_enabled = ?,
                            last_sync_time = ?,
                            updated_at = ?
                        WHERE user_id = ?
                    """,
                        (
                            config["access_token"],
                            config["refresh_token"],
                            config["expires_at"],
                            1 if config.get("enabled", True) else 0,
                            config.get("sync_interval", "0 */6 * * *"),
                            1 if config.get("sync_filter_enabled", True) else 0,
                            config.get("last_sync_time"),
                            int(datetime.now().timestamp()),
                            config["user_id"],
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO trakt_config
                        (user_id, access_token, refresh_token, expires_at, enabled,
                         sync_interval, sync_filter_enabled, last_sync_time, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            config["user_id"],
                            config["access_token"],
                            config["refresh_token"],
                            config["expires_at"],
                            1 if config.get("enabled", True) else 0,
                            config.get("sync_interval", "0 */6 * * *"),
                            1 if config.get("sync_filter_enabled", True) else 0,
                            config.get("last_sync_time"),
                            config.get("created_at", int(datetime.now().timestamp())),
                            int(datetime.now().timestamp()),
                        ),
                    )

                conn.commit()

            self._execute_with_lock(_write)
            return True
        except Exception as e:
            logger.error(f"保存 Trakt 配置失败: {e}")
            return False

    def get_trakt_config(self, user_id: str) -> Optional[dict]:
        """获取用户的 Trakt 配置"""
        try:

            def _read(conn):
                self._ensure_trakt_config_sync_filter(conn.cursor())
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT id, user_id, access_token, refresh_token,
                              expires_at, enabled, sync_interval,
                              sync_filter_enabled, last_sync_time,
                              created_at, updated_at
                       FROM trakt_config WHERE user_id = ?""",
                    (user_id,),
                )
                return cursor.fetchone()

            row = self._execute_with_lock(_read)
            if not row:
                return None

            columns = [
                "id",
                "user_id",
                "access_token",
                "refresh_token",
                "expires_at",
                "enabled",
                "sync_interval",
                "sync_filter_enabled",
                "last_sync_time",
                "created_at",
                "updated_at",
            ]
            config = dict(zip(columns, row))
            config["enabled"] = bool(config["enabled"])
            config["sync_filter_enabled"] = bool(config["sync_filter_enabled"])
            return config
        except Exception as e:
            logger.error(f"获取 Trakt 配置失败: {e}")
            return None

    def delete_trakt_config(self, user_id: str) -> bool:
        """删除用户的 Trakt 配置"""
        try:

            def _write(conn):
                cursor = conn.execute(
                    "DELETE FROM trakt_config WHERE user_id = ?", (user_id,)
                )
                conn.commit()
                return cursor.rowcount

            affected = self._execute_with_lock(_write)
            return affected > 0
        except Exception as e:
            logger.error(f"删除 Trakt 配置失败: {e}")
            return False

    def save_trakt_sync_history(self, history: dict) -> bool:
        """保存 Trakt 同步历史记录"""
        try:

            def _write(conn):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO trakt_sync_history
                    (user_id, trakt_item_id, media_type, watched_at, synced_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        history["user_id"],
                        history["trakt_item_id"],
                        history["media_type"],
                        history["watched_at"],
                        history.get("synced_at", int(datetime.now().timestamp())),
                    ),
                )
                conn.commit()

            self._execute_with_lock(_write)
            return True
        except Exception as e:
            logger.error(f"保存 Trakt 同步历史失败: {e}")
            return False

    def get_trakt_sync_history(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> dict:
        """获取用户的 Trakt 同步历史"""
        try:

            def _read(conn):
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT COUNT(*) FROM trakt_sync_history WHERE user_id = ?",
                    (user_id,),
                )
                total = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT id, user_id, trakt_item_id, media_type, watched_at, synced_at
                    FROM trakt_sync_history
                    WHERE user_id = ?
                    ORDER BY watched_at DESC
                    LIMIT ? OFFSET ?
                """,
                    (user_id, limit, offset),
                )

                records = []
                for row in cursor.fetchall():
                    records.append(
                        {
                            "id": row[0],
                            "user_id": row[1],
                            "trakt_item_id": row[2],
                            "media_type": row[3],
                            "watched_at": row[4],
                            "synced_at": row[5],
                        }
                    )

                return {
                    "records": records,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }

            return self._execute_with_lock(_read)
        except Exception as e:
            logger.error(f"获取 Trakt 同步历史失败: {e}")
            raise

    def get_last_sync_time(self, user_id: str) -> Optional[int]:
        """获取用户最后同步时间"""
        try:

            def _read(conn):
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT MAX(watched_at) FROM trakt_sync_history WHERE user_id = ?
                """,
                    (user_id,),
                )
                return cursor.fetchone()[0]

            return self._execute_with_lock(_read)
        except Exception as e:
            logger.error(f"获取最后同步时间失败: {e}")
            return None

    def get_trakt_configs_with_sync_enabled(self) -> list[dict]:
        """获取所有启用同步的 Trakt 配置"""
        try:

            def _read(conn):
                self._ensure_trakt_config_sync_filter(conn.cursor())
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT id, user_id, access_token, refresh_token,
                              expires_at, enabled, sync_interval,
                              sync_filter_enabled, last_sync_time,
                              created_at, updated_at
                       FROM trakt_config WHERE enabled = 1"""
                )
                return cursor.fetchall()

            rows = self._execute_with_lock(_read)
            if not rows:
                return []

            columns = [
                "id",
                "user_id",
                "access_token",
                "refresh_token",
                "expires_at",
                "enabled",
                "sync_interval",
                "sync_filter_enabled",
                "last_sync_time",
                "created_at",
                "updated_at",
            ]
            configs = []
            for row in rows:
                config = dict(zip(columns, row))
                config["enabled"] = bool(config["enabled"])
                config["sync_filter_enabled"] = bool(config["sync_filter_enabled"])
                configs.append(config)

            return configs
        except Exception as e:
            logger.error(f"获取启用同步的 Trakt 配置失败: {e}")
            return []

    # ===== 飞牛同步历史 =====

    def save_feiniu_sync_history(
        self,
        fn_user_guid: str,
        item_guid: str,
        update_time_snapshot: Optional[int] = None,
    ) -> bool:
        """记录已提交的飞牛条目同步（去重用）"""
        try:

            def _write(conn):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO feiniu_sync_history
                    (fn_user_guid, item_guid, synced_at, update_time_snapshot)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        fn_user_guid,
                        item_guid,
                        int(datetime.now().timestamp()),
                        update_time_snapshot,
                    ),
                )
                conn.commit()

            self._execute_with_lock(_write)
            return True
        except Exception as e:
            logger.error(f"保存飞牛同步历史失败: {e}")
            return False

    def get_feiniu_meta(self, key: str) -> Optional[str]:
        try:

            def _read(conn):
                cursor = conn.execute(
                    "SELECT value FROM feiniu_meta WHERE key = ? LIMIT 1", (key,)
                )
                return cursor.fetchone()

            row = self._execute_with_lock(_read)
            return str(row[0]) if row else None
        except Exception as e:
            logger.warning(f"读取飞牛 meta 失败: {e}")
            return None

    def set_feiniu_meta(self, key: str, value: str) -> bool:
        try:

            def _write(conn):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO feiniu_meta (key, value) VALUES (?, ?)
                    """,
                    (key, value),
                )
                conn.commit()

            self._execute_with_lock(_write)
            return True
        except Exception as e:
            logger.error(f"写入飞牛 meta 失败: {e}")
            return False

    def delete_feiniu_meta(self, key: str) -> bool:
        try:

            def _write(conn):
                conn.execute("DELETE FROM feiniu_meta WHERE key = ?", (key,))
                conn.commit()

            self._execute_with_lock(_write)
            return True
        except Exception as e:
            logger.error(f"删除飞牛 meta 失败: {e}")
            return False

    def get_feiniu_synced_set(self, user_guids: list[str]) -> set[tuple[str, str]]:
        """批量获取已同步的飞牛条目集合，用于 O(1) 去重查找"""
        if not user_guids:
            return set()
        try:

            def _read(conn):
                cursor = conn.cursor()
                placeholders = ",".join("?" * len(user_guids))
                cursor.execute(
                    f"SELECT fn_user_guid, item_guid FROM feiniu_sync_history WHERE fn_user_guid IN ({placeholders})",
                    user_guids,
                )
                return {(row[0], row[1]) for row in cursor.fetchall()}

            return self._execute_with_lock(_read)
        except Exception as e:
            logger.warning(f"批量查询飞牛同步历史失败: {e}")
            return set()

    def get_trakt_synced_set(self, user_id: str) -> set[tuple[str, int]]:
        """批量获取已同步的 Trakt 条目集合，用于 O(1) 去重查找"""
        try:

            def _read(conn):
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT trakt_item_id, watched_at FROM trakt_sync_history WHERE user_id = ?",
                    (user_id,),
                )
                return {(row[0], row[1]) for row in cursor.fetchall()}

            return self._execute_with_lock(_read)
        except Exception as e:
            logger.warning(f"批量查询 Trakt 同步历史失败: {e}")
            return set()

    def clear_feiniu_min_update_watermark(self) -> None:
        """清除「启用后仅同步新进度」水位（飞牛关闭时调用）"""
        self.delete_feiniu_meta(FEINIU_MIN_UPDATE_WATERMARK_META_KEY)

    def set_feiniu_min_update_watermark_now(self) -> int:
        """将同步起点设为当前时刻（Web 勾选启用并保存时调用，不追溯历史）"""
        now_ms = int(time.time() * 1000)
        self.set_feiniu_meta(FEINIU_MIN_UPDATE_WATERMARK_META_KEY, str(now_ms))
        logger.info("飞牛：同步起点水位已设为当前时刻（仅此后库内更新的记录参与同步）")
        return now_ms

    def get_or_create_feiniu_min_update_watermark_ms(self) -> int:
        """返回飞牛库 update_time 下限（毫秒）。首次调用时写入当前时刻，不追溯历史。"""
        existing = self.get_feiniu_meta(FEINIU_MIN_UPDATE_WATERMARK_META_KEY)
        if existing is not None:
            try:
                return int(existing)
            except ValueError:
                pass
        now_ms = int(time.time() * 1000)
        self.set_feiniu_meta(FEINIU_MIN_UPDATE_WATERMARK_META_KEY, str(now_ms))
        logger.info(
            "飞牛：已建立同步起点水位（仅此后在库中更新的观看记录会参与同步，不追溯启用前的存量）"
        )
        return now_ms

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

            return self._execute_with_lock(_read) or []
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

            return self._execute_with_lock(_read)
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

            return bool(self._execute_with_lock(_write))
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

            return int(self._execute_with_lock(_write) or 0)
        except Exception as e:
            logger.error(f"按同步记录标记通知已读失败: {e}")
            return 0

    def mark_notification_group_read(self, notification_id: int) -> int:
        """将同一番剧聚合组内的未读通知全部标为已读。"""
        from ..utils.inbox_notifications import notification_group_key

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

            return int(self._execute_with_lock(_write) or 0)
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

            return int(self._execute_with_lock(_write) or 0)
        except Exception as e:
            logger.error(f"全部通知已读失败: {e}")
            return 0

    def get_read_announcement_ids(self) -> set[str]:
        try:

            def _read(conn):
                cursor = conn.execute(
                    "SELECT announcement_id FROM announcement_read_state"
                )
                return {row[0] for row in cursor.fetchall()}

            return self._execute_with_lock(_read) or set()
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

            return bool(self._execute_with_lock(_write))
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

            return int(self._execute_with_lock(_write) or 0)
        except Exception as e:
            logger.error(f"全部公告已读失败: {e}")
            return 0

    def count_unread_notifications(self) -> int:
        try:

            def _read(conn):
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM in_app_notifications WHERE read_at IS NULL"
                )
                return int(cursor.fetchone()[0])

            return int(self._execute_with_lock(_read) or 0)
        except Exception as e:
            logger.error(f"统计未读通知失败: {e}")
            return 0


# 全局数据库实例
database_manager = DatabaseManager()
