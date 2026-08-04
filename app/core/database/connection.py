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
            # 所有环境（Docker/直装）默认统一放到 data/ 目录，便于与其他数据隔离
            db_path = "data/sync_records.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # 自动迁移：项目根目录下存在旧版 sync_records.db 时，移动到新路径
        # 覆盖 Docker 与直装场景，确保现有用户升级后数据不丢失
        if auto:
            legacy = Path("sync_records.db")
            if not self.db_path.exists() and legacy.is_file():
                shutil.move(str(legacy), str(self.db_path))
                logger.info(f"已从旧路径迁移数据库 {legacy} -> {self.db_path}")

        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._media_type_migrated = False
        self._bgm_title_migrated = False
        self._trakt_filter_migrated = False
        self._match_fields_migrated = False
        self._pending_sync_sync_record_id_migrated = False
        self._pending_candidates_sync_record_id_migrated = False
        self._bangumi_accounts_private_migrated = False
        self._tokens_encrypted_migrated = False
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
        """在锁保护下执行数据库操作。

        异常时主动 rollback，避免未提交事务悬挂在连接上被下一次写操作
        意外提交（SQLite 默认 deferred 隔离，DML 一旦执行即开启事务）。
        """
        with self._lock:
            conn = self._get_connection()
            try:
                return fn(conn)
            except Exception:
                # rollback 必须在锁内执行，避免与并发写操作的 commit 交织
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

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

    def _ensure_sync_records_match_fields(self, cursor) -> None:
        """旧库迁移：为 sync_records 增加匹配追踪字段。

        新增 4 列：
        - match_method: 匹配方式（custom_mapping/bangumi_data/archive/api_search/failed）
        - match_score: 最佳匹配置信度（0-1）
        - match_platform: 命中条目的 platform（TV/OVA/剧场版/日剧/电影...）
        - match_trace: JSON 字符串，完整匹配过程（仅 debug 模式写入）
        """
        if self._match_fields_migrated:
            return
        cursor.execute("PRAGMA table_info(sync_records)")
        cols = [row[1] for row in cursor.fetchall()]
        need_commit = False
        for col, decl in [
            ("match_method", "TEXT DEFAULT ''"),
            ("match_score", "REAL"),
            ("match_platform", "TEXT DEFAULT ''"),
            ("match_trace", "TEXT DEFAULT ''"),
        ]:
            if col not in cols:
                cursor.execute(f"ALTER TABLE sync_records ADD COLUMN {col} {decl}")
                need_commit = True
        if need_commit:
            logger.info(
                "sync_records 已迁移：增加匹配追踪字段（match_method/match_score/match_platform/match_trace）"
            )
        self._match_fields_migrated = True

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

    def _ensure_pending_sync_queue_sync_record_id(self, cursor) -> None:
        """旧库迁移：为 pending_sync_queue 增加 sync_record_id（关联 sync_records 行）。

        用于补发回写：补发成功/放弃时，通过此字段定位原始 queued 同步记录，
        把 status 从 queued 改为 success/error，形成状态闭环。
        """
        if self._pending_sync_sync_record_id_migrated:
            return
        cursor.execute("PRAGMA table_info(pending_sync_queue)")
        cols = [row[1] for row in cursor.fetchall()]
        if "sync_record_id" in cols:
            self._pending_sync_sync_record_id_migrated = True
            return
        cursor.execute(
            "ALTER TABLE pending_sync_queue ADD COLUMN sync_record_id INTEGER"
        )
        self._pending_sync_sync_record_id_migrated = True
        logger.info("pending_sync_queue 已迁移：增加 sync_record_id 列")

    def _ensure_pending_candidates_sync_record_id(self, cursor) -> None:
        """旧库迁移：为 pending_candidates 增加 sync_record_id（关联 sync_records 行）。

        用于候选确认后回写原 sync_records 状态（error → retried/success），
        形成「候选确认即补发」的闭环。
        """
        if self._pending_candidates_sync_record_id_migrated:
            return
        cursor.execute("PRAGMA table_info(pending_candidates)")
        cols = [row[1] for row in cursor.fetchall()]
        if "sync_record_id" in cols:
            self._pending_candidates_sync_record_id_migrated = True
            return
        cursor.execute(
            "ALTER TABLE pending_candidates ADD COLUMN sync_record_id INTEGER"
        )
        self._pending_candidates_sync_record_id_migrated = True
        logger.info("pending_candidates 已迁移：增加 sync_record_id 列")

    def _ensure_bangumi_accounts_private(self, cursor) -> None:
        """旧库迁移：为 bangumi_accounts 增加 private（收藏是否私有）。

        与 INI [bangumi(-*)] private 字段对齐；DEFAULT 0 即公开，与既有 INI 默认值一致。
        """
        if self._bangumi_accounts_private_migrated:
            return
        cursor.execute("PRAGMA table_info(bangumi_accounts)")
        cols = [row[1] for row in cursor.fetchall()]
        if "private" in cols:
            self._bangumi_accounts_private_migrated = True
            return
        cursor.execute(
            "ALTER TABLE bangumi_accounts ADD COLUMN private BOOLEAN NOT NULL DEFAULT 0"
        )
        self._bangumi_accounts_private_migrated = True
        logger.info("bangumi_accounts 已迁移：增加 private 列")

    def _ensure_tokens_encrypted(self, cursor) -> None:
        """一次性数据迁移：加密 DB 中的明文 token（access_token / refresh_token）。

        仓储层已改为写入即加密，此处仅处理迁移前已落库的历史明文。
        ``encrypt`` 幂等（已带 BGS1: 前缀则原样返回），``secret_key`` 为空时
        返回明文（不设迁移标记，下次启动重试）。
        """
        if self._tokens_encrypted_migrated:
            return
        try:
            from ..config_secret_crypto import PREFIX, _master_secret, encrypt

            # 统一通过 _master_secret() 取密钥，与仓储层加解密保持一致
            master = _master_secret()
            if not master:
                return

            # bangumi_accounts
            cursor.execute(
                "SELECT section_name, access_token, refresh_token FROM bangumi_accounts "
                "WHERE (access_token IS NOT NULL AND access_token != '' AND access_token NOT LIKE ?) "
                "OR (refresh_token IS NOT NULL AND refresh_token != '' AND refresh_token NOT LIKE ?)",
                (PREFIX + "%", PREFIX + "%"),
            )
            for section, at, rt in cursor.fetchall():
                new_at = encrypt(at or "", master=master)
                new_rt = encrypt(rt or "", master=master)
                cursor.execute(
                    "UPDATE bangumi_accounts SET access_token = ?, refresh_token = ? "
                    "WHERE section_name = ?",
                    (new_at, new_rt, section),
                )

            # trakt_config
            cursor.execute(
                "SELECT user_id, access_token, refresh_token FROM trakt_config "
                "WHERE (access_token IS NOT NULL AND access_token != '' AND access_token NOT LIKE ?) "
                "OR (refresh_token IS NOT NULL AND refresh_token != '' AND refresh_token NOT LIKE ?)",
                (PREFIX + "%", PREFIX + "%"),
            )
            for user_id, at, rt in cursor.fetchall():
                new_at = encrypt(at or "", master=master)
                new_rt = encrypt(rt or "", master=master)
                cursor.execute(
                    "UPDATE trakt_config SET access_token = ?, refresh_token = ? "
                    "WHERE user_id = ?",
                    (new_at, new_rt, user_id),
                )
            self._tokens_encrypted_migrated = True
        except Exception as e:
            logger.warning(f"token 加密迁移失败（将在下次启动重试）: {e}")

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
                bgm_title TEXT DEFAULT '',
                match_method TEXT DEFAULT '',
                match_score REAL,
                match_platform TEXT DEFAULT '',
                match_trace TEXT DEFAULT ''
            )
        """)

        self._ensure_sync_records_media_type(cursor)
        self._ensure_sync_records_bgm_title(cursor)
        self._ensure_sync_records_match_fields(cursor)

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                request_title TEXT NOT NULL,
                request_ori_title TEXT DEFAULT '',
                request_season INTEGER DEFAULT 1,
                request_episode INTEGER DEFAULT 0,
                user_name TEXT DEFAULT '',
                source TEXT DEFAULT '',
                candidates_json TEXT DEFAULT '[]',
                trace_json TEXT DEFAULT '{}',
                status TEXT DEFAULT 'pending',
                confirmed_subject_id TEXT DEFAULT '',
                resolved_at DATETIME,
                sync_record_id INTEGER
            )
        """)
        self._ensure_pending_candidates_sync_record_id(cursor)

        # 待同步队列：Bangumi API 不可达时缓存已匹配的同步请求，API 恢复后补发
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_sync_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_name TEXT NOT NULL,
                title TEXT NOT NULL,
                season INTEGER DEFAULT 1,
                episode INTEGER DEFAULT 0,
                subject_id TEXT NOT NULL,
                episode_id TEXT,
                source TEXT DEFAULT '',
                media_type TEXT DEFAULT 'episode',
                payload_json TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                last_attempt_at DATETIME,
                last_error TEXT,
                sync_record_id INTEGER
            )
        """)
        self._ensure_pending_sync_queue_sync_record_id(cursor)

        # Bangumi 账号（含 OAuth 令牌）：以「账号列表」为唯一真相源，
        # 取代散落在 INI 各 [bangumi-*] 段的配置。
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bangumi_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section_name TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL DEFAULT '',
                media_server_usernames TEXT NOT NULL DEFAULT '[]',
                auth_method TEXT NOT NULL DEFAULT 'manual',
                access_token TEXT,
                refresh_token TEXT,
                token_type TEXT DEFAULT 'Bearer',
                expires_at INTEGER,
                bangumi_user_id TEXT DEFAULT '',
                nickname TEXT DEFAULT '',
                avatar TEXT DEFAULT '',
                private BOOLEAN NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        self._ensure_bangumi_accounts_private(cursor)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_bangumi_accounts_section "
            "ON bangumi_accounts(section_name)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_bangumi_accounts_active "
            "ON bangumi_accounts(is_active)"
        )

        # OAuth 授权过程中的 CSRF state（临时会话，带 TTL），替代临时 JSON 文件。
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                section_name TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                redirect_uri TEXT NOT NULL DEFAULT ''
            )
        """)
        # 兼容旧库：补充 provider 列（已存在则忽略）
        try:
            cursor.execute(
                "ALTER TABLE oauth_states ADD COLUMN provider TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass
        # 兼容旧库：补充 redirect_uri 列（用于存发起授权时的 redirect_uri，
        # 回调时还原以保证 authorize 与 token 交换用同一 redirect_uri）
        try:
            cursor.execute(
                "ALTER TABLE oauth_states ADD COLUMN redirect_uri TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_states_expire "
            "ON oauth_states(expires_at)"
        )

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
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_candidates_status ON pending_candidates(status)"
        )
        # 部分唯一索引：同一 (title, season, user, source) 仅允许一个 pending 行，
        # 用于 pending_candidates 去重（upsert 依赖此索引）
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_candidates_dedup "
            "ON pending_candidates(request_title, request_season, user_name, source) "
            "WHERE status = 'pending'"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_sync_queue_status ON pending_sync_queue(status)"
        )
        # 部分唯一索引：同一用户同一集仅保留一条 pending 行（episode_id 为空时退化为按 subject_id 去重）
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_sync_queue_dedup "
            "ON pending_sync_queue(user_name, subject_id, COALESCE(episode_id, ''), source) "
            "WHERE status = 'pending'"
        )
        # sync_record_id 反查索引：加速 mark_synced_by_sync_record_id 与
        # get_pending_candidate_by_sync_record_id（原为全表扫描）
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_sync_queue_sync_record_id "
            "ON pending_sync_queue(sync_record_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_candidates_sync_record_id "
            "ON pending_candidates(sync_record_id)"
        )

        # 一次性数据迁移：加密历史明文 token（仓储层已改为写入即加密）
        self._ensure_tokens_encrypted(cursor)

        conn.commit()
        logger.info(f"数据库初始化完成: {self.db_path}")
        # 注意：backfill_historical_error_notifications 由 DatabaseManager facade
        # 在所有 repository 创建完成后调用（避免此处对 inbox_repository 的循环依赖）。
