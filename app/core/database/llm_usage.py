"""
LLM 用量日志仓库
"""

from __future__ import annotations

from typing import Any

from .base_repository import BaseRepository


class LLMUsageRepository(BaseRepository):
    """LLM API 调用用量日志的记录与统计。"""

    _TABLE = "llm_usage_logs"

    def __init__(self, conn):
        super().__init__(conn)
        self._ensure_table()

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """创建表及索引（幂等）。"""

        def _write(conn):
            cursor = conn.cursor()
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    job_id INTEGER,
                    job_name TEXT,
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'openai_compat',
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    latency_ms INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'success',
                    error_message TEXT
                )
            """)
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._TABLE}_timestamp "
                f"ON {self._TABLE}(timestamp)"
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._TABLE}_job_id "
                f"ON {self._TABLE}(job_id)"
            )
            conn.commit()

        self._run_write(_write, error_msg="创建 LLM 用量表失败")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def log_usage(
        self,
        job_id: int | None = None,
        job_name: str | None = None,
        model: str = "",
        provider: str = "openai_compat",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
        status: str = "success",
        error_message: str | None = None,
    ) -> bool:
        """插入一条用量记录。成功返回 ``True``，失败返回 ``False``。"""
        self._ensure_table()

        def _write(conn):
            cursor = conn.execute(
                f"""INSERT INTO {self._TABLE}
                (job_id, job_name, model, provider,
                 prompt_tokens, completion_tokens, total_tokens,
                 latency_ms, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    job_name,
                    model,
                    provider,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    latency_ms,
                    status,
                    error_message,
                ),
            )
            return cursor.rowcount

        result = self._run_write(
            _write,
            error_msg="记录 LLM 用量失败",
            default=False,
        )
        return bool(result)

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    # review 响应体应该使用对象，而不是使用 dict，下面的函数同样需要修改。
    def get_stats(self, scope: str = "aggregate", days: int = 30) -> dict[str, Any]:
        """获取用量统计。

        Args:
            scope:  ``"aggregate"`` 仅返回总计；``"detailed"`` 额外包含按模型、
                    按任务及按天的明细。
            days:   统计最近多少天的数据。
        """

        def _read(conn):
            cursor = conn.cursor()
            base_sql = f"""
                SELECT
                    COUNT(*)                       AS total_calls,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(CASE WHEN status = 'error'
                                 THEN 1 ELSE 0 END), 0) AS error_count,
                    COALESCE(AVG(latency_ms), 0)   AS avg_latency_ms
                FROM {self._TABLE}
            """
            # review 提取 sql 语句添加 days 的代码，抽象为函数
            if days > 0:
                base_sql += " WHERE timestamp >= datetime('now', ? || ' days')"
                cursor.execute(base_sql, (f"-{days}",))
            elif days == 0:
                base_sql += " WHERE 1=0"
                cursor.execute(base_sql)
            else:
                cursor.execute(base_sql)

            row = cursor.fetchone()
            # review row 可能为 None 或者 tuple，需要加上检查，否则在 base_sql 为 1=0 时，可能会报错
            result: dict[str, Any] = {
                "total_calls": row[0] or 0,
                "total_tokens": row[1] or 0,
                "error_count": row[2] or 0,
                "avg_latency_ms": int(row[3] or 0),
            }

            if scope == "detailed":
                result["by_model"] = self._stats_by_model(cursor, days)
                result["by_job"] = self._stats_by_job(cursor, days)
                result["daily"] = self._stats_daily(cursor, days)

            return result

        return self._run_read(_read, error_msg="获取 LLM 用量统计失败", reraise=True)

    def _stats_by_model(self, cursor, days: int) -> list[dict[str, Any]]:
        sql = f"""
            SELECT model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
            FROM {self._TABLE}
        """
        params: list[Any] = []
        if days > 0:
            sql += " WHERE timestamp >= datetime('now', ? || ' days')"
            params.append(f"-{days}")
        elif days == 0:
            sql += " WHERE 1=0"
        sql += " GROUP BY model ORDER BY total_tokens DESC"
        cursor.execute(sql, params)
        return [
            {
                "model": r[0],
                "calls": r[1],
                "total_tokens": r[2],
                "avg_latency_ms": int(r[3] or 0),
            }
            for r in cursor.fetchall()
        ]

    def _stats_by_job(self, cursor, days: int) -> list[dict[str, Any]]:
        sql = f"""
            SELECT COALESCE(job_name, '(unknown)') AS job_name,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
            FROM {self._TABLE}
        """
        params: list[Any] = []
        if days > 0:
            sql += " WHERE timestamp >= datetime('now', ? || ' days')"
            params.append(f"-{days}")
        elif days == 0:
            sql += " WHERE 1=0"
        sql += " GROUP BY job_name ORDER BY total_tokens DESC"
        cursor.execute(sql, params)
        return [
            {
                "job_name": r[0],
                "calls": r[1],
                "total_tokens": r[2],
                "avg_latency_ms": int(r[3] or 0),
            }
            for r in cursor.fetchall()
        ]

    def _stats_daily(self, cursor, days: int) -> list[dict[str, Any]]:
        sql = f"""
            SELECT DATE(timestamp) AS date,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM {self._TABLE}
        """
        params: list[Any] = []
        if days > 0:
            sql += " WHERE timestamp >= datetime('now', ? || ' days')"
            params.append(f"-{days}")
        elif days == 0:
            sql += " WHERE 1=0"
        sql += " GROUP BY DATE(timestamp) ORDER BY date"
        cursor.execute(sql, params)
        return [
            {"date": r[0], "calls": r[1], "total_tokens": r[2]}
            for r in cursor.fetchall()
        ]

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------
    # review retention_days 应该作为参数暴露出去，有两种方式，一种 llm_param，另外一种环境变量，考虑下哪种更好？
    def cleanup_old(self, retention_days: int = 30) -> int:
        """删除超过 ``retention_days`` 天的记录，返回删除条数。"""

        def _write(conn):
            cursor = conn.execute(
                f"""DELETE FROM {self._TABLE}
                WHERE timestamp < datetime('now', ? || ' days')""",
                (f"-{retention_days}",),
            )
            conn.commit()
            return cursor.rowcount

        result = self._run_write(
            _write,
            error_msg="清理旧 LLM 用量记录失败",
            default=0,
        )
        return result if isinstance(result, int) else 0
