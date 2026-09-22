"""屏蔽规则仓库（关键词黑名单，统一入口）

## 为什么合并

历史上存在**两套互不相干**的屏蔽机制，用户需要在两个地方配置、且生效时机不同：

1. ``[sync] blocked_keywords``（INI 配置）—— 标题含关键词则跳过同步，
   在**匹配前**的参数校验阶段生效。
2. ``title_blacklist`` 表（DB）—— 用户拒绝待确认候选时把该条目的
   ``subject_id`` 记入，在**匹配后**否决结果。

两者语义重叠（都是"不要同步这个"）但分散在两处，且第 2 套的 subject_id
判定要求"先匹配才知道命中谁"，导致它**永远无法提前生效**。

## 现在的设计（方案 1：统一为标题关键词判定）

合并为**单一 DB 表**，全部按**关键词**判定，全部在**匹配前**生效：

- 用户手填的关键词（原 ``blocked_keywords``）→ ``source='manual'``
- 用户拒绝待确认候选时，把该候选的**标题**记入 → ``source='reject'``

判定方式与原来的 ``blocked_keywords`` 完全一致（大小写不敏感的子串匹配），
因此生效时机自然提前到匹配前，且配置集中到 WebUI。

## 取舍说明（重要）

原 ``title_blacklist`` 按 ``subject_id`` 精确排除"同名作品中的某一部"；
改为标题关键词后，**同名作品会被一起排除**。这是方案 1 的有意取舍：
- 该表此前**记录数为 0**（从未有候选被 reject），无历史数据受影响；
- 换来的是"配置集中 + 生效提前 + 语义单一"。

## 与自定义映射的关系

自定义映射（custom_mapping）代表用户**显式指定**的意图，优先级高于黑名单：
命中自定义映射时**即使标题含屏蔽关键词也照常同步**。该优先级由调用方
（``SyncService._is_title_blocked``）通过先查映射再查黑名单实现。
"""

from __future__ import annotations

from .base_repository import BaseRepository


