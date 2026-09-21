"""负样本黑名单仓库（reject 学习）

用户在前端拒绝某条待确认候选时，把被拒 ``subject_id`` 记入该标题的黑名单，
下次自动匹配命中相同 ``subject_id`` 时直接排除（宁可漏标，不能错标）。

黑名单按标题归一化键（小写去空白）作用，与具体来源 / 用户无关：
同一标题无论来自哪个媒体源、哪个用户被拒绝，后续自动匹配都会避开该 subject。
用户显式自定义映射（custom_mapping）不受影响，因为它代表明确意图。
"""

from __future__ import annotations

from .base_repository import BaseRepository


class TitleBlacklistRepository(BaseRepository):
    """标题级负样本黑名单：title -> {subject_id, ...}"""

    _TABLE = "title_blacklist"

    def __init__(self, conn):
        super().__init__(conn)
        self._ensure_table()

    # ------------------------------------------------------------------
    # 表结构
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """创建表及索引（幂等）。"""

        def _write(conn):
            cursor = conn.cursor()
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    user_name TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(title_key, subject_id)
                )
                """
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._TABLE}_title_key "
                f"ON {self._TABLE}(title_key)"
            )
            conn.commit()

        self._run_write(_write, error_msg="创建负样本黑名单表失败")

    @staticmethod
    def _title_key(title: str) -> str:
        """标题归一化键：去掉首尾空白并转小写，吸收大小写 / 空白差异。"""
        return (title or "").strip().lower()

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def add(
        self,
        request_title: str,
        subject_id: str,
        user_name: str = "",
        source: str = "",
    ) -> bool:
        """记录一条黑名单（``INSERT OR IGNORE``，幂等）。返回是否新增。"""
        key = self._title_key(request_title)
        if not key or not subject_id:
            return False

        def _write(conn):
            cursor = conn.execute(
                f"""INSERT OR IGNORE INTO {self._TABLE}
                (title_key, title, subject_id, user_name, source)
                VALUES (?, ?, ?, ?, ?)""",
                (key, request_title, str(subject_id), user_name or "", source or ""),
            )
            return cursor.rowcount

        result = self._run_write(_write, error_msg="写入负样本黑名单失败", default=0)
        return bool(result)

    def bulk_add(
        self,
        request_title: str,
        subject_ids: list[str],
        user_name: str = "",
        source: str = "",
    ) -> int:
        """批量记录黑名单，返回新增条数。"""
        key = self._title_key(request_title)
        if not key:
            return 0
        subs = [str(s) for s in (subject_ids or []) if s]
        if not subs:
            return 0

        def _write(conn):
            count = 0
            for sid in subs:
                cursor = conn.execute(
                    f"""INSERT OR IGNORE INTO {self._TABLE}
                    (title_key, title, subject_id, user_name, source)
                    VALUES (?, ?, ?, ?, ?)""",
                    (key, request_title, sid, user_name or "", source or ""),
                )
                count += cursor.rowcount
            return count

        result = self._run_write(
            _write, error_msg="批量写入负样本黑名单失败", default=0
        )
        return int(result or 0)

    def remove(self, request_title: str, subject_id: str) -> bool:
        """移除某标题下的单个黑名单条目。"""
        key = self._title_key(request_title)
        if not key or not subject_id:
            return False

        def _write(conn):
            cursor = conn.execute(
                f"DELETE FROM {self._TABLE} WHERE title_key = ? AND subject_id = ?",
                (key, str(subject_id)),
            )
            return cursor.rowcount > 0

        return bool(
            self._run_write(_write, error_msg="移除负样本黑名单失败", default=False)
        )

    def clear_for_title(self, request_title: str) -> int:
        """清空某标题的全部黑名单（UI 清理入口）。"""
        key = self._title_key(request_title)
        if not key:
            return 0

        def _write(conn):
            cursor = conn.execute(
                f"DELETE FROM {self._TABLE} WHERE title_key = ?", (key,)
            )
            return cursor.rowcount

        result = self._run_write(_write, error_msg="清空负样本黑名单失败", default=0)
        return int(result or 0)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def get_blocked_subject_ids(self, request_title: str) -> set[str]:
        """返回该标题被拉黑的 subject_id 集合（空集合表示无 / 查询失败）。"""
        key = self._title_key(request_title)
        if not key:
            return set()

        def _read(conn):
            cursor = conn.execute(
                f"SELECT subject_id FROM {self._TABLE} WHERE title_key = ?",
                (key,),
            )
            return {row[0] for row in cursor.fetchall()}

        result = self._run_read(_read, error_msg="读取负样本黑名单失败", default=set())
        return result if isinstance(result, set) else set()
