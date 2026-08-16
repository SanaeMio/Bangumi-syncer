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

import re
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Any, Optional

from rapidfuzz.distance import DamerauLevenshtein, JaroWinkler

from ...core.logging import logger
from ._title_normalize import (
    ARCHIVE_FUZZY_THRESHOLD,
    fuse_title_similarity,
    strip_bracket_content,
    strip_kana_variant,
    strip_numeral_variant,
    strip_year_suffix,
)
from ._wiki_parser import parse_infobox

# infobox 中视为别名的 key（与原 _title_index.py 保持一致）
_ALIAS_KEYS = frozenset(
    {"别名", "alias", "aliases", "中文名", "英文名", "罗马音", "假名"}
)

# 模糊查询候选集上限（与原 _CANDIDATE_LIMIT 一致）
_CANDIDATE_LIMIT = 200

# BK-tree 模糊匹配容忍的编辑距离（单字符 typo：替换/插入/删除/换位）
# 与 SymSpell 对称删除同为「编辑距离保证」族，召回性质一致；BK-tree
# 额外内存约 69MB（56539 标题实测），是 SymSpell(363MB) 的省内存版。
# 注意：内存与构建时间随标题数线性增长（约 1.2MB/千标题），完整
# 生产库（~84 万唯一标题）外推约 1GB 内存、分钟级构建，内存敏感
# 的容器请先实测再开启。
# 是否启用由 config.ini [bangumi-archive] use_bktree 控制（默认关闭，
# 关闭时回退 trigram OR + 覆盖度预筛，零额外内存，适合小项目）。
_MAX_FUZZY_DIST = 1

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


def _normalize_key_deep(text: str) -> str:
    """深度归一化（装饰剥离 + 轻量归一化），用于跨装饰精确匹配。

    在原始标题文本上先剥除「括号及其内容 / 片假名长音与小书假名 /
    数字编号 / 年份后缀」四类装饰，再走与 _normalize_key 一致的
    NFKC + 去标点 + 小写。

    用途：find_subject_ids_by_title / _match_exact 在原有 _normalize_key
    精确相等之外，额外接受「深度归一化后相等」的命中。这样库方推送的
    「ラジオ「内包」」与 archive 存的「ラジオ」、或「Name 2021」与「Name」
    能互相命中，修复反向诊断定位的 R4/R5/R7/R9 真实缺口。

    设计要点（避免回归）：
    - 仅作「接受条件」的增量扩展，不改 FTS 存储内容，故不触发预建索引
      失同步（_normalize_key 仍用于 FTS 构建与预筛）。
    - 幂等：对已是深度归一化结果再调用，装饰已不在，结果不变。
    - 空结果保护：若剥装饰后为空串，返回 ""，调用方需 guard 非空才采用，
      避免 "" 与空标题误匹配。
    """
    if not isinstance(text, str) or not text:
        return ""
    # 在原始文本上剥装饰（括号/标点此时仍在）
    t = strip_bracket_content(text)
    t = strip_kana_variant(t)
    t = strip_numeral_variant(t)
    t = strip_year_suffix(t)
    t = t.strip()
    if not t:
        return ""
    return _normalize_key(t)


def _score_candidate(query: str, candidate: str) -> float:
    """多 scorer 融合计算候选标题相似度（0~100）

    委托给 bangumi_archive._title_normalize.fuse_title_similarity（G2 统一实现），
    保留 Archive 历史切点：partial*0.9、token_set*0.95、子串包含直接 100 分，
    不启用媒体后缀防误判（Archive FTS 空间已用 _normalize_key 对齐）。
    """
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 100.0
    if query in candidate or candidate in query:
        return 100.0
    return (
        fuse_title_similarity(
            query,
            query,
            candidate,
            None,
            None,
            partial_weight=_PARTIAL_RATIO_WEIGHT,
            token_set_weight=_TOKEN_SET_RATIO_WEIGHT,
            core_contains_weight=0.0,
            media_suffix_guard=False,
            substring_boost=True,
        )
        * 100.0
    )


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