class BlockedRuleRepository(BaseRepository):
    """屏蔽关键词表：一行一个关键词"""

    _TABLE = "blocked_rules"

    def __init__(self, conn):
        super().__init__(conn)
        self._ensure_table()

    # ------------------------------------------------------------------
    # 表结构
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """创建表及索引（幂等）"""

        def _write(conn):
            cursor = conn.cursor()
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword_key TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    user_name TEXT NOT NULL DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(keyword_key)
                )
                """
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._TABLE}_keyword_key "
                f"ON {self._TABLE}(keyword_key)"
            )
            conn.commit()

        self._run_write(_write, error_msg="创建屏蔽关键词表失败")

    @staticmethod
    def keyword_key(keyword: str) -> str:
        """归一化键：去首尾空白 + 转小写（吸收大小写/空白差异）"""
        return (keyword or "").strip().lower()

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def add(
        self,
        keyword: str,
        source: str = "manual",
        user_name: str = "",
    ) -> bool:
        """新增一条屏蔽关键词（``INSERT OR IGNORE``，幂等）。返回是否新增。"""
        key = self.keyword_key(keyword)
        if not key:
            return False
        raw = (keyword or "").strip()

        def _write(conn):
            cursor = conn.execute(
                f"""INSERT OR IGNORE INTO {self._TABLE}
                (keyword_key, keyword, source, user_name)
                VALUES (?, ?, ?, ?)""",
                (key, raw, source or "manual", user_name or ""),
            )
            return cursor.rowcount

        result = self._run_write(_write, error_msg="新增屏蔽关键词失败", default=0)
        return bool(result)

    def bulk_add(
        self,
        keywords: list[str],
        source: str = "manual",
        user_name: str = "",
    ) -> int:
        """批量新增，返回新增条数（已存在的跳过）。"""
        keys = [
            (self.keyword_key(k), (k or "").strip())
            for k in (keywords or [])
            if self.keyword_key(k)
        ]
        if not keys:
            return 0

        def _write(conn):
            count = 0
            for key, raw in keys:
                cursor = conn.execute(
                    f"""INSERT OR IGNORE INTO {self._TABLE}
                    (keyword_key, keyword, source, user_name)
                    VALUES (?, ?, ?, ?)""",
                    (key, raw, source or "manual", user_name or ""),
                )
                count += cursor.rowcount
            return count

        result = self._run_write(_write, error_msg="批量新增屏蔽关键词失败", default=0)
        return int(result or 0)

    def remove(self, keyword: str) -> bool:
        """按关键词删除（归一化后匹配）"""
        key = self.keyword_key(keyword)
        if not key:
            return False

        def _write(conn):
            cursor = conn.execute(
                f"DELETE FROM {self._TABLE} WHERE keyword_key = ?", (key,)
            )
            return cursor.rowcount > 0

        return bool(
            self._run_write(_write, error_msg="删除屏蔽关键词失败", default=False)
        )

    def clear(self) -> int:
        """清空全部（UI 清理入口），返回删除条数"""

        def _write(conn):
            cursor = conn.execute(f"DELETE FROM {self._TABLE}")
            return cursor.rowcount

        result = self._run_write(_write, error_msg="清空屏蔽关键词失败", default=0)
        return int(result or 0)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def list_all(self) -> list[dict]:
        """列出全部规则（按创建时间倒序），供管理页展示"""

        def _read(conn):
            cursor = conn.execute(
                f"SELECT id, keyword, source, user_name, created_at "
                f"FROM {self._TABLE} ORDER BY id DESC"
            )
            return [
                {
                    "id": row[0],
                    "keyword": row[1],
                    "source": row[2],
                    "user_name": row[3],
                    "created_at": row[4],
                }
                for row in cursor.fetchall()
            ]

        result = self._run_read(_read, error_msg="读取屏蔽关键词失败", default=[])
        return result if isinstance(result, list) else []

    def list_keywords(self) -> list[str]:
        """只取关键词列表（判定热路径用，避免构造 dict 开销）"""

        def _read(conn):
            cursor = conn.execute(f"SELECT keyword FROM {self._TABLE} ORDER BY id")
            return [row[0] for row in cursor.fetchall()]

        result = self._run_read(_read, error_msg="读取屏蔽关键词失败", default=[])
        return result if isinstance(result, list) else []

    def count(self) -> int:
        def _read(conn):
            cursor = conn.execute(f"SELECT COUNT(*) FROM {self._TABLE}")
            return cursor.fetchone()[0]

        return int(
            self._run_read(_read, error_msg="统计屏蔽关键词失败", default=0) or 0
        )

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    def match_title(self, *titles: str) -> str:
        """返回第一个命中标题的关键词（无命中返回空串）

        判定语义与历史 ``blocked_keywords`` 完全一致：**大小写不敏感的子串匹配**。
        传入多个标题（如 title / ori_title）时任一命中即算命中。

        热路径：关键词数量通常很小（个位数），直接遍历即可；
        若将来数量增长，可改为 Aho-Corasick 或建 FTS 索引。
        """
        keywords = self.list_keywords()
        if not keywords:
            return ""
        for title in titles:
            if not title:
                continue
            low = str(title).lower()
            for kw in keywords:
                if kw and kw.lower() in low:
                    return kw
        return ""

    # ------------------------------------------------------------------
    # 迁移
    # ------------------------------------------------------------------

    def migrate_from_config_keywords(self, raw: str) -> int:
        """把历史 ``[sync] blocked_keywords`` 的逗号串导入本表（幂等）。

        仅在**表为空**时执行，避免把用户已删除的关键词又加回来。

        Args:
            raw: 逗号分隔的关键词串（支持中英文逗号）

        Returns:
            导入条数（表非空或无需导入时返回 0）
        """
        if self.count() > 0:
            return 0
        if not raw or not raw.strip():
            return 0
        parts = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
        if not parts:
            return 0
        return self.bulk_add(parts, source="manual")
