"""Archive 导入器：zip → 解压 → JSON Lines → SQLite

职责：
- 解压 dump zip 到临时目录
- 解析各表的 JSON Lines 文件
- 全量导入到目标 SQLite 库（双库切换写入非 active 库）
- 建立索引
- 行数统计与校验
- 清空旧库（VACUUM 后释放磁盘）

schema 设计原则：
- 每次导入都是新建库（DROP TABLE IF EXISTS + CREATE）
- Archive 历史扩字段 4 次（2023-07/2024-08/2025-04/2025-09），
  按 README 字段定义建表，遇到 dump 中缺失的列时跳过（NULL）
- 查询代码使用显式列名，不依赖 SELECT *
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from ...core.logging import logger
from ..bangumi_constants import ARCHIVE_ALLOWED_SUBJECT_TYPES

# JSON Lines 批量插入大小
# 配合显式 BEGIN 事务，整表导入单次 commit；50000 行/批减少 executemany 调用次数
_BATCH_SIZE = 50000

# 进度回调签名: (task_id, percent, message) -> None
ProgressCb = Callable[[str, int, str], None]


# ===== 表 schema 定义 =====
# 每个表对应一个 (CREATE_SQL, INSERT_SQL, JSON_FIELDS) 三元组
# JSON_FIELDS 列表顺序与 INSERT_SQL 的占位符顺序一致

_SCHEMA: dict[str, dict[str, Any]] = {
    "subject": {
        # type/platform/date 字段允许 NULL（旧条目可能缺失）
        "create": """
            CREATE TABLE subject (
                id INTEGER PRIMARY KEY,
                type INTEGER,
                name TEXT,
                name_cn TEXT,
                infobox TEXT,
                platform TEXT,
                summary TEXT,
                nsfw INTEGER,
                date TEXT,
                favorite INTEGER,
                series INTEGER,
                tags TEXT,
                score REAL,
                score_details TEXT,
                rank INTEGER,
                meta_tags TEXT
            )
        """,
        "fields": [
            "id",
            "type",
            "name",
            "name_cn",
            "infobox",
            "platform",
            "summary",
            "nsfw",
            "date",
            "favorite",
            "series",
            "tags",
            "score",
            "score_details",
            "rank",
            "meta_tags",
        ],
        # tags/score/score_details/rank/meta_tags 是 2023+ 新增字段，可能缺失
        "optional_fields": {"tags", "score", "score_details", "rank", "meta_tags"},
        "index_sqls": [
            "CREATE INDEX idx_subject_name ON subject(name)",
            "CREATE INDEX idx_subject_name_cn ON subject(name_cn)",
            "CREATE INDEX idx_subject_date ON subject(date)",
            "CREATE INDEX idx_subject_type ON subject(type)",
        ],
    },
    "episode": {
        "create": """
            CREATE TABLE episode (
                id INTEGER PRIMARY KEY,
                name TEXT,
                name_cn TEXT,
                description TEXT,
                airdate TEXT,
                disc TEXT,
                duration TEXT,
                subject_id INTEGER,
                sort INTEGER,
                type INTEGER
            )
        """,
        "fields": [
            "id",
            "name",
            "name_cn",
            "description",
            "airdate",
            "disc",
            "duration",
            "subject_id",
            "sort",
            "type",
        ],
        "optional_fields": set(),
        "index_sqls": [
            "CREATE INDEX idx_episode_subject ON episode(subject_id)",
            "CREATE INDEX idx_episode_subject_sort ON episode(subject_id, sort)",
            "CREATE INDEX idx_episode_type ON episode(type)",
            # 放送日历视图按 airdate 范围查询，ISO 日期字符串可直接比较
            "CREATE INDEX idx_episode_airdate ON episode(airdate)",
        ],
    },
    "subject_relation": {
        "create": """
            CREATE TABLE subject_relation (
                subject_id INTEGER,
                relation_type INTEGER,
                related_subject_id INTEGER,
                "order" INTEGER,
                PRIMARY KEY (subject_id, relation_type, related_subject_id)
            )
        """,
        "fields": ["subject_id", "relation_type", "related_subject_id", "order"],
        "optional_fields": set(),
        "index_sqls": [
            "CREATE INDEX idx_relation_subject ON subject_relation(subject_id)",
            "CREATE INDEX idx_relation_related ON subject_relation(related_subject_id)",
            "CREATE INDEX idx_relation_type ON subject_relation(relation_type)",
        ],
    },
}

# 注：以下死表已从导入流程中移除（运行时从未被 SELECT，纯属磁盘浪费）：
# - subject_character（条目-角色关联）
# - subject_person（条目-人物关联）
# - person_relation（人物间关联）
# 如未来需要角色/人物查询能力，可在此处重新登记 schema 并加入 _IMPORT_ORDER。

# 导入顺序（subject 先于 episode，避免外键引用问题；SQLite 默认不启用 FK，但仍按依赖顺序）
_IMPORT_ORDER = [
    "subject",
    "episode",
    "subject_relation",
]

# 各表对应的 dump 文件名（基于 Archive README 描述的命名约定）
# 实际解压后文件名可能是 subject.jsonlines 或 subject.json 等，importer 会自动探测
_FILE_NAME_CANDIDATES = {
    "subject": ["subject.jsonlines", "subject.jsonl", "subject.json"],
    "episode": ["episode.jsonlines", "episode.jsonl", "episode.json"],
    "subject_relation": [
        "subject-relations.jsonlines",
        "subject-relations.jsonl",
        "subject-relations.json",
        "subject_relations.jsonlines",
        "subject_relations.jsonl",
    ],
}


class ArchiveImporter:
    """Archive dump 导入器

    用法：
        importer = ArchiveImporter()
        row_counts, duration = await importer.import_all(
            zip_path=Path("dump.zip"),
            target_db=Path("bangumi_archive_b.db"),
            task_id="20260726_120000",
            progress_cb=callback,
        )
    """

    async def import_all(
        self,
        zip_path: Path,
        target_db: Path,
        task_id: str,
        progress_cb: Optional[ProgressCb] = None,
    ) -> tuple[dict[str, int], float]:
        """完整导入流程

        Args:
            zip_path: dump zip 文件路径
            target_db: 目标 SQLite 库路径
            task_id: 任务 ID
            progress_cb: 进度回调 (task_id, stage_str, percent, message)

        Returns:
            (row_counts, duration_sec)
        """
        start = time.monotonic()

        # 1. 解压到临时目录
        extract_dir = zip_path.parent / f"{zip_path.stem}_extracted"
        if extract_dir.exists():
            import shutil

            shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            if progress_cb:
                progress_cb(task_id, "extracting", 63, f"解压 {zip_path.name}")
            await asyncio.to_thread(self._extract_zip, zip_path, extract_dir)
            if progress_cb:
                progress_cb(task_id, "extracting", 65, "解压完成")

            # 解压完成后立即删除 zip，释放磁盘空间（数据已落到 extract_dir）
            # 降低导入阶段磁盘峰值：zip(0.4GB) + 解压(1GB) + 新库(0.8GB) → 解压(1GB) + 新库(0.8GB)
            try:
                if zip_path.exists():
                    zip_path.unlink()
                    logger.debug(
                        f"bangumi_archive: 解压完成，已删除 zip {zip_path.name}"
                    )
            except OSError as e:
                logger.warning(f"bangumi_archive: 解压后删除 zip 失败: {e}")

            # 2. 删除目标库（如存在），全新建立
            if target_db.exists():
                target_db.unlink()
            target_db.parent.mkdir(parents=True, exist_ok=True)

            # 3. 逐表导入
            row_counts: dict[str, int] = {}
            total_tables = len(_IMPORT_ORDER)
            for idx, table_name in enumerate(_IMPORT_ORDER):
                jsonl_path = self._find_jsonl(extract_dir, table_name)
                if jsonl_path is None:
                    logger.warning(
                        f"bangumi_archive: 表 {table_name} 的 dump 文件不存在，跳过"
                    )
                    row_counts[table_name] = 0
                    continue

                if progress_cb:
                    progress_cb(
                        task_id,
                        "import_table",
                        65,
                        f"开始导入 {table_name} ({idx + 1}/{total_tables})",
                    )

                count = await asyncio.to_thread(
                    self._import_table, target_db, table_name, jsonl_path
                )
                row_counts[table_name] = count
                logger.info(f"bangumi_archive: 表 {table_name} 导入完成，{count} 行")

                if progress_cb:
                    pct = 65 + int((idx + 1) / total_tables * 20)
                    progress_cb(
                        task_id,
                        "import_table",
                        pct,
                        f"导入 {table_name} 完成（{count} 行）",
                    )

            # 4. 级联清理孤儿行
            # subject 表导入时按 type 过滤，episode / subject_relation 中
            # 引用被丢弃 subject_id 的行需删除，避免索引膨胀与无效回表
            if progress_cb:
                progress_cb(task_id, "cleanup", 86, "清理孤儿行")
            await asyncio.to_thread(self._cleanup_orphans, target_db)

            # 5. 建立索引
            if progress_cb:
                progress_cb(task_id, "indexing", 88, "建立索引")
            await asyncio.to_thread(self._create_indexes, target_db)
            if progress_cb:
                progress_cb(task_id, "indexing", 92, "索引建立完成")

            # 6. VACUUM 压缩
            if progress_cb:
                progress_cb(task_id, "vacuuming", 93, "VACUUM 压缩数据库")
            await asyncio.to_thread(self._vacuum, target_db)
            if progress_cb:
                progress_cb(task_id, "vacuuming", 94, "VACUUM 完成")

            duration = time.monotonic() - start
            return row_counts, duration
        finally:
            import shutil

            shutil.rmtree(extract_dir, ignore_errors=True)

    def _extract_zip(self, zip_path: Path, extract_dir: Path) -> None:
        """解压 zip 到目标目录（逐成员校验，防止 Zip-Slip 路径穿越）

        本地上传入口（/api/bangumi_archive/import_local）的 zip 内容不可信，
        若成员名含 ``../`` 或绝对路径，extractall 会写到 extract_dir 之外
        （如覆盖 config.ini），因此逐成员校验后再解压，危险成员直接跳过。
        """
        extract_root = extract_dir.resolve()
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                # 1) 拒绝路径穿越：兼容 POSIX/Windows 分隔符的 .. 与绝对路径
                norm_name = member.filename.replace("\\", "/")
                first_part = norm_name.split("/", 1)[0]
                if (
                    norm_name.startswith("/")
                    or ".." in norm_name.split("/")
                    or ":" in first_part  # Windows 盘符（如 C:/...）
                ):
                    logger.warning(
                        f"bangumi_archive: 跳过路径穿越风险成员（Zip-Slip）: "
                        f"{member.filename!r}"
                    )
                    continue
                # 2) 二次防线：解析后的目标路径必须仍在解压根目录内
                target = (extract_dir / member.filename).resolve()
                if not target.is_relative_to(extract_root):
                    logger.warning(
                        f"bangumi_archive: 跳过越界成员: {member.filename!r}"
                    )
                    continue
                zf.extract(member, extract_dir)
        logger.info(f"bangumi_archive: 已解压 {zip_path.name} 到 {extract_dir}")

    def _find_jsonl(self, extract_dir: Path, table_name: str) -> Path | None:
        """在解压目录中查找指定表的 JSON Lines 文件

        支持多种命名变体（如 subject.jsonlines / subject.jsonl），
        以及嵌套在子目录中的情况。
        """
        candidates = _FILE_NAME_CANDIDATES.get(table_name, [])
        # 先在根目录查找
        for filename in candidates:
            path = extract_dir / filename
            if path.is_file():
                return path
        # 在子目录中查找（部分 dump 可能嵌套在子目录）
        for item in extract_dir.rglob("*"):
            if item.is_file() and item.name in candidates:
                return item
        return None

    def _import_table(self, db_path: Path, table_name: str, jsonl_path: Path) -> int:
        """导入单个表

        Args:
            db_path: SQLite 库路径
            table_name: 表名
            jsonl_path: JSON Lines 文件路径

        Returns:
            导入行数
        """
        schema = _SCHEMA.get(table_name)
        if schema is None:
            logger.warning(f"bangumi_archive: 未知表 {table_name}，跳过")
            return 0

        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            # 建表
            conn.execute(schema["create"])
            conn.commit()

            # 准备 INSERT 语句
            # 用 INSERT OR IGNORE 而非 INSERT OR REPLACE：
            # - 比 INSERT OR REPLACE 快（不触发 DELETE+INSERT，仅忽略冲突行）
            # - dump 可能含重复主键（如 subject_relation 复合主键重复），OR IGNORE 静默跳过
            fields = schema["fields"]
            placeholders = ",".join(["?"] * len(fields))
            col_names = ",".join(f'"{f}"' for f in fields)
            insert_sql = f"INSERT OR IGNORE INTO {table_name} ({col_names}) VALUES ({placeholders})"

            # 批量读取并插入
            count = 0
            batch: list[tuple] = []

            # 显式开启事务，整表导入单次 commit
            # 避免每批 commit 触发 WAL 刷盘（84 万行 / 5000 = 168 次 commit → 1 次）
            conn.execute("BEGIN")
            try:
                with open(jsonl_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError as e:
                            logger.warning(
                                f"bangumi_archive: {table_name} 行解析失败，跳过: {e}"
                            )
                            continue
                        if not isinstance(row, dict):
                            continue

                        # type 过滤：subject 表仅保留动画(2)与三次元(6)
                        # 业务层查询时仅放行这两种类型，其他类型导入即浪费磁盘
                        if table_name == "subject":
                            if row.get("type") not in ARCHIVE_ALLOWED_SUBJECT_TYPES:
                                continue

                        # 提取字段（optional 字段缺失时填 None）
                        values = []
                        for field in fields:
                            v = row.get(field)
                            # list/dict 类型序列化为 JSON 字符串存储
                            if isinstance(v, (list, dict)):
                                v = json.dumps(v, ensure_ascii=False)
                            values.append(v)
                        batch.append(tuple(values))
                        count += 1

                        if len(batch) >= _BATCH_SIZE:
                            conn.executemany(insert_sql, batch)
                            batch.clear()

                    # 插入剩余
                    if batch:
                        conn.executemany(insert_sql, batch)
                        batch.clear()
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            logger.debug(
                f"bangumi_archive: {table_name} 导入 {count} 行 (from {jsonl_path.name})"
            )
            return count
        finally:
            conn.close()

    def _cleanup_orphans(self, db_path: Path) -> None:
        """清理 subject type 过滤产生的孤儿行

        subject 表导入时仅保留 type∈(2,6) 的条目，episode / subject_relation
        中引用被丢弃 subject_id 的行需删除：
        - episode.subject_id 指向被丢弃条目
        - subject_relation.subject_id 或 related_subject_id 指向被丢弃条目

        无外键约束（SQLite 默认不启用 FK），故需手动级联清理。
        """
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            # episode 孤儿行
            # NOT EXISTS 走 subject 主键索引，比 NOT IN 物化子查询更快
            cur = conn.execute(
                "DELETE FROM episode WHERE NOT EXISTS "
                "(SELECT 1 FROM subject WHERE subject.id = episode.subject_id)"
            )
            ep_removed = cur.rowcount
            # subject_relation 孤儿行（任一端指向被丢弃条目即删除）
            cur = conn.execute(
                "DELETE FROM subject_relation "
                "WHERE NOT EXISTS (SELECT 1 FROM subject WHERE subject.id = subject_relation.subject_id) "
                "OR NOT EXISTS (SELECT 1 FROM subject WHERE subject.id = subject_relation.related_subject_id)"
            )
            rel_removed = cur.rowcount
            conn.commit()
            if ep_removed or rel_removed:
                logger.info(
                    f"bangumi_archive: 孤儿行清理完成，"
                    f"episode 移除 {ep_removed} 行，subject_relation 移除 {rel_removed} 行"
                )
        finally:
            conn.close()

    def _create_indexes(self, db_path: Path) -> None:
        """为所有表建立索引"""
        conn = sqlite3.connect(str(db_path))
        try:
            for table_name, schema in _SCHEMA.items():
                for index_sql in schema.get("index_sqls", []):
                    try:
                        conn.execute(index_sql)
                    except sqlite3.OperationalError as e:
                        logger.warning(
                            f"bangumi_archive: 建立索引失败 ({table_name}): {e}"
                        )
            conn.commit()
            logger.info("bangumi_archive: 索引建立完成")

            # 构建 FTS5 trigram 标题索引（替代原内存 dict + bigram 索引）
            # 内存占用从 200-350MB 降到接近 0，查询性能 0.03-0.08ms
            self._build_fts5_index(conn)
        finally:
            conn.close()

    def _build_fts5_index(self, conn: sqlite3.Connection) -> None:
        """构建 FTS5 trigram 标题索引

        从 subject 表读取 name/name_cn/infobox，归一化后填充到
        subject_fts 虚拟表。trigram tokenizer 专为 CJK 设计，
        支持 3+ 字符子串匹配。

        性能：84 万标题约 7s（含归一化 + 插入 + optimize）。
        """
        from ._fts_query import _extract_alias_text, _normalize_key

        try:
            # contentless 模式：不重复存储原始内容，省磁盘
            # 先 DROP 再 CREATE，确保旧库升级/重建场景下不会因 rowid 冲突
            # 导致 INSERT 失败（contentless FTS5 不支持 INSERT OR REPLACE）
            conn.execute("DROP TABLE IF EXISTS subject_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE subject_fts USING fts5("
                "name, name_cn, aliases, content='', tokenize='trigram'"
                ")"
            )
            # 批量读取 subject 表，归一化后插入 FTS5
            # subject 表已按 type 过滤（仅保留 2/6），此处无需再过滤
            cursor = conn.execute("SELECT id, name, name_cn, infobox FROM subject")
            insert_sql = (
                "INSERT INTO subject_fts(rowid, name, name_cn, aliases) "
                "VALUES (?, ?, ?, ?)"
            )
            batch: list[tuple[int, str, str, str]] = []
            count = 0
            while True:
                rows = cursor.fetchmany(_BATCH_SIZE)
                if not rows:
                    break
                for row in rows:
                    sid, name, name_cn, infobox = row
                    norm_name = _normalize_key(name or "")
                    norm_cn = _normalize_key(name_cn or "")
                    aliases = _extract_alias_text(infobox)
                    # 跳过完全无标题的条目（避免空行污染索引）
                    if not norm_name and not norm_cn and not aliases:
                        continue
                    batch.append((sid, norm_name, norm_cn, aliases))
                # 攒满 _BATCH_SIZE 再批量插入（与 _import_table 一致）
                if len(batch) >= _BATCH_SIZE:
                    conn.executemany(insert_sql, batch)
                    count += len(batch)
                    batch.clear()
            # 插入剩余
            if batch:
                conn.executemany(insert_sql, batch)
                count += len(batch)
                batch.clear()
            # optimize 合并索引段，提升查询性能
            conn.execute("INSERT INTO subject_fts(subject_fts) VALUES('optimize')")
            conn.commit()
            logger.info(f"bangumi_archive: FTS5 trigram 索引构建完成，{count} 条标题")
        except sqlite3.OperationalError as e:
            # FTS5 不可用（老版本 SQLite）时跳过，查询层会降级到 API
            logger.warning(
                f"bangumi_archive: FTS5 trigram 索引构建失败: {e}。"
                f"需 SQLite ≥ 3.34.0 且启用 FTS5 扩展，查询将降级到 API。"
            )

    def _vacuum(self, db_path: Path) -> None:
        """VACUUM 压缩数据库"""
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("VACUUM")
            logger.debug(f"bangumi_archive: VACUUM 完成 ({db_path.name})")
        finally:
            conn.close()

    def clear_database(self, db_path: Path) -> None:
        """清空数据库（删除文件，释放磁盘）

        用于双库模式：导入成功切换后清空旧库。
        同时清理 WAL/SHM 文件，以及旧版本（FTS5 改造前）残留的 .index 磁盘缓存。
        """
        try:
            if db_path.exists():
                db_path.unlink()
                logger.info(f"bangumi_archive: 已清空旧库 {db_path.name}")
            # 同时清理 WAL/SHM 文件
            for suffix in ("-wal", "-shm"):
                sidecar = db_path.with_suffix(db_path.suffix + suffix)
                if sidecar.exists():
                    try:
                        sidecar.unlink()
                    except OSError:
                        pass
            # 清理旧版本残留的 .index 磁盘缓存（FTS5 改造后已废弃，
            # 仅为从旧版升级的用户清理残留文件，新装用户无此文件）
            # db_path 名为 bangumi_archive_{a|b}.db，对应缓存为 bangumi_archive_{a|b}.index
            index_path = db_path.with_suffix(".index")
            if index_path.exists():
                try:
                    index_path.unlink()
                    logger.info(
                        f"bangumi_archive: 已清理旧版索引缓存 {index_path.name}"
                    )
                except OSError as e:
                    logger.warning(
                        f"bangumi_archive: 清理旧版索引缓存失败 {index_path}: {e}"
                    )
        except OSError as e:
            logger.warning(f"bangumi_archive: 清空数据库失败 {db_path}: {e}")
