"""
飞牛同步历史与 meta 仓库
"""

import time
from datetime import datetime
from typing import Optional

from ..logging import logger
from .base_repository import BaseRepository
from .connection import FEINIU_MIN_UPDATE_WATERMARK_META_KEY


class FeiniuRepository(BaseRepository):
    """飞牛同步历史与 meta 的增删改查"""

    def __init__(self, conn):
        super().__init__(conn)

    def save_feiniu_sync_history(
        self,
        fn_user_guid: str,
        item_guid: str,
        update_time_snapshot: Optional[int] = None,
    ) -> bool:
        """记录已提交的飞牛条目同步（去重用）"""

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
            return True

        return self._run_write(_write, error_msg="保存飞牛同步历史失败", default=False)

    def get_feiniu_synced_set(self, user_guids: list[str]) -> set[tuple[str, str]]:
        """批量获取已同步的飞牛条目集合，用于 O(1) 去重查找"""
        if not user_guids:
            return set()

        def _read(conn):
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(user_guids))
            cursor.execute(
                f"SELECT fn_user_guid, item_guid FROM feiniu_sync_history WHERE fn_user_guid IN ({placeholders})",
                user_guids,
            )
            return {(row[0], row[1]) for row in cursor.fetchall()}

        return self._run_read(
            _read, error_msg="批量查询飞牛同步历史失败", default=set()
        )

    def get_feiniu_meta(self, key: str) -> Optional[str]:
        def _read(conn):
            cursor = conn.execute(
                "SELECT value FROM feiniu_meta WHERE key = ? LIMIT 1", (key,)
            )
            return cursor.fetchone()

        row = self._run_read(_read, error_msg="读取飞牛 meta 失败", default=None)
        return str(row[0]) if row else None

    def set_feiniu_meta(self, key: str, value: str) -> bool:
        def _write(conn):
            conn.execute(
                """
                INSERT OR REPLACE INTO feiniu_meta (key, value) VALUES (?, ?)
                """,
                (key, value),
            )
            return True

        return self._run_write(_write, error_msg="写入飞牛 meta 失败", default=False)

    def delete_feiniu_meta(self, key: str) -> bool:
        def _write(conn):
            conn.execute("DELETE FROM feiniu_meta WHERE key = ?", (key,))
            return True

        return self._run_write(_write, error_msg="删除飞牛 meta 失败", default=False)

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
