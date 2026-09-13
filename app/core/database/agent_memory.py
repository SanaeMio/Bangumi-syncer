"""Agent 工作记忆仓库（定时任务执行摘要）。

主表（带 FTS）= 热记忆：写入（store_and_mark）、检索（get_recent/search_fts）、
prune 降级到归档表（冷记忆，search_archive 用 LIKE 检索）。
"""

from __future__ import annotations

from app.models.memory import MemoryEntry

from .base_repository import BaseRepository

_MAIN_COLS = "id, task_type, task_id, run_id, summary, full_text, outcome, tokens_used, created_at"


class AgentMemoryRepository(BaseRepository):
    """记忆仓库：store_and_mark / prune / get_recent / search_fts / search_archive。"""

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def store_and_mark(self, entry: MemoryEntry, record_ids: list[int]) -> int:
        """同一事务：INSERT 记忆（entry）+ 关联表写入消费标记
        （sync_records_consumed：每条记录一行 (sync_record_id, run_id)）。

        run 的原子单元——"记忆记录 + 记录消费"要么全成功要么全回滚，
        消除"记忆已写但标记未写"的中间态。消费标记是记忆域数据，故在同一
        事务内直连关联表执行。多对多：一条记录可被多个任务的 run 消费，
        互不覆盖（INSERT OR IGNORE 幂等，同 run 不重复记）。
        """

        def _write(conn):
            cur = conn.execute(
                """
                INSERT INTO agent_working_memory
                (task_type, task_id, run_id, summary, full_text, outcome, tokens_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.task_type,
                    entry.task_id,
                    entry.run_id,
                    entry.summary,
                    entry.full_text,
                    entry.outcome,
                    entry.tokens_used,
                ),
            )
            n1 = cur.rowcount
            n2 = 0
            if record_ids:
                # 多对多：INSERT OR IGNORE——记录已存在 (record, run) 时不重复
                cur = conn.executemany(
                    "INSERT OR IGNORE INTO sync_records_consumed "
                    "(sync_record_id, run_id) VALUES (?, ?)",
                    [(rid, entry.run_id) for rid in record_ids],
                )
                n2 = cur.rowcount
            return n1 + n2

        return self._run_write(_write, error_msg="写入任务记忆失败", reraise=True)

    def prune(self, task_type: str, task_id: str, keep: int = 1000) -> int:
        """独立 best-effort 事务：超出 keep 的旧记录先 INSERT INTO archive，
        再 DELETE 主表（触发器同步删 FTS 索引）。默认 1000 条/任务。

        与 store_and_mark 不同事务：prune 是维护性操作，失败只导致表不清理
        （下次 run 重试），不应回滚一次已成功且已消耗 LLM token 的总结 run。
        """

        def _write(conn):
            cursor = conn.execute(
                """
                SELECT id FROM agent_working_memory
                WHERE task_type = ? AND task_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT -1 OFFSET ?
                """,
                (task_type, task_id, keep),
            )
            old_ids = [row[0] for row in cursor.fetchall()]
            if not old_ids:
                return 0
            placeholders = ",".join("?" * len(old_ids))
            params = [task_type, task_id, *old_ids]
            cur = conn.execute(
                f"""
                INSERT INTO agent_working_memory_archive
                (task_type, task_id, run_id, summary, full_text, outcome, tokens_used, created_at)
                SELECT task_type, task_id, run_id, summary, full_text, outcome, tokens_used, created_at
                FROM agent_working_memory
                WHERE task_type = ? AND task_id = ? AND id IN ({placeholders})
                """,
                params,
            )
            n_archived = cur.rowcount
            cur = conn.execute(
                f"""
                DELETE FROM agent_working_memory
                WHERE task_type = ? AND task_id = ? AND id IN ({placeholders})
                """,
                params,
            )
            return n_archived + cur.rowcount

        return self._run_write(_write, error_msg="清理任务记忆失败", default=0)

    # ------------------------------------------------------------------
    # 清理与重置（Phase 2.0.3）
    # ------------------------------------------------------------------

    def rename_task(self, task_type: str, old_task_id: str, new_task_id: str) -> int:
        """改名迁移：同一事务 UPDATE 主表 + 归档表的 task_id（记忆跟随任务）。

        消费标记无需迁移（关联表只关联 sync_record_id + run_id，不依赖 task_id）。
        """

        def _write(conn):
            n1 = conn.execute(
                "UPDATE agent_working_memory SET task_id = ? "
                "WHERE task_type = ? AND task_id = ?",
                (new_task_id, task_type, old_task_id),
            ).rowcount
            n2 = conn.execute(
                "UPDATE agent_working_memory_archive SET task_id = ? "
                "WHERE task_type = ? AND task_id = ?",
                (new_task_id, task_type, old_task_id),
            ).rowcount
            return n1 + n2

        return self._run_write(_write, error_msg="迁移任务记忆失败", default=0)

    def clear_task(self, task_type: str, task_id: str) -> int:
        """同一事务清空该 task 的记忆 + 消费标记。

        顺序敏感：① 先收集 run_id（主表 + 归档表 UNION）——必须在删表前取，
        否则 run_id→task_id 映射丢失；② 删主表；③ 删归档表；
        ④ 清关联表中 run_id ∈ 本任务 run_ids 的消费标记（DELETE 关联表行，
        天然只影响本任务——其他任务消费同一记录的标记保留，跨任务隔离）。
        不筛 outcome，全部删除（想保留偏好重新开始的场景用"复制新 job"）。
        """

        def _write(conn):
            main_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT run_id FROM agent_working_memory "
                    "WHERE task_type = ? AND task_id = ?",
                    (task_type, task_id),
                )
            ]
            arch_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT run_id FROM agent_working_memory_archive "
                    "WHERE task_type = ? AND task_id = ?",
                    (task_type, task_id),
                )
            ]
            run_ids = set(main_ids) | set(arch_ids)

            n1 = conn.execute(
                "DELETE FROM agent_working_memory WHERE task_type = ? AND task_id = ?",
                (task_type, task_id),
            ).rowcount
            n2 = conn.execute(
                "DELETE FROM agent_working_memory_archive "
                "WHERE task_type = ? AND task_id = ?",
                (task_type, task_id),
            ).rowcount

            n3 = 0
            if run_ids:
                placeholders = ",".join("?" * len(run_ids))
                n3 = conn.execute(
                    "DELETE FROM sync_records_consumed "
                    f"WHERE run_id IN ({placeholders})",
                    tuple(run_ids),
                ).rowcount
            return n1 + n2 + n3

        return self._run_write(_write, error_msg="清空任务记忆失败", default=0)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_recent(
        self, task_type: str, task_id: str, limit: int = 5
    ) -> list[MemoryEntry]:
        """按 task 取最近 N 条记忆（created_at DESC, id DESC 保证同秒内稳定排序）。"""

        def _read(conn):
            cursor = conn.execute(
                f"""
                SELECT {_MAIN_COLS} FROM agent_working_memory
                WHERE task_type = ? AND task_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (task_type, task_id, limit),
            )
            return [MemoryEntry.from_row(row) for row in cursor.fetchall()]

        return self._run_read(_read, error_msg="获取最近任务记忆失败", default=[])

    def get_task_run_ids(self, task_type: str, task_id: str) -> set[str]:
        """按任务取本任务全部 run_id 集合（主表 + 归档 UNION）。

        供消费排除按任务隔离：判断记录在关联表中的 consumed_run_ids 是否属于
        「当前任务」——有交集则剔除（同任务去重），无交集则保留（跨任务互斥解除，
        如每日总结消费后年度总结仍可消费）。prune 下沉归档后 run_id 仍归属
        本任务（消费标记不随归档失效），故必须 UNION 归档表。
        """

        def _read(conn):
            rows = conn.execute(
                """
                SELECT run_id FROM agent_working_memory
                WHERE task_type = ? AND task_id = ?
                UNION
                SELECT run_id FROM agent_working_memory_archive
                WHERE task_type = ? AND task_id = ?
                """,
                (task_type, task_id, task_type, task_id),
            ).fetchall()
            return {row[0] for row in rows}

        return self._run_read(
            _read, error_msg="获取任务 run_id 集合失败", default=set()
        )

    def get_related_titles(
        self,
        task_type: str,
        task_id: str,
        titles: list[str],
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """同剧关联：按今日记录的剧名反查历史总结（日期倒序最近 N 条）。

        机制：sync_records_consumed 关联表是"该记录被哪次总结消费过"的显式
        关联——以今日 bgm_title 集合为键，经关联表 JOIN 主表 + 归档表 UNION，
        即"同类剧目的历史总结"（含已归档冷层）。比 FTS 子串猜测更精确，
        且自然覆盖冷层（消费标记不随 prune 清理）。

        边界：titles 为空/全空 → 短路返回 []；同剧多集被同一次总结消费 →
        GROUP BY m.id 去重；主表/归档表同 run_id 不可能共存（run_id UNIQUE，
        prune 搬家）→ UNION 天然去重；排序 = get_recent 同款稳定序；
        性能：bgm_title 暂无索引，全表扫 JOIN（10 万行内毫秒级），
        超量预案见 closeout §3 性能注记。
        """
        clean = [t for t in titles if t and t.strip()]
        if not clean:
            return []
        placeholders = ",".join("?" * len(clean))

        def _read(conn):
            cursor = conn.execute(
                f"""
                SELECT m.id, m.task_type, m.task_id, m.run_id, m.summary,
                       m.full_text, m.outcome, m.tokens_used, m.created_at
                FROM agent_working_memory m
                JOIN sync_records_consumed c ON c.run_id = m.run_id
                JOIN sync_records s ON s.id = c.sync_record_id
                WHERE s.bgm_title IN ({placeholders})
                  AND m.task_type = ? AND m.task_id = ?
                GROUP BY m.id
                UNION
                SELECT m.id, m.task_type, m.task_id, m.run_id, m.summary,
                       m.full_text, m.outcome, m.tokens_used, m.created_at
                FROM agent_working_memory_archive m
                JOIN sync_records_consumed c ON c.run_id = m.run_id
                JOIN sync_records s ON s.id = c.sync_record_id
                WHERE s.bgm_title IN ({placeholders})
                  AND m.task_type = ? AND m.task_id = ?
                GROUP BY m.id
                -- UNION 结果集列名取自首个 SELECT，排序用序号避免列名歧义
                ORDER BY 9 DESC, 1 DESC
                LIMIT ?
                """,
                (*clean, task_type, task_id, *clean, task_type, task_id, limit),
            )
            return [MemoryEntry.from_row(row) for row in cursor.fetchall()]

        return self._run_read(_read, error_msg="获取同剧关联记忆失败", default=[])

    def search_fts(
        self, terms: list[str], task_type: str, limit: int = 5
    ) -> list[MemoryEntry]:
        """[deprecated] FTS5 全文检索（热记忆），按 task_type 过滤。

        **已停用**：相关回忆由 get_related_titles（联表反查）承担——FTS 搜索的
        输入（标题词）与联表相同，而联表经关联表（sync_records_consumed）显式关联更精确、
        且覆盖归档冷层。物理结构（虚表/触发器/索引）保留，供 Phase 5
        混合检索（FTS5 + 向量，RRF 融合）复用；无业务调用方。

        多关键词 OR 连接（任一命中即相关——关键词是今日明细标题，目的
        是捞回与任一标题相关的历史记忆）；**每个关键词作为一个整体短语**
        （保留多词标题如 "Spy x Family" 的完整匹配，不再按空白二次切分）；
        中文子串匹配依赖 trigram tokenizer（SQLite >= 3.34）；查询词短语
        引号包裹避免特殊字符破坏 MATCH 语法；短词（<3 字符，trigram
        无法命中）过滤。
        """

        def _read(conn):
            phrases = [
                '"' + t.strip().replace('"', '""') + '"'
                for t in terms
                if len(t.strip()) >= 3
            ]
            if not phrases:
                return []
            match = " OR ".join(phrases)
            cursor = conn.execute(
                """
                SELECT m.id, m.task_type, m.task_id, m.run_id, m.summary,
                       m.full_text, m.outcome, m.tokens_used, m.created_at
                FROM agent_memory_fts f
                JOIN agent_working_memory m ON m.id = f.rowid
                WHERE agent_memory_fts MATCH ? AND m.task_type = ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, task_type, limit),
            )
            return [MemoryEntry.from_row(row) for row in cursor.fetchall()]

        return self._run_read(_read, error_msg="检索任务记忆失败", default=[])

    def search_archive(
        self, task_type: str | None, keywords: str, limit: int = 50
    ) -> list[MemoryEntry]:
        """冷记忆检索（LIKE，无 FTS）——Phase 3 失败定位等查全量历史用。"""

        def _read(conn):
            like = f"%{keywords}%"
            type_clause = "AND task_type = ?" if task_type else ""
            args = [like, like] + ([task_type] if task_type else []) + [limit]
            cursor = conn.execute(
                f"""
                SELECT {_MAIN_COLS} FROM agent_working_memory_archive
                WHERE (summary LIKE ? OR full_text LIKE ?) {type_clause}
                ORDER BY id DESC
                LIMIT ?
                """,
                args,
            )
            return [MemoryEntry.from_row(row) for row in cursor.fetchall()]

        return self._run_read(_read, error_msg="检索归档记忆失败", default=[])