def _year_of(date_str: Any) -> Optional[int]:
    """从 date 字符串抽取 4 位年份（19xx/20xx），无则返回 None。"""
    if not isinstance(date_str, str) or not date_str:
        return None
    m = re.search(r"(?:19|20)\d{2}", date_str)
    return int(m.group(0)) if m else None


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
        # BK-tree 模糊匹配索引（内存，随 active 库切换重建）
        self._bk_tree: Optional[dict] = None
        self._bk_built_path: Optional[Path] = None
        # BK-tree 后台异步构建状态：避免首次 fuzzy 查询阻塞 7-18s
        self._bk_building: bool = False
        self._bk_build_path: Optional[Path] = None
        # BK-tree + 短查询 JaroWinkler 打分的启用开关。
        # None = 由 config.ini [bangumi-archive] use_bktree 决定（默认关闭）；
        # 非 None = 运行时显式覆盖（set_bktree_enabled 设置）。
        self.use_bktree: Optional[bool] = None

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
        """检查 subject_fts 表是否存在且已填充真实数据

        旧判断只看 ``COUNT(*) > 0``，但 contentless FTS5 不存储原文、
        ``SELECT name`` 永远为空串，单凭行数无法区分「正常索引」与
        「建在空数据上的空壳索引」。空壳索引行数也 > 0，却 MATCH 任何词
        都命中 0，会让查询层静默退化且永不自愈（见 _ensure_built 短路逻辑）。

        因此这里额外用一条确定存在的真实标题做 MATCH 探针：命中即视为
        已就绪；命中 0 视为空壳，返回 False 触发 _build_fts_from_subject 重建。
        """
        try:
            r = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='subject_fts'"
            ).fetchone()
            if r is None:
                return False
            # 行数 > 0 是必要条件（表存在但未建/构建中断的残留仍可能为 0）
            cnt = conn.execute("SELECT COUNT(*) FROM subject_fts").fetchone()
            if cnt[0] == 0:
                return False
            # 内容探针：取一条确定存在、且含 ≥3 个可索引字符的 subject 名，
            # 按与构建一致的归一化取前 3 字符作为 MATCH token（trigram 至少 3 字符）。
            # 优先取较长的 name（ORDER BY LENGTH DESC），避免全库只有短标题（如
            # 「銀魂」2 字符）时探针 token 不足 3 字符导致 MATCH 永远 0 命中、
            # 误判为空壳触发无限重建。
            probe = conn.execute(
                "SELECT name FROM subject "
                "WHERE name IS NOT NULL AND name <> '' "
                "ORDER BY LENGTH(name) DESC LIMIT 1"
            ).fetchone()
            if probe is None:
                # subject 表本身为空，无内容可索引，等同未就绪
                return False
            token = _normalize_key(probe[0])[:3]
            if len(token) < 3:
                # 标题归一化后不足 3 字符（如 CJK 2 字标题），trigram 无法 MATCH，
                # 退化为行数判定，避免误杀合法短标题库
                return cnt[0] > 0
            hit = conn.execute(
                "SELECT rowid FROM subject_fts WHERE name MATCH ? LIMIT 1",
                (token,),
            ).fetchone()
            return hit is not None
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
        # _ensure_conn 设置了 query_only=ON，需临时关闭以允许建表。
        # 构建段加锁串行化：查询路径与 build_in_background（导入后）可能
        # 并发触发自愈，同一连接上同时 DROP/CREATE VIRTUAL TABLE 会互相
        # 踩踏（database is locked 或索引被对方 DROP），加锁后等待方通过
        # 双重检查直接复用首个线程的构建结果。
        with self._lock:
            if self._fts_ready:
                return True
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

    def _is_fts_empty_shell_or_missing(self) -> bool:
        """只读探针：FTS 表缺失或为空壳（内容探针命中 0）返回 True。

        仅用于 find_subject_ids_* 的未就绪守卫判断，决定是否需要查询时自愈重建。
        关键：本方法打开一条独立只读连接检查，**不改动**实例的 ``_conn`` /
        ``_built_path`` / ``_fts_ready`` 状态——否则会对「被显式 invalidate 的
        功能正常库」误置为就绪，破坏「invalidate 后查询返回空、降级 API」契约
        （见 test_not_ready_returns_empty_without_building，
        test_invalidate_clears_index 要求 _built_path 仍为 None）。

        返回值语义：
        - True  → FTS 缺失或为空壳，应在查询时由 _ensure_built 自愈重建；
        - False → FTS 功能正常（仅因 invalidate 暂未就绪），保持降级、不重建。
        """
        try:
            active_path = self._get_active_path()
            if not active_path.exists():
                return True
            conn = sqlite3.connect(str(active_path), check_same_thread=False)
            try:
                conn.execute("PRAGMA query_only=ON")
                return not self._check_fts_table_exists(conn)
            finally:
                conn.close()
        except sqlite3.Error:
            return True

    def _build_fts_from_subject(self, conn: sqlite3.Connection) -> None:
        """从 subject 表构建 FTS5 索引（旧库升级/测试场景自动触发）

        复用 _import.py._build_fts5_index 的构建逻辑，保持单一来源。
        """
        from ._import import ArchiveImporter

        importer = ArchiveImporter()
        importer._build_fts5_index(conn)

    def invalidate(self) -> None:
        """显式标记查询层失效（下次访问时重连）

        active 库切换时由 _archive.py 调用。同时丢弃 BK-tree 内存索引。
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
            self._bk_tree = None
            self._bk_built_path = None
            self._bk_building = False
            self._bk_build_path = None

    def set_bktree_enabled(self, enabled: bool) -> None:
        """运行时显式覆盖 BK-tree 模糊匹配开关

        开启：使用 BK-tree 候选生成 + 短查询 JaroWinkler 打分，
        编辑距离保证召回单字符 typo（替换/插入/删除/换位），约 69MB 额外内存
        （56539 标题实测，随标题数线性增长，~1.2MB/千标题）。
        关闭：回退到 trigram OR + 覆盖度预筛，无额外内存索引，适合内存敏感场景。

        注意：默认由 config.ini [bangumi-archive] use_bktree 控制（默认关闭）。
        本方法用于运行时临时覆盖配置文件的设定；active 库切换 invalidate 时
        不会重置该覆盖值（覆盖是会话级的，配置是持久级的）。

        开启时后台异步触发 BK-tree 构建（不阻塞调用方），首次 fuzzy 查询在
        构建完成前走 OR+覆盖度兜底，构建完成后自动切换到 BK-tree 路径。
        关闭时立即释放已缓存的 BK-tree 并复位构建态，不触发任何构建；即便
        此前有后台构建在途，其结果也会因开关关闭而被丢弃（见 _build_bktree_worker）。
        """
        self.use_bktree = bool(enabled)
        if enabled:
            # 后台异步触发构建，避免阻塞；首次查询在就绪前走兜底
            self._maybe_get_bktree()
        else:
            # 关闭：丢弃任何缓存的 BK-tree，释放内存，且不触发构建
            with self._lock:
                self._bk_tree = None
                self._bk_built_path = None
                self._bk_building = False

    def _resolve_use_bktree(self) -> bool:
        """解析 BK-tree 是否启用：运行时显式覆盖优先，否则读配置（默认关闭）

        Returns:
            True 表示启用 BK-tree 路径；False 表示回退 OR+覆盖度兜底。
        """
        if self.use_bktree is not None:
            return self.use_bktree
        try:
            from ...core.config import get_config_manager

            return bool(
                get_config_manager().get_config(
                    "bangumi-archive", "use_bktree", fallback=False
                )
            )
        except Exception:  # pragma: no cover - 配置不可读时安全回退
            return False

    def _build_bktree_index(self, conn: sqlite3.Connection) -> Optional[dict]:
        """纯构建逻辑：从给定连接读取 subject 表构建 BK-tree（不含缓存管理）

        后台构建线程会自行开只读连接调用本方法；同步路径 _ensure_bktree
        也复用本方法。返回 BK-tree 根节点，subject 表不可读时返回 None。
        """
        try:
            items: list[tuple[str, int]] = []
            for sid, name, name_cn in conn.execute(
                "SELECT id, name, name_cn FROM subject"
            ).fetchall():
                for t in (name, name_cn):
                    if not t:
                        continue
                    k = _normalize_key(t)
                    if k:
                        items.append((k, sid))
            root: Optional[dict] = None
            for k, sid in items:
                if root is None:
                    root = {"key": k, "sids": {sid}, "children": {}}
                else:
                    _bktree_add(root, k, sid)
            return root
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive BK-tree 索引构建失败: {e}")
            return None

    def _ensure_bktree(self, conn: sqlite3.Connection) -> Optional[dict]:
        """同步确保 BK-tree 就绪（阻塞构建，约 7-18s）

        仅用于测试或显式需要立即就绪的场景。生产首次 fuzzy 查询走
        _maybe_get_bktree 的后台异步构建，不阻塞调用方。
        """
        with self._lock:
            if self._bk_tree is not None and self._bk_built_path == self._built_path:
                return self._bk_tree
            self._bk_tree = None
            self._bk_built_path = None
        root = self._build_bktree_index(conn)
        with self._lock:
            self._bk_tree = root
            self._bk_built_path = self._built_path
        return root

    def _maybe_get_bktree(self) -> Optional[dict]:
        """非阻塞获取 BK-tree：已就绪直接返回；否则后台异步触发构建并返回 None

        首次 fuzzy 查询调用方据此走 OR+覆盖度兜底，后台构建完成后下一次查询
        即命中 BK-tree 路径（编辑距离保证召回单字符 typo）。线程安全。

        开关语义（源头保证）：仅在 BK-tree 启用（use_bktree 解析为 True，
        即运行时 set_bktree_enabled(True) 或配置 use_bktree=true）时才可能
        触发后台构建；关闭时**绝不**启动构建线程、也不持有任何缓存，直接返回
        None 让调用方走 OR+覆盖度兜底。
        """
        # 关闭时不构建缓存：直接返回，不置 building 标志、不启动线程
        if not self._resolve_use_bktree():
            return None
        with self._lock:
            if self._bk_tree is not None and self._bk_built_path == self._built_path:
                return self._bk_tree
            if self._bk_building:
                return None
            self._bk_building = True
            self._bk_build_path = self._built_path
        # 锁外启动后台线程（daemon，避免进程退出挂起）；线程自行开只读连接
        threading.Thread(target=self._build_bktree_worker, daemon=True).start()
        return None

    def _build_bktree_worker(self) -> None:
        """后台构建 BK-tree：自行开只读连接，避免跨线程共享主线程连接

        构建完成后仅当 active 库路径未变才采用结果（切换会 invalidate 改
        _built_path）；否则丢弃由下次查询重新触发。无论成功失败都复位
        _bk_building，确保不会死锁后续异步触发。
        """
        try:
            with self._lock:
                expect_path = self._bk_build_path
            if expect_path is None:
                return
            try:
                conn = sqlite3.connect(str(expect_path), check_same_thread=False)
                conn.execute("PRAGMA query_only=ON")
            except sqlite3.Error as e:
                logger.warning(f"bangumi_archive BK-tree 后台连接失败: {e}")
                return
            try:
                root = self._build_bktree_index(conn)
            finally:
                conn.close()
            with self._lock:
                # active 库切换（invalidate 改 _built_path）则丢弃，下次重触发；
                # 构建过程中开关被关闭也丢弃，确保「关闭不持有缓存」
                if (
                    self._bk_build_path == expect_path
                    and self._bk_build_path == self._built_path
                    and self._resolve_use_bktree()
                ):
                    self._bk_tree = root
                    self._bk_built_path = expect_path
        finally:
            with self._lock:
                self._bk_building = False

    def build_in_background(self) -> None:
        """轻量同步初始化（兼容接口）

        FTS5 表在导入时已构建，无需后台预构建。但此方法会同步调用
        _ensure_built()：对已就绪的库是轻量检查（仅查询 sqlite_master），
        对缺 subject_fts 的旧库会触发一次同步 FTS 构建（约 7s）。
        _archive.py 在导入完成后调用此方法以确保查询层就绪。

        若配置启用 BK-tree（use_bktree=true），则后台异步触发 BK-tree 构建，
        不阻塞导入完成；首次 fuzzy 查询在构建完成前走 OR+覆盖度兜底。
        """
        # 触发一次连接检查 + 必要时同步构建 FTS5
        self._ensure_built()
        # 配置启用 BK-tree 时，后台异步构建（不阻塞首次查询）
        if self._resolve_use_bktree():
            self._maybe_get_bktree()

    def find_subject_ids_by_title(
        self, title: str, year: Optional[int] = None
    ) -> list[int]:
        """精确匹配标题 → subject_id 列表

        两阶段：
        1. FTS5 MATCH 预筛候选（trigram 子串命中，0.03ms）
        2. 关联 subject 表取原始 name/name_cn/infobox/date，
           归一化后精确比对（contentless FTS5 表不支持 = 查询）

        候选集分批查询（每批 500），避免短标题 MATCH 命中数万条时
        超过 SQLite 占位符上限（默认 999）。

        年份消歧（修复同名多义 / 翻拍痛点 R1+R2）：当查询含 4 位年份
        （19xx/20xx）或显式传入 year 时，先按「去年份的裸标题」匹配，再在
        多个同名候选中优先返回 date 年份相符者——查询 "銀魂 2006" 应优先
        2006 版而非返回全部銀魂（含 2011/2017 版）。year 为 None 且无年份
        时退化为原行为，不影响既有场景。

        Args:
            title: 查询标题（任意大小写/首尾空白）
            year: 显式年份消歧（可选）；为 None 时自动从 title 抽取

        Returns:
            命中的 subject_id 列表（已排序），空列表表示未命中或未就绪
        """
        if not self.is_ready:
            # 未就绪守卫：
            # - FTS 功能正常但被显式 invalidate：保持降级（返回空），由调用方
            #   显式 _ensure_built/build_in_background 重建（与 try_search 降级 API 契约一致，
            #   见 test_not_ready_returns_empty_without_building / test_invalidate_clears_index）。
            # - FTS 缺失或为空壳（内容探针命中 0）：查询时自愈重建，避免静默退化
            #   （见 test_empty_shell_fts_self_heals_on_query）。
            # 用只读探针区分二者，不预先翻转 _fts_ready（否则功能正常库会被误置就绪）。
            if not self._is_fts_empty_shell_or_missing():
                return []
            if not self._ensure_built():
                return []
        # 年份消歧：自动从查询抽取 4 位年份（仅当未显式传入）
        if year is None:
            m = re.search(r"(?:19|20)\d{2}", title)
            if m:
                year = int(m.group(0))
        # 去年份裸标题用于匹配（year 命中时仅用裸标题，避免 "2006" 干扰归一化）
        match_title = title
        if year is not None:
            base = re.sub(r"(?:19|20)\d{2}", "", title)
            if base and base != title:
                match_title = base
        key = _normalize_key(match_title)
        deep_key = _normalize_key_deep(match_title)
        if not key and not deep_key:
            return []
        conn = self._ensure_conn()
        if conn is None:
            return []
        try:
            # 精确/深度匹配候选召回：优先用归一化键等值索引（subject_key_index），
            # 等值查询 O(log n) 且完整（不截断），修复短查询 LIKE 预筛 LIMIT 500
            # 把高 id 真实匹配切掉的问题（反向诊断 R4/R5/R9 的真实缺口来源）。
            # 索引不可用时回退原 FTS/LIKE 预筛，保证向后兼容。
            candidate_ids = _collect_candidates_key_index(conn, key, deep_key)
            if candidate_ids is None:
                candidate_ids = self._collect_candidates_fts(conn, key)
                if deep_key and deep_key != key:
                    candidate_ids += self._collect_candidates_fts(conn, deep_key)
            if not candidate_ids:
                return []
            seen: set[int] = set()
            ordered: list[int] = []
            for cid in candidate_ids:
                if cid not in seen:
                    seen.add(cid)
                    ordered.append(cid)
            # (sid, lvl, date) —— date 用于年份消歧排序
            result: list[tuple[int, int, str]] = []
            # 分批查询，避免超过 SQLite 占位符上限（999）
            for i in range(0, len(ordered), _EXACT_BATCH_SIZE):
                batch = ordered[i : i + _EXACT_BATCH_SIZE]
                placeholders = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT id, name, name_cn, infobox, date "
                    f"FROM subject WHERE id IN ({placeholders})",
                    batch,
                ).fetchall()
                for sid, name, name_cn, infobox, sdate in rows:
                    lvl = self._match_exact(sid, name, name_cn, infobox, key, deep_key)
                    if lvl:
                        result.append((sid, lvl, sdate or ""))
            if year is not None and len(result) > 1:
                # 年份消歧：date 年份相符者置顶（仍保留其余同名候选，不丢召回）
                ym = [r for r in result if _year_of(r[2]) == year]
                others = [r for r in result if _year_of(r[2]) != year]
                ym.sort(key=lambda x: (-x[1], x[0]))
                others.sort(key=lambda x: (-x[1], x[0]))
                result = ym + others
            else:
                # 排序：精确匹配(key 级, lvl=2)优先于深度匹配(lvl=1)，同组内按 id 升序。
                # 缓解「裸查询命中多个装饰变体」的过度合并（精确项排前）。
                result.sort(key=lambda x: (-x[1], x[0]))
            return [sid for sid, _, _ in result]
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
        deep_key: str = "",
    ) -> int:
        """判断单个 subject 是否与归一化后的 key 匹配，返回匹配级别：

        - 2：轻量归一化精确相等（原行为，最可靠）；
        - 1：深度归一化相等（跨括号内容/片假名变体/数字编号/年份后缀装饰命中，
             修复反向诊断 R4/R5/R7/R9 缺口）；
        - 0：不匹配。
        deep_key 为空时不采用深度匹配，避免空串误命中。
        """
        if name:
            if _normalize_key(name) == key:
                return 2
            if deep_key and _normalize_key_deep(name) == deep_key:
                return 1
        if name_cn:
            if _normalize_key(name_cn) == key:
                return 2
            if deep_key and _normalize_key_deep(name_cn) == deep_key:
                return 1
        if infobox and key in _extract_alias_text(infobox).split():
            return 2
        return 0

    def find_subject_ids_fuzzy(
        self,
        title: str,
        threshold: int = ARCHIVE_FUZZY_THRESHOLD,
        limit: int = 5,
    ) -> list[tuple[int, float]]:
        """模糊匹配标题 → [(subject_id, score), ...]

        可开关的双路径（由 config.ini [bangumi-archive] use_bktree 控制，
        默认关闭；set_bktree_enabled 可运行时显式覆盖）：

        「开启」：BK-tree 候选生成 + 短查询 JaroWinkler 打分。
            BK-tree 对编辑距离 ≤1 的 typo（替换/插入/删除/换位）有数学保证的
            召回，候选集极小（~1 条）；短查询（≤5 字符，1 字损毁率高）改用
            JaroWinkler 相似度打分，避免 rapidfuzz.ratio 对短串过严。
            约 69MB 额外内存（56539 标题实测，随标题数线性增长，
            ~1.2MB/千标题；~84 万标题的完整生产库外推约 1GB）。

        「关闭」（默认）：trigram OR 预筛 + trigram 覆盖度预筛（并集），再
            rapidfuzz 精排，无额外内存索引。适合内存敏感的小项目。

        两条路径最终都复用相同的 rapidfuzz 阈值（默认 80）打分契约，
        差异仅在「候选如何从库中召回」。

        Args:
            title: 查询标题
            threshold: 相似度阈值（0-100），默认 80
            limit: 最多返回数量，默认 5

        Returns:
            [(subject_id, score), ...]，score 降序，最多 limit 条
            未就绪时返回空列表
        """
        if not self.is_ready:
            # 未就绪守卫：
            # - FTS 功能正常但被显式 invalidate：保持降级（返回空），由调用方
            #   显式 _ensure_built/build_in_background 重建（与 try_search 降级 API 契约一致，
            #   见 test_not_ready_returns_empty_without_building / test_invalidate_clears_index）。
            # - FTS 缺失或为空壳（内容探针命中 0）：查询时自愈重建，避免静默退化
            #   （见 test_empty_shell_fts_self_heals_on_query）。
            # 用只读探针区分二者，不预先翻转 _fts_ready（否则功能正常库会被误置就绪）。
            if not self._is_fts_empty_shell_or_missing():
                return []
            if not self._ensure_built():
                return []
        key = _normalize_key(title)
        if not key:
            return []
        conn = self._ensure_conn()
        if conn is None:
            return []

        # 路径一（可开关）：BK-tree + 短查询 JaroWinkler 打分
        if self._resolve_use_bktree():
            root = self._maybe_get_bktree()
            if root is not None:
                bk_cands = _query_bktree(root, key, max_dist=_MAX_FUZZY_DIST)
                if len(bk_cands) > _CANDIDATE_LIMIT:
                    bk_cands = bk_cands[:_CANDIDATE_LIMIT]
                if bk_cands:
                    bk_res = self._score_fuzzy_candidates(
                        conn, bk_cands, title, threshold, limit, use_jw=True
                    )
                    if bk_res:
                        return bk_res

        # 路径二（兜底）：trigram OR 预筛 + trigram 覆盖度预筛（并集）
        candidate_ids = self._collect_candidates_fts(conn, key, fuzzy=True)
        if len(key) >= 5:
            seen = set(candidate_ids)
            for c in _collect_candidates_coverage(conn, key):
                if c not in seen:
                    seen.add(c)
                    candidate_ids.append(c)
        if not candidate_ids:
            return []
        if len(candidate_ids) > _CANDIDATE_LIMIT:
            candidate_ids = candidate_ids[:_CANDIDATE_LIMIT]
        return self._score_fuzzy_candidates(
            conn, candidate_ids, title, threshold, limit, use_jw=False
        )

    def _score_fuzzy_candidates(
        self,
        conn: sqlite3.Connection,
        candidate_ids: list[int],
        title: str,
        threshold: int,
        limit: int,
        use_jw: bool,
    ) -> list[tuple[int, float]]:
        """对候选 subject_id 拉取标题并打分，返回 [(sid, score)] 降序截断

        Args:
            use_jw: 是否对短查询（归一化后 ≤5 字符）改用 JaroWinkler 相似度。
                短串 1 字损毁时 rapidfuzz.ratio 过严，JW 更稳；长查询仍用
                _score_candidate 多 scorer 融合。
        """
        if not candidate_ids:
            return []
        key = _normalize_key(title)
        short_query = use_jw and len(key) <= 5
        try:
            id_to_score: dict[int, float] = {}
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
                    if short_query:
                        score = JaroWinkler.similarity(key, cand_key) * 100
                    else:
                        score = _score_candidate(key, cand_key)
                    if score > best_score:
                        best_score = score
                # aliases 精排：覆盖 typo 发生在别名、name/name_cn 相似度不够的场景
                # （短查询停用别名，避免短查询下别名噪声拉低精度）
                if infobox and not short_query:
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


# 归一化键等值索引表：为精确/深度匹配提供 O(log n) 的等值候选召回，
# 取代短查询 LIKE 预筛的 LIMIT 截断（避免常见短查询丢失真实匹配）。
_KEY_INDEX_TABLE = "subject_key_index"
_KEY_INDEX_BUILD_BATCH = 5000
# 模块级锁：保护 key_index 懒构建，避免多线程并发首次查询触发
# `database is locked`（DROP/CREATE/INSERT/commit 非原子，需串行化）。
# 注意：实例锁 self._lock 无法跨实例生效，故用模块级锁。
_key_index_lock = threading.Lock()


def _build_key_index(conn: sqlite3.Connection) -> None:
    """构建归一化键等值索引（subject_key_index）。

    对 subject 的 name / name_cn / infobox 别名计算轻量与深度归一化键，
    写入 (k, sid) 行并建索引。精确/深度匹配时只需 ``WHERE k = ?`` 等值查询，
    即得到完整候选集（不截断），从根本上修复短查询 LIKE 预筛 LIMIT 500
    把高 id 真实匹配切掉的问题（反向诊断 R4/R5/R9 的真实缺口来源）。

    构建会写入 archive DB 文件（与 subject_fts 表一致，受 query_only 切换保护）；
    失败（如只读库）由调用方回退到原 FTS/LIKE 预筛。

    调用方应持有 _key_index_lock 以避免并发构建冲突。
    """
    try:
        conn.execute("PRAGMA query_only=OFF")
        conn.execute(f"DROP TABLE IF EXISTS {_KEY_INDEX_TABLE}")
        conn.execute(
            f"CREATE TABLE {_KEY_INDEX_TABLE} (k TEXT NOT NULL, sid INTEGER NOT NULL)"
        )
        conn.execute(f"CREATE INDEX idx_{_KEY_INDEX_TABLE}_k ON {_KEY_INDEX_TABLE}(k)")
        cursor = conn.execute("SELECT id, name, name_cn, infobox FROM subject")
        rows_to_insert: list[tuple[str, int]] = []
        while True:
            rows = cursor.fetchmany(_KEY_INDEX_BUILD_BATCH)
            if not rows:
                break
            for sid, name, name_cn, infobox in rows:
                keys: set[str] = set()
                if name:
                    keys.add(_normalize_key(name))
                    dk = _normalize_key_deep(name)
                    if dk:
                        keys.add(dk)
                if name_cn:
                    keys.add(_normalize_key(name_cn))
                    dk = _normalize_key_deep(name_cn)
                    if dk:
                        keys.add(dk)
                if infobox:
                    for alias in _extract_alias_text(infobox).split():
                        if alias:
                            keys.add(alias)
                for k in keys:
                    if k:
                        rows_to_insert.append((k, sid))
        if rows_to_insert:
            conn.executemany(
                f"INSERT INTO {_KEY_INDEX_TABLE}(k, sid) VALUES (?, ?)",
                rows_to_insert,
            )
        conn.commit()
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error as e:
        logger.warning(f"bangumi_archive 归一化键索引构建失败: {e}")
        try:
            conn.execute("PRAGMA query_only=ON")
        except sqlite3.Error:
            pass


def _ensure_key_index(conn: sqlite3.Connection) -> bool:
    """惰性确保当前连接的 archive DB 已构建归一化键索引。

    通过「表名存在 + 内容非空」双重检测区分三种状态：
    - 表不存在 → 构建后返回
    - 表存在且非空 → 直接返回 True
    - 表存在但为空（构建中断/OOM/进程被 kill 残留）→ 触发重建，避免静默失效

    多线程并发首次查询时由 _key_index_lock 串行化构建，避免 `database is locked`。

    Returns:
        True 表示索引可用；False 表示不可用（调用方应回退原预筛）。
    """
    with _key_index_lock:
        try:
            r = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (_KEY_INDEX_TABLE,),
            ).fetchone()
            if r is not None:
                # 表存在：追加 COUNT 探针，空表（损坏残留）触发重建
                cnt = conn.execute(
                    f"SELECT COUNT(*) FROM {_KEY_INDEX_TABLE}"
                ).fetchone()
                if cnt and cnt[0] > 0:
                    return True
                # 表存在但为空：落入重建路径
        except sqlite3.Error:
            return False
        try:
            _build_key_index(conn)
            return True
        except sqlite3.Error:
            return False


def _collect_candidates_key_index(
    conn: sqlite3.Connection, key: str, deep_key: str
) -> list[int] | None:
    """通过归一化键等值索引召回精确/深度匹配候选。

    返回按 (key, deep_key) 等值查询并集去重后的 sid 列表；索引不可用时返回
    None（调用方回退原 FTS/LIKE 预筛）。结果完整（不截断），是修复短查询
    预筛截断的关键。

    注意：索引可用但等值查询返回空列表时返回 []（非 None），表示「确实无匹配」；
    索引不可用（_ensure_key_index 失败）时返回 None，触发回退。两者由
    _ensure_key_index 的 COUNT 探针保证区分——空表会被重建，不会静默失效。
    """
    if not _ensure_key_index(conn):
        return None
    try:
        result: list[int] = []
        seen: set[int] = set()
        for k in (key, deep_key):
            if not k:
                continue
            rows = conn.execute(
                f"SELECT sid FROM {_KEY_INDEX_TABLE} WHERE k = ?", (k,)
            ).fetchall()
            for (sid,) in rows:
                if sid not in seen:
                    seen.add(sid)
                    result.append(sid)
        return result
    except sqlite3.Error as e:
        logger.warning(f"bangumi_archive 键索引等值查询异常: {e}")
        return None


# ---------------------------------------------------------------------------
# BK-tree 模糊匹配（编辑距离保证召回单字符 typo；省内存版 SymSpell）
# ---------------------------------------------------------------------------
def _bktree_add(node: dict, key: str, sid: int) -> None:
    """将 key→sid 插入 BK-tree 节点（按 Damerau-Levenshtein 距离分桶）"""
    d = DamerauLevenshtein.distance(key, node["key"])
    if d == 0:
        node["sids"].add(sid)
        return
    child = node["children"].get(d)
    if child is None:
        node["children"][d] = {"key": key, "sids": {sid}, "children": {}}
    else:
        _bktree_add(child, key, sid)


def _query_bktree(
    root: Optional[dict], query: str, max_dist: int = _MAX_FUZZY_DIST
) -> list[int]:
    """在 BK-tree 中检索与 query 编辑距离 ≤ max_dist 的所有 subject_id

    利用度量树剪枝：仅访问距离区间 [d-max_dist, d+max_dist] 的子树。
    返回去重后的 sid 列表（可能含少量超出候选上限，调用方再截断）。
    """
    results: set[int] = set()
    if root is None or not query:
        return []
    stack = [root]
    while stack:
        n = stack.pop()
        d = DamerauLevenshtein.distance(query, n["key"])
        if d <= max_dist:
            results |= n["sids"]
        lo = d - max_dist
        hi = d + max_dist
        for cd, child in n["children"].items():
            if lo <= cd <= hi:
                stack.append(child)
    return list(results)


def _collect_candidates_coverage(
    conn: sqlite3.Connection,
    query: str,
    max_dist: int = _MAX_FUZZY_DIST,
    per_gram_limit: int = 500,
) -> list[int]:
    """trigram 覆盖度预筛：要求候选与查询共享 ≥ (trigram 数 − max_dist) 个 trigram

    不像 trigram OR 那样「任一命中即可」（OR 会对含常见 trigram 的无关条目
    召回爆炸），而是按覆盖度过滤，对单字符 typo 召回更稳、候选更少。
    作为关闭 BK-tree 后的兜底预筛，与 OR 预筛取并集。
    """
    q_grams = [query[i : i + 3] for i in range(len(query) - 2)]
    if not q_grams:
        return []
    min_shared = max(1, len(q_grams) - max_dist)
    counts: dict[int, int] = {}
    try:
        for g in q_grams:
            rows = conn.execute(
                "SELECT rowid FROM subject_fts "
                "WHERE name MATCH ? OR name_cn MATCH ? OR aliases MATCH ? LIMIT ?",
                (g,) * 3 + (per_gram_limit,),
            ).fetchall()
            for (sid,) in rows:
                counts[sid] = counts.get(sid, 0) + 1
    except sqlite3.Error as e:
        logger.warning(f"bangumi_archive 覆盖度候选预筛异常: {e}")
        return []
    return [sid for sid, c in counts.items() if c >= min_shared]


# 全局单例
archive_fts_query = ArchiveFTSQuery()


# 模块级包装函数（供测试脚本批量查询使用，避免访问私有静态方法）
def _collect_candidates_fts_static(
    conn: sqlite3.Connection, query: str, fuzzy: bool = False
) -> list[int]:
    """FTS5 候选预筛的模块级包装（委托给 ArchiveFTSQuery 静态方法）"""
    return ArchiveFTSQuery._collect_candidates_fts(conn, query, fuzzy=fuzzy)
