"""Archive 只读查询接口（第二期启用）

职责：
- 按 active 指针连接当前服务的 SQLite 库
- 提供 subject / episode / relations 等只读查询
- 命中率统计（供 _stats 模块使用）

第一期不接入业务查询链路；本模块仅作为接口预实现，待第二期接入。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from ...core.logging import logger
from ._archive import bangumi_archive


class ArchiveStore:
    """Archive 只读查询接口

    通过 bangumi_archive 单例获取当前 active 库路径。
    使用 sqlite3 连接（check_same_thread=False）+ 线程锁保证线程安全。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._connected_path: Path | None = None

    def _get_connection(self) -> sqlite3.Connection | None:
        """获取当前 active 库的连接

        active 指针变化时自动重连。
        库不存在或损坏时返回 None（调用方应降级到 API）。
        """
        with self._lock:
            active_path = bangumi_archive.get_active_db_path()
            if not active_path.exists():
                return None

            # 检测 active 切换：路径变化时重连
            if self._conn is not None and self._connected_path != active_path:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
                self._connected_path = None

            if self._conn is None:
                try:
                    conn = sqlite3.connect(str(active_path), check_same_thread=False)
                    conn.execute("PRAGMA query_only=ON")  # 只读模式
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.row_factory = sqlite3.Row
                    self._conn = conn
                    self._connected_path = active_path
                except sqlite3.Error as e:
                    logger.warning(
                        f"bangumi_archive: 连接 active 库失败 {active_path}: {e}"
                    )
                    return None

            # 简单健康检查（连接断开时重连）
            try:
                self._conn.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
                self._connected_path = None
                return None

            return self._conn

    def close(self) -> None:
        """关闭连接"""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
                self._connected_path = None

    # ===== 查询接口（第二期接入业务） =====

    def get_subject(self, subject_id: int) -> dict[str, Any] | None:
        """按 ID 查询条目

        Returns:
            dict 或 None（未命中时）
        """
        conn = self._get_connection()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT id, type, name, name_cn, infobox, platform, summary, "
                "nsfw, date, favorite, series, tags, score, score_details, "
                "rank, meta_tags FROM subject WHERE id = ?",
                (subject_id,),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_subject 失败: {e}")
            return None

    def get_episodes(self, subject_id: int) -> list[dict[str, Any]]:
        """查询条目的所有章节"""
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT id, name, name_cn, description, airdate, disc, "
                "duration, subject_id, sort, type FROM episode "
                "WHERE subject_id = ? ORDER BY sort",
                (subject_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_episodes 失败: {e}")
            return []

    def get_related_subjects(self, subject_id: int) -> list[dict[str, Any]]:
        """查询条目的关联条目"""
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                'SELECT subject_id, relation_type, related_subject_id, "order" '
                "FROM subject_relation WHERE subject_id = ? "
                'ORDER BY "order"',
                (subject_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_related_subjects 失败: {e}")
            return []

    def find_sequel_chain(self, subject_id: int, max_hops: int = 30) -> list[int]:
        """预构图：沿续集链获取所有续作 subject_id

        Args:
            subject_id: 起始条目
            max_hops: 最大跳数（防环）

        Returns:
            续集链 subject_id 列表（不含起始条目）
        """
        conn = self._get_connection()
        if conn is None:
            return []
        # relation_type=1 对应 SEQUEL（见 bangumi/common YAML）
        visited: set[int] = {subject_id}
        chain: list[int] = []
        current = subject_id
        for _ in range(max_hops):
            try:
                row = conn.execute(
                    "SELECT related_subject_id FROM subject_relation "
                    "WHERE subject_id = ? AND relation_type = 1 "
                    'ORDER BY "order" LIMIT 1',
                    (current,),
                ).fetchone()
            except sqlite3.Error as e:
                logger.warning(f"bangumi_archive find_sequel_chain 失败: {e}")
                break
            if row is None:
                break
            next_id = row[0]
            if next_id in visited:
                break
            visited.add(next_id)
            chain.append(next_id)
            current = next_id
        return chain

    def count_rows(self, table_name: str) -> int:
        """查询表行数（供状态展示）"""
        conn = self._get_connection()
        if conn is None:
            return 0
        try:
            row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
            return row[0] if row else 0
        except sqlite3.Error:
            return 0


# 全局单例
archive_store = ArchiveStore()
