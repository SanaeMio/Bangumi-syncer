"""
LLM 用量日志仓库
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base_repository import BaseRepository


@dataclass
class ModelStats:
    """按模型的用量统计。"""

    model: str = ""
    calls: int = 0
    total_tokens: int = 0
    avg_latency_ms: int = 0


@dataclass
class JobStats:
    """按任务的用量统计。"""

    job_name: str = ""
    calls: int = 0
    total_tokens: int = 0
    avg_latency_ms: int = 0


@dataclass
class DailyStats:
    """按天的用量统计。"""

    date: str = ""
    calls: int = 0
    total_tokens: int = 0


@dataclass
class LLMUsageStats:
    """LLM 用量统计汇总。"""

    total_calls: int = 0
    total_tokens: int = 0
    error_count: int = 0
    avg_latency_ms: int = 0
    by_model: list[ModelStats] = field(default_factory=list)
    by_job: list[JobStats] = field(default_factory=list)
    daily: list[DailyStats] = field(default_factory=list)


class LLMUsageRepository(BaseRepository):
    """LLM API 调用用量日志的记录与统计。"""

    _TABLE = "llm_usage_logs"
    _DEFAULT_RETENTION_DAYS = 365

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
    # stats helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _days_filter(days: int) -> tuple[str, list[Any]]:
        """根据 days 参数生成 WHERE 子句和对应参数列表。"""
        if days > 0:
            return (
                " WHERE timestamp >= datetime('now', ? || ' days')",
                [f"-{days}"],
            )
        elif days == 0:
            return (" WHERE 1=0", [])
        return ("", [])

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------

    def get_stats(self, scope: str = "aggregate", days: int = 30) -> LLMUsageStats:
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
            where, params = self._days_filter(days)
            cursor.execute(base_sql + where, params)

            row = cursor.fetchone()
            if row is None:
                return LLMUsageStats()

            result = LLMUsageStats(
                total_calls=row[0] or 0,
                total_tokens=row[1] or 0,
                error_count=row[2] or 0,
                avg_latency_ms=int(row[3] or 0),
            )

            if scope == "detailed":
                result.by_model = self._stats_by_model(cursor, days)
                result.by_job = self._stats_by_job(cursor, days)
                result.daily = self._stats_daily(cursor, days)

            return result

        return self._run_read(_read, error_msg="获取 LLM 用量统计失败", reraise=True)

    def _stats_by_model(self, cursor, days: int) -> list[ModelStats]:
        sql = f"""
            SELECT model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
            FROM {self._TABLE}
        """
        where, params = self._days_filter(days)
        sql += where + " GROUP BY model ORDER BY total_tokens DESC"
        cursor.execute(sql, params)
        return [
            ModelStats(
                model=r[0],
                calls=r[1],
                total_tokens=r[2],
                avg_latency_ms=int(r[3] or 0),
            )
            for r in cursor.fetchall()
        ]

    def _stats_by_job(self, cursor, days: int) -> list[JobStats]:
        sql = f"""
            SELECT COALESCE(job_name, '(unknown)') AS job_name,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
            FROM {self._TABLE}
        """
        where, params = self._days_filter(days)
        sql += where + " GROUP BY job_name ORDER BY total_tokens DESC"
        cursor.execute(sql, params)
        return [
            JobStats(
                job_name=r[0],
                calls=r[1],
                total_tokens=r[2],
                avg_latency_ms=int(r[3] or 0),
            )
            for r in cursor.fetchall()
        ]

    def _stats_daily(self, cursor, days: int) -> list[DailyStats]:
        sql = f"""
            SELECT DATE(timestamp) AS date,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM {self._TABLE}
        """
        where, params = self._days_filter(days)
        sql += where + " GROUP BY DATE(timestamp) ORDER BY date"
        cursor.execute(sql, params)
        return [
            DailyStats(date=r[0], calls=r[1], total_tokens=r[2])
            for r in cursor.fetchall()
        ]

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------

    def cleanup_old(self, retention_days: int = _DEFAULT_RETENTION_DAYS) -> int:
        """删除超过 ``retention_days`` 天的记录，返回删除条数。"""
        if retention_days <= 0:
            retention_days = self._DEFAULT_RETENTION_DAYS

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
