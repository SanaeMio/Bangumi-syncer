"""
数据库管理模块
"""

import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .logging import logger

# 飞牛「启用后不同步历史」水位键（sync_records.db / feiniu_meta）
FEINIU_MIN_UPDATE_WATERMARK_META_KEY = "min_update_watermark_ms"


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

        self._init_database()

    def _ensure_sync_records_media_type(self, cursor) -> None:
        """旧库迁移：为 sync_records 增加 media_type（历史数据为 episode）。"""
        cursor.execute("PRAGMA table_info(sync_records)")
        cols = [row[1] for row in cursor.fetchall()]
        if "media_type" in cols:
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
        logger.info("sync_records 已迁移：增加 media_type 列并回填 episode")

    def _init_database(self) -> None:
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
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
                media_type TEXT NOT NULL DEFAULT 'episode'
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

        conn.commit()
        conn.close()
        logger.info(f"数据库初始化完成: {self.db_path}")

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
    ) -> None:
        """记录同步日志到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            self._ensure_sync_records_media_type(cursor)

            # 使用本地时间而不是UTC时间
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute(
                """
                INSERT INTO sync_records
                (timestamp, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source, media_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"记录同步日志失败: {e}")

    def get_sync_records(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        user_name: Optional[str] = None,
        source: Optional[str] = None,
        source_prefix: Optional[str] = None,
    ) -> dict[str, Any]:
        """获取同步记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            self._ensure_sync_records_media_type(cursor)

            # 构建查询条件
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

            # 获取总数
            count_query = f"SELECT COUNT(*) FROM sync_records{where_clause}"
            cursor.execute(count_query, params)
            total = cursor.fetchone()[0]

            query = f"""
                SELECT id, timestamp, user_name, title, ori_title, season, episode,
                       subject_id, episode_id, status, message, source, media_type
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
                    }
                )

            conn.close()

            return {
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        except Exception as e:
            logger.error(f"获取同步记录失败: {e}")
            raise

    def get_sync_record_by_id(self, record_id: int) -> Optional[dict[str, Any]]:
        """根据ID获取单个同步记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            self._ensure_sync_records_media_type(cursor)

            cursor.execute(
                """
                SELECT id, timestamp, user_name, title, ori_title, season, episode,
                       subject_id, episode_id, status, message, source, media_type
                FROM sync_records
                WHERE id = ?
            """,
                (record_id,),
            )

            row = cursor.fetchone()
            conn.close()

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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                UPDATE sync_records
                SET status = ?, message = ?
                WHERE id = ?
            """,
                (status, message, record_id),
            )

            conn.commit()
            affected_rows = cursor.rowcount
            conn.close()

            if affected_rows > 0:
                return True
            else:
                logger.warning(f"记录 {record_id} 不存在，无法更新")
                return False
        except Exception as e:
            logger.error(f"更新同步记录状态失败: {e}")
            return False

    def get_sync_stats(self) -> dict[str, Any]:
        """获取同步统计信息"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 总同步次数
            cursor.execute("SELECT COUNT(*) FROM sync_records")
            total_syncs = cursor.fetchone()[0]

            # 成功同步次数
            cursor.execute("SELECT COUNT(*) FROM sync_records WHERE status = 'success'")
            success_syncs = cursor.fetchone()[0]

            # 失败同步次数
            cursor.execute("SELECT COUNT(*) FROM sync_records WHERE status = 'error'")
            error_syncs = cursor.fetchone()[0]

            # 今日同步次数
            cursor.execute(
                "SELECT COUNT(*) FROM sync_records WHERE DATE(timestamp) = DATE('now')"
            )
            today_syncs = cursor.fetchone()[0]

            # 用户统计
            cursor.execute(
                "SELECT user_name, COUNT(*) FROM sync_records GROUP BY user_name ORDER BY COUNT(*) DESC"
            )
            user_stats = [
                {"user": row[0], "count": row[1]} for row in cursor.fetchall()
            ]

            # 最近7天统计
            cursor.execute("""
                SELECT DATE(timestamp) as date, COUNT(*) as count
                FROM sync_records
                WHERE timestamp >= datetime('now', '-7 days')
                GROUP BY DATE(timestamp)
                ORDER BY date
            """)
            daily_stats = [
                {"date": row[0], "count": row[1]} for row in cursor.fetchall()
            ]

            conn.close()

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
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            raise

    # ===== Trakt 配置相关方法 =====

    def save_trakt_config(self, config: dict) -> bool:
        """保存或更新 Trakt 配置"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 检查是否存在
            cursor.execute(
                "SELECT id FROM trakt_config WHERE user_id = ?", (config["user_id"],)
            )
            existing = cursor.fetchone()

            if existing:
                # 更新现有配置
                cursor.execute(
                    """
                    UPDATE trakt_config SET
                        access_token = ?,
                        refresh_token = ?,
                        expires_at = ?,
                        enabled = ?,
                        sync_interval = ?,
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
                        config.get("last_sync_time"),
                        int(datetime.now().timestamp()),
                        config["user_id"],
                    ),
                )
            else:
                # 插入新配置
                cursor.execute(
                    """
                    INSERT INTO trakt_config
                    (user_id, access_token, refresh_token, expires_at, enabled,
                     sync_interval, last_sync_time, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        config["user_id"],
                        config["access_token"],
                        config["refresh_token"],
                        config["expires_at"],
                        1 if config.get("enabled", True) else 0,
                        config.get("sync_interval", "0 */6 * * *"),
                        config.get("last_sync_time"),
                        config.get("created_at", int(datetime.now().timestamp())),
                        int(datetime.now().timestamp()),
                    ),
                )

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"保存 Trakt 配置失败: {e}")
            return False

    def get_trakt_config(self, user_id: str) -> Optional[dict]:
        """获取用户的 Trakt 配置"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM trakt_config WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()

            conn.close()

            if not row:
                return None

            # 转换为字典
            columns = [
                "id",
                "user_id",
                "access_token",
                "refresh_token",
                "expires_at",
                "enabled",
                "sync_interval",
                "last_sync_time",
                "created_at",
                "updated_at",
            ]
            config = dict(zip(columns, row))
            # 转换布尔值
            config["enabled"] = bool(config["enabled"])
            return config
        except Exception as e:
            logger.error(f"获取 Trakt 配置失败: {e}")
            return None

    def delete_trakt_config(self, user_id: str) -> bool:
        """删除用户的 Trakt 配置"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("DELETE FROM trakt_config WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除 Trakt 配置失败: {e}")
            return False

    def save_trakt_sync_history(self, history: dict) -> bool:
        """保存 Trakt 同步历史记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 使用 INSERT OR REPLACE 来处理唯一约束冲突
            cursor.execute(
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
            conn.close()
            return True
        except Exception as e:
            logger.error(f"保存 Trakt 同步历史失败: {e}")
            return False

    def get_trakt_sync_history(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> dict:
        """获取用户的 Trakt 同步历史"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 获取总数
            cursor.execute(
                "SELECT COUNT(*) FROM trakt_sync_history WHERE user_id = ?", (user_id,)
            )
            total = cursor.fetchone()[0]

            # 获取记录
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

            conn.close()

            return {
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        except Exception as e:
            logger.error(f"获取 Trakt 同步历史失败: {e}")
            raise

    def get_last_sync_time(self, user_id: str) -> Optional[int]:
        """获取用户最后同步时间"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT MAX(watched_at) FROM trakt_sync_history WHERE user_id = ?
            """,
                (user_id,),
            )
            result = cursor.fetchone()[0]

            conn.close()
            return result
        except Exception as e:
            logger.error(f"获取最后同步时间失败: {e}")
            return None

    def get_trakt_configs_with_sync_enabled(self) -> list[dict]:
        """获取所有启用同步的 Trakt 配置"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM trakt_config WHERE enabled = 1
            """)
            rows = cursor.fetchall()

            conn.close()

            if not rows:
                return []

            # 转换为字典列表
            columns = [
                "id",
                "user_id",
                "access_token",
                "refresh_token",
                "expires_at",
                "enabled",
                "sync_interval",
                "last_sync_time",
                "created_at",
                "updated_at",
            ]
            configs = []
            for row in rows:
                config = dict(zip(columns, row))
                config["enabled"] = bool(config["enabled"])
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
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
            conn.close()
            return True
        except Exception as e:
            logger.error(f"保存飞牛同步历史失败: {e}")
            return False

    def is_feiniu_item_synced(self, fn_user_guid: str, item_guid: str) -> bool:
        """是否已为该飞牛用户与条目提交过同步"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM feiniu_sync_history
                WHERE fn_user_guid = ? AND item_guid = ?
                LIMIT 1
                """,
                (fn_user_guid, item_guid),
            )
            row = cursor.fetchone()
            conn.close()
            return row is not None
        except Exception as e:
            logger.warning(f"查询飞牛同步历史失败: {e}")
            return False

    def get_feiniu_meta(self, key: str) -> Optional[str]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM feiniu_meta WHERE key = ? LIMIT 1", (key,)
            )
            row = cursor.fetchone()
            conn.close()
            return str(row[0]) if row else None
        except Exception as e:
            logger.warning(f"读取飞牛 meta 失败: {e}")
            return None

    def set_feiniu_meta(self, key: str, value: str) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO feiniu_meta (key, value) VALUES (?, ?)
                """,
                (key, value),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"写入飞牛 meta 失败: {e}")
            return False

    def delete_feiniu_meta(self, key: str) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM feiniu_meta WHERE key = ?", (key,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"删除飞牛 meta 失败: {e}")
            return False

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


# 全局数据库实例
database_manager = DatabaseManager()
