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

# JSON Lines 批量插入大小
_BATCH_SIZE = 5000

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
    "subject_character": {
        "create": """
            CREATE TABLE subject_character (
                character_id INTEGER,
                subject_id INTEGER,
                type INTEGER,
                "order" INTEGER,
                PRIMARY KEY (character_id, subject_id)
            )
        """,
        "fields": ["character_id", "subject_id", "type", "order"],
        "optional_fields": set(),
        "index_sqls": [
            "CREATE INDEX idx_subchar_subject ON subject_character(subject_id)",
            "CREATE INDEX idx_subchar_character ON subject_character(character_id)",
        ],
    },
    "subject_person": {
        "create": """
            CREATE TABLE subject_person (
                person_id INTEGER,
                subject_id INTEGER,
                position INTEGER,
                appear_eps TEXT,
                PRIMARY KEY (person_id, subject_id, position)
            )
        """,
        "fields": ["person_id", "subject_id", "position", "appear_eps"],
        # appear_eps 是 2025-09 新增字段，可能缺失
        "optional_fields": {"appear_eps"},
        "index_sqls": [
            "CREATE INDEX idx_subperson_subject ON subject_person(subject_id)",
            "CREATE INDEX idx_subperson_person ON subject_person(person_id)",
        ],
    },
    "person_relation": {
        "create": """
            CREATE TABLE person_relation (
                person_type TEXT,
                person_id INTEGER,
                related_person_id INTEGER,
                relation_type INTEGER,
                spoiler INTEGER,
                ended INTEGER,
                PRIMARY KEY (person_type, person_id, related_person_id, relation_type)
            )
        """,
        "fields": [
            "person_type",
            "person_id",
            "related_person_id",
            "relation_type",
            "spoiler",
            "ended",
        ],
        "optional_fields": set(),
        "index_sqls": [
            "CREATE INDEX idx_personrel_person ON person_relation(person_id)",
            "CREATE INDEX idx_personrel_related ON person_relation(related_person_id)",
        ],
    },
}

# 导入顺序（subject 先于 episode，避免外键引用问题；SQLite 默认不启用 FK，但仍按依赖顺序）
_IMPORT_ORDER = [
    "subject",
    "episode",
    "subject_relation",
    "subject_character",
    "subject_person",
    "person_relation",
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
    "subject_character": [
        "subject-characters.jsonlines",
        "subject-characters.jsonl",
        "subject_characters.jsonlines",
    ],
    "subject_person": [
        "subject-persons.jsonlines",
        "subject-persons.jsonl",
        "subject_persons.jsonlines",
    ],
    "person_relation": [
        "person-relations.jsonlines",
        "person-relations.jsonl",
        "person_relations.jsonlines",
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

            # 4. 建立索引
            if progress_cb:
                progress_cb(task_id, "indexing", 88, "建立索引")
            await asyncio.to_thread(self._create_indexes, target_db)
            if progress_cb:
                progress_cb(task_id, "indexing", 92, "索引建立完成")

            # 5. VACUUM 压缩
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
        """解压 zip 到目标目录"""
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
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
            fields = schema["fields"]
            placeholders = ",".join(["?"] * len(fields))
            col_names = ",".join(f'"{f}"' for f in fields)
            insert_sql = f"INSERT OR REPLACE INTO {table_name} ({col_names}) VALUES ({placeholders})"

            # 批量读取并插入
            count = 0
            batch: list[tuple] = []
            schema["optional_fields"]

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
                        conn.commit()
                        batch.clear()

                # 插入剩余
                if batch:
                    conn.executemany(insert_sql, batch)
                    conn.commit()
                    batch.clear()

            logger.debug(
                f"bangumi_archive: {table_name} 导入 {count} 行 (from {jsonl_path.name})"
            )
            return count
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
        finally:
            conn.close()

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
        同时清理对应的 .index 磁盘缓存，避免旧索引残留占用空间（~460MB）。
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
            # 清理对应的 .index 磁盘缓存文件
            # db_path 名为 bangumi_archive_{a|b}.db，对应缓存为 bangumi_archive_{a|b}.index
            index_path = db_path.with_suffix(".index")
            if index_path.exists():
                try:
                    index_path.unlink()
                    logger.info(f"bangumi_archive: 已清理旧索引缓存 {index_path.name}")
                except OSError as e:
                    logger.warning(
                        f"bangumi_archive: 清理索引缓存失败 {index_path}: {e}"
                    )
        except OSError as e:
            logger.warning(f"bangumi_archive: 清空数据库失败 {db_path}: {e}")
