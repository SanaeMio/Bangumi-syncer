"""
数据库管理模块
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .logging import logger


class DatabaseManager:
    """数据库管理器"""

    def __init__(self, db_path: str = "sync_records.db"):
        self.db_path = Path(db_path)
        self._init_database()

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
                source TEXT NOT NULL
            )
        """)

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
    ) -> None:
        """记录同步日志到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 使用本地时间而不是UTC时间
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute(
                """
                INSERT INTO sync_records
                (timestamp, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

            # 获取记录
            query = f"""
                SELECT id, timestamp, user_name, title, ori_title, season, episode,
                       subject_id, episode_id, status, message, source
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

            cursor.execute(
                """
                SELECT id, timestamp, user_name, title, ori_title, season, episode,
                       subject_id, episode_id, status, message, source
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


# 全局数据库实例
database_manager = DatabaseManager()
