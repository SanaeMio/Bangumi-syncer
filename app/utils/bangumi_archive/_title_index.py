"""Archive 标题索引（内存 dict + rapidfuzz 模糊匹配）

职责：
- 从 Archive subject 表构建标题 → subject_id 集合的内存索引
- 提供精确匹配 O(1) 和模糊匹配（rapidfuzz）
- 在 active 库切换时自动失效并延迟重建
- 磁盘缓存：构建完成后序列化为 JSON，启动时直接加载（~5-10s）
- 后台预构建：导入完成后立即触发，避免首次查询阻塞

数据来源：
- subject.name（日文原名）
- subject.name_cn（中文名）
- subject.infobox 中解析出的别名（key 在 _ALIAS_KEYS 中）

性能基线（658248 个 subject）：
- 首次从 DB 构建：~111s（主要耗时在 parse_infobox）
- 从磁盘 JSON 加载：~5-10s
- 精确查询：O(1) <1ms
- 模糊查询：rapidfuzz ~10-50ms
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
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

# 磁盘缓存文件名（与 db 同目录，按 active 库名 a/b 区分）
_CACHE_FILENAME_TEMPLATE = "bangumi_archive_{suffix}.index"

# 缓存格式版本：字段结构变更时递增，旧缓存自动失效
_CACHE_FORMAT_VERSION = 1


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
    - 后台构建期间 is_ready=False，try_search 降级到 API
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._title_to_ids: dict[str, list[int]] = {}
        # 标记已构建的库路径（路径变化即视为失效）
        self._built_path: Optional[Path] = None
        # 后台构建线程（None 表示无构建任务或已完成）
        self._build_thread: Optional[threading.Thread] = None

    @property
    def is_ready(self) -> bool:
        """索引是否已构建完成可用

        try_search 在未就绪时返回 archive_miss 降级到 API，
        避免阻塞调用方。
        """
        return bool(self._title_to_ids) and self._built_path is not None

    def _get_cache_path(self, db_path: Path) -> Path:
        """获取 db 对应的磁盘缓存路径

        按 active 库名 a/b 区分，避免双库切换后读到旧缓存。
        """
        suffix = "a" if db_path.name.endswith("_a.db") else "b"
        return db_path.parent / _CACHE_FILENAME_TEMPLATE.format(suffix=suffix)

    def _is_cache_valid(self, cache_path: Path, db_path: Path) -> bool:
        """校验磁盘缓存是否有效

        校验项：
        1. 缓存文件存在
        2. 缓存 format_version 匹配
        3. 缓存的 db_mtime 与当前 db 的 mtime 一致（db 变更则失效）
        4. 缓存的 db_size 与当前 db 的 size 一致（双保险）
        """
        if not cache_path.exists():
            return False
        try:
            db_stat = db_path.stat()
            cache_stat = cache_path.stat()
            # 缓存文件不能比 db 更旧
            if cache_stat.st_mtime < db_stat.st_mtime:
                return False
            # 读缓存头部校验
            with open(cache_path, "rb") as f:
                header = json.loads(f.readline())
            if header.get("format_version") != _CACHE_FORMAT_VERSION:
                return False
            if header.get("db_mtime") != db_stat.st_mtime:
                return False
            if header.get("db_size") != db_stat.st_size:
                return False
            return True
        except (OSError, ValueError, KeyError):
            return False

    def _load_from_disk(self, cache_path: Path, db_path: Path) -> bool:
        """从磁盘 JSON 加载索引

        Returns:
            True 表示加载成功，False 表示加载失败或缓存无效
        """
        try:
            db_stat = db_path.stat()
            with open(cache_path, "rb") as f:
                header = json.loads(f.readline())
                if header.get("format_version") != _CACHE_FORMAT_VERSION:
                    logger.warning(
                        f"bangumi_archive 标题索引缓存版本不匹配: "
                        f"cache={header.get('format_version')}, "
                        f"expected={_CACHE_FORMAT_VERSION}，将重建"
                    )
                    return False
                if header.get("db_mtime") != db_stat.st_mtime:
                    logger.info("bangumi_archive 标题索引缓存 db mtime 不符，将重建")
                    return False
                # 加载主体：{title: [ids]}
                self._title_to_ids = json.loads(f.readline())
            self._built_path = db_path
            logger.info(
                f"bangumi_archive 标题索引从磁盘加载完成: "
                f"{len(self._title_to_ids)} 个唯一标题"
            )
            return True
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warning(f"bangumi_archive 标题索引磁盘缓存加载失败: {e}")
            return False

    def _save_to_disk(self, cache_path: Path, db_path: Path) -> None:
        """将索引序列化到磁盘 JSON

        格式：
        - 第一行：header JSON（format_version, db_mtime, db_size, built_at）
        - 第二行：title_to_ids JSON（{title: [subject_id, ...]}）

        注意：set 序列化为 list，加载后保持 list 不转回 set
        （list 占用内存更小，查询性能与 set 接近）。
        """
        try:
            db_stat = db_path.stat()
            header = {
                "format_version": _CACHE_FORMAT_VERSION,
                "db_mtime": db_stat.st_mtime,
                "db_size": db_stat.st_size,
                "built_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = cache_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(header, ensure_ascii=False))
                f.write("\n")
                f.write(json.dumps(self._title_to_ids, ensure_ascii=False))
            os.replace(str(tmp), str(cache_path))
            logger.debug(f"bangumi_archive 标题索引已写入磁盘缓存: {cache_path}")
        except OSError as e:
            logger.warning(f"bangumi_archive 标题索引写入磁盘缓存失败: {e}")

    def _ensure_built(self) -> bool:
        """延迟构建索引。已构建且未失效则跳过。

        优先级：
        1. 内存已构建且未失效 → 直接用
        2. 磁盘缓存有效 → 加载
        3. 从 DB 构建 + 写入磁盘缓存

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

            # 优先尝试磁盘缓存
            cache_path = self._get_cache_path(active_path)
            if self._is_cache_valid(cache_path, active_path):
                if self._load_from_disk(cache_path, active_path):
                    return bool(self._title_to_ids)

            # 缓存无效，从 DB 构建
            self._build_internal(active_path)
            if self._title_to_ids:
                # 构建成功，写入磁盘缓存供下次使用
                self._save_to_disk(cache_path, active_path)
            return bool(self._title_to_ids)

    def _build_internal(self, db_path: Path) -> None:
        """实际构建索引（在锁内调用）"""
        self._title_to_ids.clear()
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.execute("PRAGMA query_only=ON")
            conn.row_factory = sqlite3.Row

            # 批量读取，避免一次性 fetchall 占用大量内存
            cursor = conn.execute("SELECT id, name, name_cn, infobox FROM subject")

            subject_count = 0
            skipped_infobox = 0
            while True:
                rows = cursor.fetchmany(2000)
                if not rows:
                    break
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
                        # 用 list 而非 set，节省内存且 JSON 友好
                        id_list = self._title_to_ids.get(key)
                        if id_list is None:
                            id_list = []
                            self._title_to_ids[key] = id_list
                        if subject_id not in id_list:
                            id_list.append(subject_id)
                    subject_count += 1

            self._built_path = db_path
            logger.info(
                f"bangumi_archive 标题索引构建完成: "
                f"{len(self._title_to_ids)} 个唯一标题, "
                f"{subject_count} 个条目 (跳过 {skipped_infobox} 个空 infobox)"
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

        性能优化：空 infobox 字符串直接跳过，避免 parse_infobox 调用开销。
        """
        titles: list[str] = []
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            titles.append(name)
        name_cn = row.get("name_cn")
        if isinstance(name_cn, str) and name_cn.strip():
            titles.append(name_cn)

        infobox_raw = row.get("infobox")
        # 优化：空字符串/None 直接跳过，省去 parse_infobox 开销
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

    def build_in_background(self) -> None:
        """后台触发索引构建（不阻塞调用方）

        场景：导入完成后立即调用，让首次查询时索引已就绪。
        构建期间 is_ready=False，try_search 返回 archive_miss 降级到 API。
        构建完成后写入磁盘缓存。
        """
        active_path = bangumi_archive.get_active_db_path()
        if not active_path.exists():
            return

        with self._lock:
            # 已构建或正在构建则跳过
            if self._built_path == active_path and self._title_to_ids:
                return
            if self._build_thread is not None and self._build_thread.is_alive():
                return

            # 优先尝试磁盘缓存（同步加载，~5-10s 可接受）
            cache_path = self._get_cache_path(active_path)
            if self._is_cache_valid(cache_path, active_path):
                if self._load_from_disk(cache_path, active_path):
                    return  # 磁盘缓存加载成功，无需后台构建

            # 启动后台构建线程
            self._build_thread = threading.Thread(
                target=self._background_build,
                args=(active_path, cache_path),
                name="archive-title-index-build",
                daemon=True,
            )
            self._build_thread.start()
            logger.info("bangumi_archive 标题索引后台构建已启动")

    def _background_build(self, db_path: Path, cache_path: Path) -> None:
        """后台构建索引（在工作线程内执行）"""
        try:
            with self._lock:
                # 双检锁：可能其他线程已构建
                if self._built_path == db_path and self._title_to_ids:
                    return
                self._build_internal(db_path)
                if self._title_to_ids:
                    self._save_to_disk(cache_path, db_path)
        except Exception as e:
            logger.warning(f"bangumi_archive 标题索引后台构建异常: {e}")

    def find_subject_ids_by_title(self, title: str) -> list[int]:
        """精确匹配标题 → subject_id 列表

        Args:
            title: 查询标题（任意大小写/首尾空白）

        Returns:
            命中的 subject_id 列表（可能为多个，调用方需进一步筛选）
            空列表表示未命中或索引未就绪

        注意：本方法不触发同步构建，避免阻塞调用方。
        索引未就绪时返回空列表，调用方（如 try_search）应自行
        检查 is_ready 并降级到 API。后台构建由 build_in_background
        或显式 _ensure_built 触发。
        """
        if not self.is_ready:
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
            索引未就绪时返回空列表（不触发同步构建）
        """
        if not self.is_ready:
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
