"""基于 SQLite FTS5 trigram 的 Archive 标题查询层

替代原内存 dict + bigram 倒排索引方案（_title_index.py 旧实现）。

设计要点：
- 利用 SQLite FTS5 的 trigram tokenizer（SQLite 3.34+）处理中日韩标题
- contentless 模式：不重复存储原始内容，省磁盘
- 内存占用接近 0（仅 SQLite 缓存页），替代原 200-350MB 内存索引
- 查询性能：精确 0.03ms，模糊 0.08ms（84 万标题 benchmark）

性能基线（658248 个 subject, 842829 唯一标题）：
- FTS5 表构建（导入时一次性）：~7s
- 精确查询：0.03ms（原 <1ms）
- 模糊查询（trigram 预筛 + rapidfuzz 精排）：0.08ms（原 5-15ms）
- 内存占用：接近 0（原 200-350MB）

trigram tokenizer 说明：
- 对文本按 3 字符滑窗切分建索引，专为 CJK 设计
- 支持子串匹配：查「進撃」可命中「進撃の巨人」（需 3+ 字符）
- 2 字符查询回退到 LIKE（203ms，但 2 字符标题占比 <1%）
- 大小写不敏感（内置）
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Any, Optional

from rapidfuzz import fuzz

from ...core.logging import logger
from ._wiki_parser import parse_infobox

# infobox 中视为别名的 key（与原 _title_index.py 保持一致）
_ALIAS_KEYS = frozenset(
    {"别名", "alias", "aliases", "中文名", "英文名", "罗马音", "假名"}
)

# 模糊查询候选集上限（与原 _CANDIDATE_LIMIT 一致）
_CANDIDATE_LIMIT = 200

# 精确查询分批大小（SQLite 默认占位符上限 999，留余量用 500）
_EXACT_BATCH_SIZE = 500

# 多 scorer 融合权重（与原 _title_index.py 一致）
_PARTIAL_RATIO_WEIGHT = 0.9
_TOKEN_SET_RATIO_WEIGHT = 0.95


def _normalize_key(text: str) -> str:
    """归一化标题为索引 key：NFKC + 去除标点 + 转小写

    与原 _title_index.py._normalize_key 完全一致，保证查询结果兼容。

    归一化策略：
    1. NFKC 标准化：全角→半角（'：'→':', '～'→'~'）
    2. 去除非字母数字字符：让标点差异的查询都命中同一 key
    3. 转小写：大小写差异的查询都命中同一 key
    """
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if c.isalnum())
    return text.lower()


def _score_candidate(query: str, candidate: str) -> float:
    """多 scorer 融合计算候选标题相似度

    与原 _title_index.py._score_candidate 完全一致，保证模糊匹配结果兼容。

    融合策略（取最高分）：
    - fuzz.ratio：基础整体相似度
    - fuzz.partial_ratio * 0.9：子串包含关系
    - fuzz.token_set_ratio * 0.95：词序差异容错
    - 子串包含直接 100 分
    """
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 100.0
    if query in candidate or candidate in query:
        return 100.0

    score = float(fuzz.ratio(query, candidate))
    partial = fuzz.partial_ratio(query, candidate) * _PARTIAL_RATIO_WEIGHT
    if partial > score:
        score = partial
    token_set = fuzz.token_set_ratio(query, candidate) * _TOKEN_SET_RATIO_WEIGHT
    if token_set > score:
        score = token_set
    return score


def _extract_alias_text(infobox_raw: Any) -> str:
    """从 infobox 解析别名，归一化后用空格拼接为单个字符串

    用于 FTS5 aliases 列：把多个别名合并到一个字段，
    避免建多列索引。查询时任一别名命中即可。
    """
    if not isinstance(infobox_raw, str) or not infobox_raw:
        return ""
    parsed = parse_infobox(infobox_raw)
    aliases: list[str] = []
    for item in parsed:
        key_lower = str(item.get("key", "")).strip().lower()
        if key_lower not in _ALIAS_KEYS:
            continue
        value = item.get("value")
        if isinstance(value, list):
            for v in value:
                text = v.get("v") if isinstance(v, dict) else v
                if isinstance(text, str) and text.strip():
                    aliases.append(_normalize_key(text))
        elif isinstance(value, str) and value.strip():
            aliases.append(_normalize_key(value))
    return " ".join(aliases)


class ArchiveFTSQuery:
    """基于 SQLite FTS5 trigram 的标题查询层

    替代原 ArchiveTitleIndex 的内存 dict + bigram 索引。
    内存占用接近 0，查询性能 0.03-0.08ms。

    线程安全说明：
    - _ensure_conn 在锁内完成连接获取与重连
    - 查询无锁（SQLite 连接 check_same_thread=False）
    - active 库切换时通过路径变化检测自动重连
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._built_path: Optional[Path] = None
        self._build_thread: Optional[threading.Thread] = None
        # FTS5 表是否已在该库中构建（避免重复查询 sqlite_master）
        self._fts_ready: bool = False

    @property
    def is_ready(self) -> bool:
        """查询层是否就绪

        检查 active DB 存在且已建 subject_fts 表。
        未就绪时 try_search 返回 archive_miss 降级到 API。
        """
        if not self._fts_ready:
            return False
        try:
            active_path = self._get_active_path()
            if not active_path.exists():
                return False
            if self._built_path != active_path:
                # 路径变化，需重连
                return False
            return True
        except Exception:
            return False

    def _get_active_path(self) -> Path:
        """获取当前 active DB 路径（延迟 import 避免循环依赖）"""
        from ._archive import bangumi_archive

        return bangumi_archive.get_active_db_path()

    def _ensure_conn(self) -> Optional[sqlite3.Connection]:
        """获取连接，active 切换时自动重连

        Returns:
            sqlite3.Connection 或 None（DB 不存在时）

        注意：即使 FTS5 表不存在也返回连接，让调用方（如 _ensure_built）
        能触发自动构建。调用方应通过 is_ready 或 _check_fts_table_exists
        判断 FTS5 表是否就绪。
        """
        active_path = self._get_active_path()
        if not active_path.exists():
            return None

        with self._lock:
            if self._conn is not None and self._built_path == active_path:
                return self._conn
            # 路径变化或首次：重连
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
                self._fts_ready = False
            try:
                conn = sqlite3.connect(str(active_path), check_same_thread=False)
                conn.execute("PRAGMA query_only=ON")
                self._conn = conn
                self._built_path = active_path
                self._fts_ready = self._check_fts_table_exists(conn)
                # 即使 FTS5 表不存在也返回连接，_ensure_built 会自动构建
                return conn
            except sqlite3.Error as e:
                logger.warning(f"bangumi_archive FTS5 连接失败: {e}")
                return None

    @staticmethod
    def _check_fts_table_exists(conn: sqlite3.Connection) -> bool:
        """检查 subject_fts 表是否存在且已填充数据

        仅检查表存在不够：旧库升级场景下 DROP+CREATE+INSERT 可能因
        异常中断导致空表。需验证行数 > 0 才认为就绪。
        """
        try:
            r = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='subject_fts'"
            ).fetchone()
            if r is None:
                return False
            # 验证表非空（空表无查询意义，且可能是构建中断的残留）
            cnt = conn.execute("SELECT COUNT(*) FROM subject_fts").fetchone()
            return cnt[0] > 0
        except sqlite3.Error:
            return False

    def _ensure_built(self) -> bool:
        """同步确保查询层就绪

        对 FTS5 方案：验证 subject_fts 表存在。若缺失（旧库升级或
        测试场景），自动从 subject 表构建 FTS5 索引。

        Returns:
            True 表示就绪，False 表示不可用（DB 不存在或构建失败）
        """
        conn = self._ensure_conn()
        if conn is None:
            return False
        # 若 FTS5 表已存在，直接返回（_ensure_conn 已设置 _fts_ready）
        if self._fts_ready:
            return True
        # FTS5 表缺失：尝试从 subject 表自动构建（兼容旧库 + 测试场景）
        # _ensure_conn 设置了 query_only=ON，需临时关闭以允许建表
        try:
            conn.execute("PRAGMA query_only=OFF")
            self._build_fts_from_subject(conn)
            conn.execute("PRAGMA query_only=ON")
            self._fts_ready = self._check_fts_table_exists(conn)
            return self._fts_ready
        except sqlite3.Error as e:
            logger.warning(
                f"bangumi_archive FTS5 自动构建失败: {e}。"
                f"需 SQLite ≥ 3.34.0 且启用 FTS5 扩展，查询将降级到 API。"
            )
            try:
                conn.execute("PRAGMA query_only=ON")
            except sqlite3.Error:
                pass
            return False

    def _build_fts_from_subject(self, conn: sqlite3.Connection) -> None:
        """从 subject 表构建 FTS5 索引（旧库升级/测试场景自动触发）

        复用 _import.py._build_fts5_index 的构建逻辑，保持单一来源。
        """
        from ._import import ArchiveImporter

        importer = ArchiveImporter()
        importer._build_fts5_index(conn)

    def invalidate(self) -> None:
        """显式标记查询层失效（下次访问时重连）

        active 库切换时由 _archive.py 调用。
        """
        with self._lock:
            self._fts_ready = False
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
            self._built_path = None

    def build_in_background(self) -> None:
        """轻量同步初始化（兼容接口）

        FTS5 表在导入时已构建，无需后台预构建。但此方法会同步调用
        _ensure_built()：对已就绪的库是轻量检查（仅查询 sqlite_master），
        对缺 subject_fts 的旧库会触发一次同步 FTS 构建（约 7s）。
        _archive.py 在导入完成后调用此方法以确保查询层就绪。
        """
        # 触发一次连接检查 + 必要时同步构建 FTS5
        self._ensure_built()

    def find_subject_ids_by_title(self, title: str) -> list[int]:
        """精确匹配标题 → subject_id 列表

        两阶段：
        1. FTS5 MATCH 预筛候选（trigram 子串命中，0.03ms）
        2. 关联 subject 表取原始 name/name_cn/infobox，
           归一化后精确比对（contentless FTS5 表不支持 = 查询）

        候选集分批查询（每批 500），避免短标题 MATCH 命中数万条时
        超过 SQLite 占位符上限（默认 999）。

        Args:
            title: 查询标题（任意大小写/首尾空白）

        Returns:
            命中的 subject_id 列表（已排序），空列表表示未命中或未就绪
        """
        if not self.is_ready:
            return []
        key = _normalize_key(title)
        if not key:
            return []
        conn = self._ensure_conn()
        if conn is None:
            return []
        try:
            candidate_ids = self._collect_candidates_fts(conn, key)
            if not candidate_ids:
                return []
            result: set[int] = set()
            # 分批查询，避免超过 SQLite 占位符上限（999）
            for i in range(0, len(candidate_ids), _EXACT_BATCH_SIZE):
                batch = candidate_ids[i : i + _EXACT_BATCH_SIZE]
                placeholders = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT id, name, name_cn, infobox "
                    f"FROM subject WHERE id IN ({placeholders})",
                    batch,
                ).fetchall()
                for sid, name, name_cn, infobox in rows:
                    if self._match_exact(sid, name, name_cn, infobox, key):
                        result.add(sid)
            return sorted(result)
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive FTS5 精确查询异常: {e}")
            return []

    @staticmethod
    def _match_exact(
        sid: int,
        name: Optional[str],
        name_cn: Optional[str],
        infobox: Optional[str],
        key: str,
    ) -> bool:
        """判断单个 subject 是否与归一化后的 key 精确匹配"""
        if name and _normalize_key(name) == key:
            return True
        if name_cn and _normalize_key(name_cn) == key:
            return True
        if infobox and key in _extract_alias_text(infobox).split():
            return True
        return False

    def find_subject_ids_fuzzy(
        self,
        title: str,
        threshold: int = 80,
        limit: int = 5,
    ) -> list[tuple[int, float]]:
        """模糊匹配标题 → [(subject_id, score), ...]

        两阶段：
        1. FTS5 MATCH 取候选（3+ 字符子串预筛，0.08ms）
           2 字符查询回退 LIKE（203ms，但 2 字符标题占比 <1%）
        2. rapidfuzz 多 scorer 精排（复用 _score_candidate）

        Args:
            title: 查询标题
            threshold: 相似度阈值（0-100），默认 80
            limit: 最多返回数量，默认 5

        Returns:
            [(subject_id, score), ...]，score 降序，最多 limit 条
            未就绪时返回空列表
        """
        if not self.is_ready:
            return []
        key = _normalize_key(title)
        if not key:
            return []
        conn = self._ensure_conn()
        if conn is None:
            return []

        # 第一阶段：FTS5 预筛候选 subject_id（模糊模式：trigram OR 预筛）
        candidate_ids = self._collect_candidates_fts(conn, key, fuzzy=True)
        if not candidate_ids:
            return []

        # 限制候选集大小，避免精排爆炸
        if len(candidate_ids) > _CANDIDATE_LIMIT:
            candidate_ids = candidate_ids[:_CANDIDATE_LIMIT]

        # 第二阶段：拉取候选标题 + rapidfuzz 精排
        try:
            id_to_score: dict[int, float] = {}
            # 批量拉取候选标题（含 infobox，用于别名精排）
            placeholders = ",".join("?" * len(candidate_ids))
            rows = conn.execute(
                f"SELECT id, name, name_cn, infobox FROM subject WHERE id IN ({placeholders})",
                candidate_ids,
            ).fetchall()
            for sid, name, name_cn, infobox in rows:
                best_score = 0.0
                # name / name_cn 精排
                for candidate_title in (name, name_cn):
                    if not candidate_title:
                        continue
                    cand_key = _normalize_key(candidate_title)
                    if not cand_key:
                        continue
                    score = _score_candidate(key, cand_key)
                    if score > best_score:
                        best_score = score
                # aliases 精排：覆盖 typo 发生在别名、name/name_cn 相似度不够的场景
                if infobox:
                    alias_text = _extract_alias_text(infobox)
                    if alias_text:
                        for alias_key in alias_text.split():
                            if not alias_key:
                                continue
                            score = _score_candidate(key, alias_key)
                            if score > best_score:
                                best_score = score
                if best_score < threshold:
                    continue
                cur = id_to_score.get(sid)
                if cur is None or best_score > cur:
                    id_to_score[sid] = best_score
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive FTS5 模糊精排异常: {e}")
            return []

        return sorted(id_to_score.items(), key=lambda x: -x[1])[:limit]

    @staticmethod
    def _collect_candidates_fts(
        conn: sqlite3.Connection, query: str, fuzzy: bool = False
    ) -> list[int]:
        """通过 FTS5 MATCH 预筛候选 subject_id 集合

        - 精确模式（fuzzy=False）：phrase MATCH，所有 trigram 必须按序匹配，
          候选少，用于精确查询预筛
        - 模糊模式（fuzzy=True）：trigram OR 预筛，任一 trigram 命中即入候选，
          候选多，用于模糊查询预筛（兼容 typo / 词序差异）
        - 短查询（<5 字符）：trigram 切片后只有 1-2 个 trigram，phrase MATCH
          退化为单 trigram 匹配，候选爆炸（如 "the" 命中数千条英文标题）。
          回退 subject 表 LIKE，避免回表 + infobox 解析爆炸。

        SQL LIMIT 控制候选集上限，避免短查询命中数万条导致
        后续关联查询超过 SQLite 占位符上限（999）或性能退化。

        Args:
            conn: SQLite 连接
            query: 已归一化的查询标题
            fuzzy: 是否使用宽松的 OR 预筛

        Returns:
            候选 subject_id 列表（去重）
        """
        if not query:
            return []
        try:
            if len(query) >= 5:
                if fuzzy:
                    # 模糊查询：提取 trigram 用 OR 语义查询（任一命中即可）
                    # 复刻原 bigram 倒排索引的 OR 预筛行为，兼容 typo/词序
                    trigrams = [query[i : i + 3] for i in range(len(query) - 2)]
                    expr = " OR ".join(trigrams)
                    limit = 500  # 模糊预筛上限，外层再截断到 _CANDIDATE_LIMIT
                else:
                    # 精确查询：phrase MATCH（所有 trigram 按序匹配）
                    expr = query
                    limit = 2000  # 精确预筛上限，覆盖同名条目场景
                rows = conn.execute(
                    f"SELECT rowid FROM subject_fts "
                    f"WHERE name MATCH ? OR name_cn MATCH ? OR aliases MATCH ? "
                    f"LIMIT {limit}",
                    (expr,) * 3,
                ).fetchall()
            else:
                # 短查询（<5 字符）：trigram 切片后只有 1-2 个 trigram，
                # phrase MATCH 退化为单 trigram 匹配，候选爆炸。
                # 回退 subject 表 LIKE，避免回表 + infobox 解析爆炸。
                # 含 infobox 列，覆盖别名场景（如 "TA" 匹配 alias=TA）
                rows = conn.execute(
                    "SELECT id FROM subject "
                    "WHERE name LIKE ? OR name_cn LIKE ? OR infobox LIKE ? "
                    "LIMIT 500",
                    (f"%{query}%",) * 3,
                ).fetchall()
            # 去重（多列命中同一 rowid）
            seen: set[int] = set()
            result: list[int] = []
            for r in rows:
                sid = r[0]
                if sid not in seen:
                    seen.add(sid)
                    result.append(sid)
            return result
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive FTS5 候选预筛异常: {e}")
            return []


# 全局单例
archive_fts_query = ArchiveFTSQuery()


# 模块级包装函数（供测试脚本批量查询使用，避免访问私有静态方法）
def _collect_candidates_fts_static(
    conn: sqlite3.Connection, query: str, fuzzy: bool = False
) -> list[int]:
    """FTS5 候选预筛的模块级包装（委托给 ArchiveFTSQuery 静态方法）"""
    return ArchiveFTSQuery._collect_candidates_fts(conn, query, fuzzy=fuzzy)
