"""Archive 标题索引（内存 dict + rapidfuzz 模糊匹配）

职责：
- 从 Archive subject 表构建标题 → subject_id 集合的内存索引
- 提供精确匹配 O(1) 和模糊匹配（rapidfuzz）
- 在 active 库切换时自动失效并延迟重建

数据来源：
- subject.name（日文原名）
- subject.name_cn（中文名）
- subject.infobox 中解析出的别名（key 在 _ALIAS_KEYS 中）

设计参考：bangumi_data 的 _build_title_index 与 matching.py。
与 bangumi_data 差异：Archive 覆盖全表（含无中文翻译条目），
故来源更全；且直接持有 subject_id，免去中转映射。
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from rapidfuzz import fuzz, process

from ...core.logging import logger
from ._archive import bangumi_archive
from ._wiki_parser import parse_infobox

# infobox 中视为别名的 key（已小写化，与解析输出对齐）
# 参考 search.py title_diff_ratio 中按 key=="别名" 提取别名的逻辑。
# 同时纳入 alias/aliases/中文名/英文名/罗马音 等常见别名键，
# 提升匹配率；非别名键（如「话数」「原作」）不进入索引。
_ALIAS_KEYS = frozenset(
    {"别名", "alias", "aliases", "中文名", "英文名", "罗马音", "假名"}
)


def _normalize_key(text: str) -> str:
    """归一化标题为索引 key：去首尾空白并转小写。

    Archive 标题来源混杂（日文/中文/英文/罗马音），
    小写归一化可让大小写差异的查询都命中同一 key。
    """
    if not isinstance(text, str):
        return ""
    return text.strip().lower()


class ArchiveTitleIndex:
    """Archive 标题内存索引

    线程安全说明：
    - 构建在锁内完成，避免并发重复构建
    - 查询无锁读取 dict（构建完成后只读）
    - active 库切换时通过路径变化检测自动重建
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._title_to_ids: dict[str, set[int]] = {}
        # 标记已构建的库路径（路径变化即视为失效）
        self._built_path: Optional[Path] = None

    def _ensure_built(self) -> bool:
        """延迟构建索引。已构建且未失效则跳过。

        Returns:
            True 表示索引可用，False 表示不可用（库不存在或构建失败）
        """
        active_path = bangumi_archive.get_active_db_path()
        if not active_path.exists():
            return False

        # 快速路径：路径未变化且已有索引
        if self._built_path == active_path and self._title_to_ids:
            return True

        # 慢路径：加锁构建（双检锁）
        with self._lock:
            if self._built_path == active_path and self._title_to_ids:
                return True
            self._build_internal(active_path)
            return bool(self._title_to_ids)

    def _build_internal(self, db_path: Path) -> None:
        """实际构建索引（在锁内调用）"""
        self._title_to_ids.clear()
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.execute("PRAGMA query_only=ON")
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                "SELECT id, name, name_cn, infobox FROM subject"
            ).fetchall()

            subject_count = 0
            for row in rows:
                row_dict = dict(row)
                subject_id = row_dict.get("id")
                if subject_id is None:
                    continue
                titles = self._extract_titles(row_dict)
                for title in titles:
                    key = _normalize_key(title)
                    if not key:
                        continue
                    id_set = self._title_to_ids.get(key)
                    if id_set is None:
                        id_set = set()
                        self._title_to_ids[key] = id_set
                    id_set.add(subject_id)
                subject_count += 1

            self._built_path = db_path
            logger.info(
                f"bangumi_archive 标题索引构建完成: "
                f"{len(self._title_to_ids)} 个唯一标题, {subject_count} 个条目"
            )
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive 标题索引构建失败: {e}")
            self._title_to_ids.clear()
            self._built_path = None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass

    @staticmethod
    def _extract_titles(row: dict[str, Any]) -> list[str]:
        """从 subject 行提取所有可用于匹配的标题

        - name（日文原名）
        - name_cn（中文名）
        - infobox 中的别名（key 在 _ALIAS_KEYS 中，已小写匹配）
        """
        titles: list[str] = []
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            titles.append(name)
        name_cn = row.get("name_cn")
        if isinstance(name_cn, str) and name_cn.strip():
            titles.append(name_cn)

        infobox_raw = row.get("infobox")
        if isinstance(infobox_raw, str) and infobox_raw:
            parsed = parse_infobox(infobox_raw)
            for item in parsed:
                key_lower = str(item.get("key", "")).strip().lower()
                if key_lower not in _ALIAS_KEYS:
                    continue
                value = item.get("value")
                if isinstance(value, list):
                    for v in value:
                        text = v.get("v") if isinstance(v, dict) else v
                        if isinstance(text, str) and text.strip():
                            titles.append(text)
                elif isinstance(value, str) and value.strip():
                    titles.append(value)
        return titles

    def invalidate(self) -> None:
        """显式标记索引失效（下次访问时重建）

        通常由 active 库切换自动触发，无需手动调用；
        仅在外部确知数据变化时调用。
        """
        with self._lock:
            self._built_path = None
            self._title_to_ids.clear()

    def find_subject_ids_by_title(self, title: str) -> list[int]:
        """精确匹配标题 → subject_id 列表

        Args:
            title: 查询标题（任意大小写/首尾空白）

        Returns:
            命中的 subject_id 列表（可能为多个，调用方需进一步筛选）
            空列表表示未命中或索引不可用
        """
        if not self._ensure_built():
            return []
        key = _normalize_key(title)
        if not key:
            return []
        ids = self._title_to_ids.get(key)
        if not ids:
            return []
        return sorted(ids)

    def find_subject_ids_fuzzy(
        self,
        title: str,
        threshold: int = 80,
        limit: int = 5,
    ) -> list[tuple[int, float]]:
        """模糊匹配标题 → [(subject_id, score), ...]

        使用 rapidfuzz process.extract 遍历所有标题，按 score 降序。
        多个标题命中同一 subject_id 时取最高分。

        Args:
            title: 查询标题
            threshold: 相似度阈值（0-100），默认 80
            limit: 最多返回数量，默认 5

        Returns:
            [(subject_id, score), ...]，score 降序，最多 limit 条
        """
        if not self._ensure_built():
            return []
        query = _normalize_key(title)
        if not query or not self._title_to_ids:
            return []

        try:
            # 注意：process.extract 对 list 输入返回 (str, score, index)，
            # 对 dict 输入返回 (str, score, key)。这里传 list 后用 index 反查 key，
            # 避免 dict_keys 被 rapidfuzz 视为 list 时 index≠key 的陷阱。
            all_titles = list(self._title_to_ids.keys())
            results = process.extract(
                query,
                all_titles,
                scorer=fuzz.ratio,
                score_cutoff=threshold,
                limit=limit * 4,  # 多取以备同 subject_id 合并后仍足够
            )
        except Exception as e:
            logger.warning(f"bangumi_archive 模糊匹配异常: {e}")
            return []

        # 合并同 subject_id，取最高分
        id_to_score: dict[int, float] = {}
        for _matched, score, idx in results:
            # idx 是 all_titles 列表中的下标，反查得到真实标题 key
            title_key = all_titles[idx]
            ids = self._title_to_ids.get(title_key)
            if not ids:
                continue
            for sid in ids:
                cur = id_to_score.get(sid)
                if cur is None or score > cur:
                    id_to_score[sid] = score

        return sorted(id_to_score.items(), key=lambda x: -x[1])[:limit]


# 全局单例
archive_title_index = ArchiveTitleIndex()
