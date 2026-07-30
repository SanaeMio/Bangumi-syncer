"""Archive 标题索引（内存 dict + bigram 倒排 + rapidfuzz 模糊匹配）

职责：
- 从 Archive subject 表构建标题 → subject_id 集合的内存索引
- 提供精确匹配 O(1) 和模糊匹配
- bigram 倒排索引预筛选候选集，避免全量遍历
- 多 scorer 融合（ratio/partial_ratio/token_set_ratio + 子串短路）
- 在 active 库切换时自动失效并延迟重建
- 磁盘缓存：构建完成后序列化为 JSON，启动时直接加载（~5-10s）
- 后台预构建：导入完成后立即触发，避免首次查询阻塞

数据来源：
- subject.name（日文原名）
- subject.name_cn（中文名）
- subject.infobox 中解析出的别名（key 在 _ALIAS_KEYS 中）

性能基线（658248 个 subject, 842829 唯一标题）：
- 首次从 DB 构建：~111s（主要耗时在 parse_infobox）
- 从磁盘 JSON 加载：~5-10s
- 精确查询：O(1) <1ms
- 模糊查询（bigram 预筛 + 多 scorer）：~5-15ms（原 50-300ms）
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import heapq
import json
import os
import sqlite3
import threading
import time as _time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rapidfuzz import fuzz, process

from ...core.logging import logger
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
# v1: 仅 title_to_ids
# v2: 新增 bigram 倒排索引（find_subject_ids_fuzzy 性能优化）
# v3: _normalize_key 改为 NFKC + 去除标点（让 'C.A.N.S' 与 'CANS' 等价）
_CACHE_FORMAT_VERSION = 3

# bigram 倒排索引：单 bigram 对应的 title_key 集合大小上限。
# 当某 bigram 命中过多（如「的」「a」等高频 bigram）时跳过该 bigram，
# 避免候选集爆炸（10万+ 标题无需进入精排）。
_BIGRAM_MAX_POSTINGS = 5000

# bigram 候选集精排上限：按 bigram 命中数排序后取 top-N 精排
_CANDIDATE_LIMIT = 200

# 多 scorer 融合权重
_PARTIAL_RATIO_WEIGHT = 0.9
_TOKEN_SET_RATIO_WEIGHT = 0.95


def _normalize_key(text: str) -> str:
    """归一化标题为索引 key：NFKC + 去除标点 + 转小写。

    Archive 标题来源混杂（日文/中文/英文/罗马音），驱动推送的
    标题可能存在全半角差异、标点差异（如 'C.A.N.S' vs 'CANS'，
    'Battle Spirits [Re]' vs 'Battle Spirits Re'）。

    归一化策略：
    1. NFKC 标准化：全角→半角（'：'→':', '～'→'~'）
    2. 去除非字母数字字符：让标点差异的查询都命中同一 key
       （'C.A.N.S' 和 'CANS' 都归一化为 'cans'）
    3. 转小写：大小写差异的查询都命中同一 key

    副作用：原本不同的标题可能归一化后相同（如 'A-B' 和 'AB'），
    使 subject_id 列表变长，但调用方通过 type/air_date 过滤即可。
    """
    if not isinstance(text, str):
        return ""
    # NFKC 标准化（全角→半角、兼容性分解）
    text = unicodedata.normalize("NFKC", text)
    # 保留字母数字（含中日韩字符），去除标点和分隔符
    text = "".join(c for c in text if c.isalnum())
    return text.lower()


def _extract_bigrams(text: str) -> set[str]:
    """从归一化标题提取字符 bigram 集合

    示例：「完美世界」→ {'完美', '美世', '世界'}
    短标题（len<2）返回空集，仍可用精确匹配

    Args:
        text: 已 _normalize_key 处理的标题

    Returns:
        bigram 集合（set 去重，构建期和查询期共用）
    """
    if not text or len(text) < 2:
        return set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


class ArchiveTitleIndex:
    """Archive 标题内存索引

    线程安全说明：
    - 构建在锁内完成，避免并发重复构建
    - 查询无锁读取 dict（构建完成后只读）
    - active 库切换时通过路径变化检测自动重建
    - 后台构建期间 is_ready=False，try_search 降级到 API

    索引结构：
    - _title_to_ids: dict[title_key → list[subject_id]] 精确匹配
    - _bigram_index: dict[bigram → list[title_key]] 模糊匹配预筛
        查询时对 query bigrams 求交集计数 → top-N 候选 → 多 scorer 精排
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._title_to_ids: dict[str, list[int]] = {}
        # bigram 倒排索引：bigram → list[title_key]
        # 仅在构建时填充，查询时只读
        self._bigram_index: dict[str, list[str]] = {}
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
                # v2+ 缓存含 bigram 倒排索引（第三行）
                # 容错：旧 v1 缓存可能没有，回退为空 dict 触发首次查询时按需重建
                line = f.readline()
                if line:
                    try:
                        self._bigram_index = json.loads(line)
                    except json.JSONDecodeError:
                        self._bigram_index = {}
                else:
                    self._bigram_index = {}
            self._built_path = db_path
            logger.info(
                f"bangumi_archive 标题索引从磁盘加载完成: "
                f"{len(self._title_to_ids)} 个唯一标题, "
                f"{len(self._bigram_index)} 个 bigram"
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
        - 第三行：bigram_index JSON（{bigram: [title_key, ...]}）

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
                f.write("\n")
                f.write(json.dumps(self._bigram_index, ensure_ascii=False))
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
        # 延迟导入避免与 _archive.py 循环 import
        from ._archive import bangumi_archive

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
        self._bigram_index.clear()
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.execute("PRAGMA query_only=ON")
            conn.row_factory = sqlite3.Row

            # 批量读取，避免一次性 fetchall 占用大量内存
            cursor = conn.execute("SELECT id, name, name_cn, infobox FROM subject")

            subject_count = 0
            # 临时用 set 存储 subject_ids（O(1) 去重），构建后转 list
            # 用 list 时 `if subject_id not in id_list` 是 O(n)，热门标题会膨胀导致 quadratic
            title_to_id_sets: dict[str, set[int]] = {}
            # 临时累积 bigram → title_key 映射（用 set 去重，构建后转 list）
            bigram_sets: dict[str, set[str]] = {}
            _t_build_start = _time.perf_counter()
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
                        # 用 set 存储 subject_id（O(1) add，无需去重检查）
                        id_set = title_to_id_sets.get(key)
                        if id_set is None:
                            id_set = set()
                            title_to_id_sets[key] = id_set
                        id_set.add(subject_id)
                        # 构建 bigram 倒排索引（每个 title_key 只入一次）
                        # 性能优化：高频 bigram（posting 超过阈值）立即停止添加，
                        # 避免高频 bigram（如「の」「a」）的 set 膨胀到几十万元素拖慢构建
                        for bigram in _extract_bigrams(key):
                            posting = bigram_sets.get(bigram)
                            if posting is None:
                                posting = set()
                                bigram_sets[bigram] = posting
                            elif len(posting) >= _BIGRAM_MAX_POSTINGS:
                                # 标记为已满，后续不再添加
                                continue
                            posting.add(key)
                    subject_count += 1
                # 进度日志（每 50000 条输出一次）
                if subject_count % 50000 == 0:
                    elapsed = _time.perf_counter() - _t_build_start
                    logger.info(
                        f"bangumi_archive 索引构建中: {subject_count} 条, "
                        f"{len(title_to_id_sets)} 标题, "
                        f"{len(bigram_sets)} bigram, "
                        f"耗时 {elapsed:.1f}s"
                    )

            logger.info(
                f"bangumi_archive 索引构建读取完成: {subject_count} 条, "
                f"耗时 {_time.perf_counter() - _t_build_start:.1f}s, "
                f"开始转换索引..."
            )

            # 将 set 转为 list 节省内存（查询时按 list 顺序遍历，JSON 友好）
            for title_key, id_set in title_to_id_sets.items():
                self._title_to_ids[title_key] = list(id_set)
            title_to_id_sets.clear()

            # 转换 bigram 索引，过滤过高频 bigram
            skipped_bigrams = 0
            for bigram, posting_set in bigram_sets.items():
                if len(posting_set) > _BIGRAM_MAX_POSTINGS:
                    skipped_bigrams += 1
                    continue
                self._bigram_index[bigram] = list(posting_set)
            # 释放临时结构
            bigram_sets.clear()

            self._built_path = db_path
            logger.info(
                f"bangumi_archive 标题索引构建完成: "
                f"{len(self._title_to_ids)} 个唯一标题, "
                f"{len(self._bigram_index)} 个 bigram "
                f"(跳过 {skipped_bigrams} 个高频 bigram), "
                f"{subject_count} 个条目"
            )
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive 标题索引构建失败: {e}")
            self._title_to_ids.clear()
            self._bigram_index.clear()
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
            self._bigram_index.clear()

    def build_in_background(self) -> None:
        """后台触发索引构建（不阻塞调用方）

        场景：导入完成后立即调用，让首次查询时索引已就绪。
        构建期间 is_ready=False，try_search 返回 archive_miss 降级到 API。
        构建完成后写入磁盘缓存。
        """
        # 延迟导入避免与 _archive.py 循环 import
        from ._archive import bangumi_archive

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
            try:
                from ...services.notification_service import notification_service

                notification_service.notify(
                    "archive_build_failed",
                    source="bangumi-archive",
                    error_message=f"标题索引后台构建异常: {e}",
                    stage="title_index_build",
                )
            except Exception:
                pass

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

    def _collect_candidates_by_bigram(self, query: str) -> list[str]:
        """通过 bigram 倒排索引预筛候选 title_key 集合

        对 query 的每个 bigram 查询倒排索引，统计每个 title_key 命中的
        bigram 数量（命中越多越相似），按命中数降序取 top-N。

        鲁棒性说明：query 中可能含未在索引中的 bigram（如拼写错误），
        此时仍能从其他命中的 bigram 找到候选。仅当所有 bigram 都
        未命中时才返回空列表。

        Args:
            query: 已归一化的查询标题

        Returns:
            候选 title_key 列表（按 bigram 命中数降序，最多 _CANDIDATE_LIMIT 个）
        """
        if not self._bigram_index or not query:
            return []
        query_bigrams = _extract_bigrams(query)
        if not query_bigrams:
            return []

        # 遍历所有 bigram，统计每个 title_key 命中的 bigram 数
        # 注意：未在索引中的 bigram 自然跳过，不影响其他 bigram 命中
        hit_counts: dict[str, int] = {}
        for bigram in query_bigrams:
            posting = self._bigram_index.get(bigram)
            if not posting:
                continue
            for tk in posting:
                hit_counts[tk] = hit_counts.get(tk, 0) + 1

        if not hit_counts:
            return []

        # 按命中数降序取 top-N
        if len(hit_counts) <= _CANDIDATE_LIMIT:
            return sorted(hit_counts.keys(), key=lambda k: -hit_counts[k])
        # 用 nlargest 取 top-N（避免全排序）
        return [
            tk
            for _, tk in heapq.nlargest(
                _CANDIDATE_LIMIT,
                ((count, tk) for tk, count in hit_counts.items()),
            )
        ]

    @staticmethod
    def _score_candidate(query: str, candidate: str) -> float:
        """多 scorer 融合计算候选标题相似度

        融合策略（取最高分）：
        - fuzz.ratio：基础整体相似度
        - fuzz.partial_ratio * 0.9：子串包含关系（短串在长串中匹配）
        - fuzz.token_set_ratio * 0.95：词序差异容错
        - 子串包含直接 100 分（query 是 candidate 子串或反之）

        Args:
            query: 归一化查询
            candidate: 归一化候选标题

        Returns:
            0-100 相似度分数
        """
        if not query or not candidate:
            return 0.0
        # 子串包含短路：直接 100 分
        if query == candidate:
            return 100.0
        if query in candidate or candidate in query:
            return 100.0

        score = float(fuzz.ratio(query, candidate))
        # partial_ratio 对子串包含更敏感
        partial = fuzz.partial_ratio(query, candidate) * _PARTIAL_RATIO_WEIGHT
        if partial > score:
            score = partial
        # token_set_ratio 对词序差异容错
        token_set = fuzz.token_set_ratio(query, candidate) * _TOKEN_SET_RATIO_WEIGHT
        if token_set > score:
            score = token_set
        return score

    def find_subject_ids_fuzzy(
        self,
        title: str,
        threshold: int = 80,
        limit: int = 5,
    ) -> list[tuple[int, float]]:
        """模糊匹配标题 → [(subject_id, score), ...]

        两阶段查询：
        1. bigram 倒排索引预筛候选 title_key 集合（避免全量遍历）
        2. 对候选集用多 scorer 融合精排

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

        # 第一阶段：bigram 倒排索引预筛候选
        candidates = self._collect_candidates_by_bigram(query)

        # 兜底：bigram 索引未命中或为空时，回退到全量 process.extract
        # （仅在缓存加载缺失 bigram 索引的边界情况触发）
        if not candidates:
            if not self._bigram_index:
                # bigram 索引未加载（旧缓存），回退全量 fuzzy
                try:
                    all_titles = list(self._title_to_ids.keys())
                    results = process.extract(
                        query,
                        all_titles,
                        scorer=fuzz.ratio,
                        score_cutoff=threshold,
                        limit=limit * 4,
                    )
                except Exception as e:
                    logger.warning(f"bangumi_archive 模糊匹配异常（回退全量）: {e}")
                    return []
                id_to_score: dict[int, float] = {}
                for _matched, score, idx in results:
                    title_key = all_titles[idx]
                    ids = self._title_to_ids.get(title_key)
                    if not ids:
                        continue
                    for sid in ids:
                        cur = id_to_score.get(sid)
                        if cur is None or score > cur:
                            id_to_score[sid] = score
                return sorted(id_to_score.items(), key=lambda x: -x[1])[:limit]
            return []

        # 第二阶段：多 scorer 精排候选集
        try:
            id_to_score: dict[int, float] = {}
            for candidate_key in candidates:
                score = self._score_candidate(query, candidate_key)
                if score < threshold:
                    continue
                ids = self._title_to_ids.get(candidate_key)
                if not ids:
                    continue
                for sid in ids:
                    cur = id_to_score.get(sid)
                    if cur is None or score > cur:
                        id_to_score[sid] = score
        except Exception as e:
            logger.warning(f"bangumi_archive 模糊匹配异常（bigram 精排）: {e}")
            return []

        return sorted(id_to_score.items(), key=lambda x: -x[1])[:limit]


# 全局单例
archive_title_index = ArchiveTitleIndex()
