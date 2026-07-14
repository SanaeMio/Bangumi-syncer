"""
数据库连接管理（连接、锁、schema 迁移）
"""

import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from ..logging import logger

# 飞牛「启用后不同步历史」水位键（sync_records.db / feiniu_meta）
FEINIU_MIN_UPDATE_WATERMARK_META_KEY = "min_update_watermark_ms"
# 历史 error 同步记录回填收件箱通知（一次性，标为已读）
INBOX_ERROR_BACKFILL_META_KEY = "inbox_error_backfill_v1"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class DatabaseConnection:
    """数据库连接管理器：负责连接管理、锁、schema 迁移"""

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
        # 注意：backfill_historical_error_notifications 由 DatabaseManager facade
        # 在所有 repository 创建完成后调用（避免此处对 inbox_repository 的循环依赖）。
