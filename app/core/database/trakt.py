"""
Trakt 配置与同步历史仓库
"""

from datetime import datetime
from typing import Optional

from .base_repository import BaseRepository


class TraktRepository(BaseRepository):
    """Trakt 配置与同步历史的增删改查"""

    def __init__(self, conn):
        super().__init__(conn)

    def save_trakt_config(self, config: dict) -> bool:
        """保存或更新 Trakt 配置"""
        def _write(conn):
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
            return True

        return self._run_write(
            _write,
            error_msg="保存 Trakt 配置失败",
            default=False,
            ensure_schema=self._conn._ensure_trakt_config_sync_filter,
        )

    def get_trakt_config(self, user_id: str) -> Optional[dict]:
        """获取用户的 Trakt 配置"""
        def _read(conn):
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

        row = self._run_read(
            _read,
            error_msg="获取 Trakt 配置失败",
            default=None,
            ensure_schema=self._conn._ensure_trakt_config_sync_filter,
        )
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

    def delete_trakt_config(self, user_id: str) -> bool:
        """删除用户的 Trakt 配置"""
        def _write(conn):
            cursor = conn.execute(
                "DELETE FROM trakt_config WHERE user_id = ?", (user_id,)
            )
            return cursor.rowcount

        affected = self._run_write(
            _write, error_msg="删除 Trakt 配置失败", default=False
        )
        return affected > 0

    def save_trakt_sync_history(self, history: dict) -> bool:
        """保存 Trakt 同步历史记录"""
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
            return True

        return self._run_write(
            _write, error_msg="保存 Trakt 同步历史失败", default=False
        )

    def get_trakt_sync_history(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> dict:
        """获取用户的 Trakt 同步历史"""
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

        return self._run_read(
            _read, error_msg="获取 Trakt 同步历史失败", reraise=True
        )

    def get_last_sync_time(self, user_id: str) -> Optional[int]:
        """获取用户最后同步时间"""
        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT MAX(watched_at) FROM trakt_sync_history WHERE user_id = ?
            """,
                (user_id,),
            )
            return cursor.fetchone()[0]

        return self._run_read(
            _read, error_msg="获取最后同步时间失败", default=None
        )

    def get_trakt_configs_with_sync_enabled(self) -> list[dict]:
        """获取所有启用同步的 Trakt 配置"""
        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, user_id, access_token, refresh_token,
                          expires_at, enabled, sync_interval,
                          sync_filter_enabled, last_sync_time,
                          created_at, updated_at
                   FROM trakt_config WHERE enabled = 1"""
            )
            return cursor.fetchall()

        rows = self._run_read(
            _read,
            error_msg="获取启用同步的 Trakt 配置失败",
            default=[],
            ensure_schema=self._conn._ensure_trakt_config_sync_filter,
        )
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

    def get_trakt_synced_set(self, user_id: str) -> set[tuple[str, int]]:
        """批量获取已同步的 Trakt 条目集合，用于 O(1) 去重查找"""
        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                "SELECT trakt_item_id, watched_at FROM trakt_sync_history WHERE user_id = ?",
                (user_id,),
            )
            return {(row[0], row[1]) for row in cursor.fetchall()}

        return self._run_read(
            _read, error_msg="批量查询 Trakt 同步历史失败", default=set()
        )
