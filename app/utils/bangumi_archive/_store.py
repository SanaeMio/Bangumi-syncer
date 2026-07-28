"""Archive 只读查询接口

职责：
- 按 active 指针连接当前服务的 SQLite 库
- 提供 subject / episode / relations 等只读查询
- 字段映射到 BangumiApi 返回结构（供短路层透明替换）

第二期 A：接入业务读路径作为 Archive 短路的数据源。
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from ...core.logging import logger
from ..bangumi_constants import (
    RELATION_ID_SEQUEL,
    RELATIONS,
)
from ._archive import bangumi_archive
from ._wiki_parser import parse_infobox


class ArchiveStore:
    """Archive 只读查询接口

    通过 bangumi_archive 单例获取当前 active 库路径。
    使用 sqlite3 连接（check_same_thread=False）+ 线程锁保证线程安全。

    返回数据结构对齐 BangumiApi：
    - get_subject 返回字段与 API subjects/{id} 一致
    - get_episodes 返回字段与 API episodes 一致
    - get_related_subjects 返回 list[dict]，含 relation（中文）/ type 字段
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._connected_path: Optional[Path] = None

    def _get_connection(self) -> Optional[sqlite3.Connection]:
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

    # ===== 查询接口 =====

    def get_subject(
        self, subject_id: int, subject_type: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """按 ID 查询条目

        Args:
            subject_id: 条目 ID
            subject_type: 可选过滤（如 SUBJECT_TYPE_ANIME=2），None 不过滤

        Returns:
            dict（对齐 BangumiApi 返回结构）或 None（未命中时）

        字段映射说明：
        - Archive 的 infobox 是原始 wiki 串，与 API 一致
        - tags/score/score_details/meta_tags 在 Archive 中是 JSON 字符串，这里反序列化为 list/dict
        - date 字段对应 API 的 date
        """
        conn = self._get_connection()
        if conn is None:
            return None
        try:
            sql = (
                "SELECT id, type, name, name_cn, infobox, platform, summary, "
                "nsfw, date, favorite, series, tags, score, score_details, "
                "rank, meta_tags FROM subject WHERE id = ?"
            )
            params: tuple = (subject_id,)
            if subject_type is not None:
                sql += " AND type = ?"
                params = (subject_id, subject_type)
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            return self._adapt_subject_row(dict(row))
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_subject 失败: {e}")
            return None

    def get_episodes(
        self,
        subject_id: int,
        episode_type: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """查询条目的章节

        Args:
            subject_id: 条目 ID
            episode_type: 可选过滤（如 EPISODE_TYPE_NORMAL=0），None 返回全部

        Returns:
            list[dict]（对齐 BangumiApi episodes 返回结构）
        """
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            sql = (
                "SELECT id, name, name_cn, description, airdate, disc, "
                "duration, subject_id, sort, type FROM episode "
                "WHERE subject_id = ?"
            )
            params: list[Any] = [subject_id]
            if episode_type is not None:
                sql += " AND type = ?"
                params.append(episode_type)
            sql += " ORDER BY sort"
            rows = conn.execute(sql, params).fetchall()
            return [self._adapt_episode_row(dict(r)) for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_episodes 失败: {e}")
            return []

    def get_related_subjects(self, subject_id: int) -> list[dict[str, Any]]:
        """查询条目的关联条目

        Returns:
            list[dict]，每个 dict 含：
            - id: 关联条目 subject_id（int）
            - relation: 关联类型中文名（str，如「续集」「前传」）
            - type: 关联类型 ID（int，用于精确过滤）
            - order: 排序字段（int）

        对齐 BangumiApi 的 `_find_next_sequel_id` / `_find_related_id_by_relation`
        使用 `item["relation"]` 中文匹配的模式。
        """
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
            return [self._adapt_relation_row(dict(r)) for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_related_subjects 失败: {e}")
            return []

    def find_related_by_relation(self, subject_id: int, relation_id: int) -> list[int]:
        """按关联类型 ID 查询关联条目

        Args:
            subject_id: 起始条目
            relation_id: bangumi_constants.RELATION_ID_*（如 RELATION_ID_SEQUEL=3）

        Returns:
            关联条目 ID 列表（按 order 排序）
        """
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT related_subject_id FROM subject_relation "
                "WHERE subject_id = ? AND relation_type = ? "
                'ORDER BY "order"',
                (subject_id, relation_id),
            ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive find_related_by_relation 失败: {e}")
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
        # 使用 RELATION_ID_SEQUEL=3 常量（修复之前 relation_type=1 的 bug）
        visited: set[int] = {subject_id}
        chain: list[int] = []
        current = subject_id
        for _ in range(max_hops):
            try:
                row = conn.execute(
                    "SELECT related_subject_id FROM subject_relation "
                    "WHERE subject_id = ? AND relation_type = ? "
                    'ORDER BY "order" LIMIT 1',
                    (current, RELATION_ID_SEQUEL),
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

    def find_prequel_chain(self, subject_id: int, max_hops: int = 30) -> list[int]:
        """预构图：沿前传链获取所有前作 subject_id

        与 find_sequel_chain 对称，用于向回追溯前传（季数递减方向）。
        """
        from ..bangumi_constants import RELATION_ID_PREQUEL

        conn = self._get_connection()
        if conn is None:
            return []
        visited: set[int] = {subject_id}
        chain: list[int] = []
        current = subject_id
        for _ in range(max_hops):
            try:
                row = conn.execute(
                    "SELECT related_subject_id FROM subject_relation "
                    "WHERE subject_id = ? AND relation_type = ? "
                    'ORDER BY "order" LIMIT 1',
                    (current, RELATION_ID_PREQUEL),
                ).fetchone()
            except sqlite3.Error as e:
                logger.warning(f"bangumi_archive find_prequel_chain 失败: {e}")
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

    # ===== 字段适配（Archive → BangumiApi 返回结构） =====

    @staticmethod
    def _adapt_subject_row(row: dict[str, Any]) -> dict[str, Any]:
        """将 Archive subject 行适配为 BangumiApi 返回结构

        关键差异：
        - tags/score/score_details/meta_tags 在 Archive 中是 JSON 字符串，反序列化为 list/dict
        - infobox 在 Archive 中是原始 wiki 串（如 {{Infobox|key=value}}），
          这里通过 wiki_parser 解析为 API 兼容的 list[dict] 格式；
          解析失败时回退为空列表（与 API 返回空 infobox 行为一致）
        """
        for json_field in ("tags", "score", "score_details", "meta_tags"):
            val = row.get(json_field)
            if isinstance(val, str) and val:
                try:
                    row[json_field] = json.loads(val)
                except (ValueError, TypeError):
                    # 保留原始字符串，不破坏数据
                    pass

        # infobox: 原始 wiki 串 → API 兼容的 list[dict]
        infobox_raw = row.get("infobox")
        if isinstance(infobox_raw, str):
            if infobox_raw:
                parsed = parse_infobox(infobox_raw)
                # 解析失败（非 Infobox 模板/格式异常）时回退为空列表，
                # 与 BangumiApi 在 infobox 字段为空时返回 [] 的行为对齐
                row["infobox"] = parsed if parsed else []
            else:
                # 空字符串视为空 infobox
                row["infobox"] = []
        elif infobox_raw is None:
            # None 视为空 infobox
            row["infobox"] = []
        # 已是 list/dict 的异常情况保持原样

        return row

    @staticmethod
    def _adapt_episode_row(row: dict[str, Any]) -> dict[str, Any]:
        """将 Archive episode 行适配为 BangumiApi 返回结构

        Archive 的字段名与 API 一致，仅 airdate 对应 API 的 airdate。
        """
        return row

    @staticmethod
    def _adapt_relation_row(row: dict[str, Any]) -> dict[str, Any]:
        """将 Archive relation 行适配为 BangumiApi 返回结构

        Archive 字段：subject_id, relation_type(int), related_subject_id, order
        API 字段：id, relation(中文), type(int)

        映射规则：
        - id ← related_subject_id（关联的目标条目 ID）
        - relation ← RELATIONS[relation_type] 中文名
        - type ← relation_type（保留 int 供精确过滤）
        - order ← order
        """
        relation_type = row.get("relation_type")
        return {
            "id": row.get("related_subject_id"),
            "relation": RELATIONS.get(relation_type, "其他")
            if relation_type is not None
            else "其他",
            "type": relation_type,
            "order": row.get("order", 0),
        }


# 全局单例
archive_store = ArchiveStore()
