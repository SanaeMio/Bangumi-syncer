"""
Tests for LLMUsageRepository
"""

import sqlite3
from unittest.mock import patch


def _approx(value, expected, tolerance=10):
    """Assert *value* is within *tolerance* of *expected*."""
    return abs(value - expected) <= tolerance


class TestLLMUsageRepository:
    """Tests for the LLMUsageRepository (table, log_usage, get_stats, cleanup)."""

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_db(temp_dir):
        """Create a DatabaseManager pointed at a temp file and return it."""
        db_path = temp_dir / "llm.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
        return db

    @staticmethod
    def _raw_conn(temp_dir):
        return sqlite3.connect(str(temp_dir / "llm.db"))

    # ── table creation ───────────────────────────────────────────────

    def test_table_created_on_init(self, temp_dir, reset_singletons):
        _ = self._make_db(temp_dir)

        with self._raw_conn(temp_dir) as raw:
            cursor = raw.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='llm_usage_logs'"
            )
            assert cursor.fetchone() is not None

    def test_table_has_expected_columns(self, temp_dir, reset_singletons):
        _ = self._make_db(temp_dir)

        with self._raw_conn(temp_dir) as raw:
            cursor = raw.execute("PRAGMA table_info(llm_usage_logs)")
            rows = cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id",
            "timestamp",
            "job_id",
            "job_name",
            "model",
            "provider",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "latency_ms",
            "status",
            "error_message",
        }
        assert col_names == expected

    def test_indices_created(self, temp_dir, reset_singletons):
        _ = self._make_db(temp_dir)

        with self._raw_conn(temp_dir) as raw:
            for idx_name in (
                "idx_llm_usage_logs_timestamp",
                "idx_llm_usage_logs_job_id",
            ):
                cursor = raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                    (idx_name,),
                )
                assert cursor.fetchone() is not None, f"Index {idx_name} not found"

    def test_table_idempotent(self, temp_dir, reset_singletons):
        """Calling _ensure_table twice does not raise."""
        db = self._make_db(temp_dir)
        # Trigger again by calling log_usage (which calls _ensure_table)
        for _ in range(3):
            assert db.llm_usage.log_usage(model="gpt-4", total_tokens=10)

    # ── log_usage ────────────────────────────────────────────────────

    def test_log_usage_minimal(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        assert db.llm_usage.log_usage(model="gpt-4")

        with self._raw_conn(temp_dir) as raw:
            row = raw.execute(
                "SELECT id, model, provider, status FROM llm_usage_logs"
            ).fetchone()
        assert row[1] == "gpt-4"
        assert row[2] == "openai_compat"
        assert row[3] == "success"

    def test_log_usage_full(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        assert db.llm_usage.log_usage(
            job_id=42,
            job_name="summary",
            model="claude-3",
            provider="anthropic",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=320,
            status="error",
            error_message="timeout",
        )

        with self._raw_conn(temp_dir) as raw:
            row = raw.execute(
                "SELECT job_id, job_name, model, provider, "
                "prompt_tokens, completion_tokens, total_tokens, "
                "latency_ms, status, error_message "
                "FROM llm_usage_logs"
            ).fetchone()
        assert row == (
            42,
            "summary",
            "claude-3",
            "anthropic",
            100,
            50,
            150,
            320,
            "error",
            "timeout",
        )

    def test_log_usage_returns_false_on_error(self, temp_dir, reset_singletons):
        """Force a DB error and verify log_usage returns False."""
        db = self._make_db(temp_dir)
        # Simulate connection drop so the next operation fails.
        db._conn = None
        with patch(
            "app.core.database.sqlite3.connect", side_effect=OSError("disk full")
        ):
            result = db.llm_usage.log_usage(model="test")
        assert result is False

    # ── get_stats: aggregate ─────────────────────────────────────────

    def test_get_stats_aggregate(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        repo = db.llm_usage

        repo.log_usage(model="a", total_tokens=100, latency_ms=50, status="success")
        repo.log_usage(model="b", total_tokens=200, latency_ms=150, status="success")
        repo.log_usage(
            model="a",
            total_tokens=300,
            latency_ms=200,
            status="error",
            error_message="oops",
        )

        stats = repo.get_stats(scope="aggregate", days=30)
        assert stats.total_calls == 3
        assert stats.total_tokens == 600
        assert stats.error_count == 1
        assert _approx(stats.avg_latency_ms, 400 / 3)

    def test_get_stats_aggregate_empty(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        stats = db.llm_usage.get_stats(scope="aggregate", days=30)
        assert stats.total_calls == 0
        assert stats.total_tokens == 0
        assert stats.error_count == 0
        assert stats.avg_latency_ms == 0

    # ── get_stats: detailed ──────────────────────────────────────────

    def test_get_stats_detailed_includes_by_model(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        repo = db.llm_usage

        repo.log_usage(model="m1", total_tokens=100, status="success")
        repo.log_usage(model="m1", total_tokens=200, status="success")
        repo.log_usage(model="m2", total_tokens=50, status="success")

        stats = repo.get_stats(scope="detailed", days=30)
        by_model = {m.model: m for m in stats.by_model}
        assert by_model["m1"].calls == 2
        assert by_model["m1"].total_tokens == 300
        assert by_model["m2"].calls == 1
        assert by_model["m2"].total_tokens == 50

    def test_get_stats_detailed_includes_by_job(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        repo = db.llm_usage

        repo.log_usage(
            job_name="summary", model="m", total_tokens=100, status="success"
        )
        repo.log_usage(
            job_name="summary", model="m", total_tokens=200, status="success"
        )
        repo.log_usage(job_name="tagging", model="m", total_tokens=50, status="success")

        stats = repo.get_stats(scope="detailed", days=30)
        by_job = {j.job_name: j for j in stats.by_job}
        assert by_job["summary"].calls == 2
        assert by_job["summary"].total_tokens == 300
        assert by_job["tagging"].calls == 1

    def test_get_stats_detailed_includes_daily(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        repo = db.llm_usage

        repo.log_usage(model="m", total_tokens=100, status="success")
        repo.log_usage(model="m", total_tokens=200, status="success")

        stats = repo.get_stats(scope="detailed", days=30)
        assert len(stats.daily) >= 1
        today_entry = stats.daily[-1]
        assert today_entry.calls >= 2
        assert today_entry.total_tokens >= 300

    def test_get_stats_detailed_respects_days(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        repo = db.llm_usage

        repo.log_usage(model="m", total_tokens=1, status="success")

        # Should not return data when days=0 (excludes all)
        stats = repo.get_stats(scope="detailed", days=0)
        assert stats.total_calls == 0

    def test_get_stats_scope_defaults_to_aggregate(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        repo = db.llm_usage
        repo.log_usage(model="m", total_tokens=100, status="success")
        stats = repo.get_stats()  # default scope
        assert not stats.by_model  # aggregate 不返回明细
        assert stats.total_calls == 1

    # ── cleanup ──────────────────────────────────────────────────────

    def test_cleanup_deletes_old_records(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        repo = db.llm_usage

        # Insert a record with a known old timestamp.
        with self._raw_conn(temp_dir) as raw:
            raw.execute(
                """INSERT INTO llm_usage_logs
                (timestamp, model, provider, prompt_tokens,
                 completion_tokens, total_tokens, latency_ms, status)
                VALUES (datetime('now', '-60 days'), 'old', 'oai',
                        0, 0, 1, 0, 'success')"""
            )
            raw.execute(
                """INSERT INTO llm_usage_logs
                (timestamp, model, provider, prompt_tokens,
                 completion_tokens, total_tokens, latency_ms, status)
                VALUES (datetime('now', '-10 days'), 'recent', 'oai',
                        0, 0, 1, 0, 'success')"""
            )
            raw.commit()

        deleted = repo.cleanup_old(retention_days=30)
        assert deleted == 1

        with self._raw_conn(temp_dir) as raw:
            rows = raw.execute("SELECT model FROM llm_usage_logs").fetchall()
        models = {r[0] for r in rows}
        assert "old" not in models
        assert "recent" in models

    def test_cleanup_returns_zero_when_nothing_to_delete(
        self, temp_dir, reset_singletons
    ):
        db = self._make_db(temp_dir)
        repo = db.llm_usage
        repo.log_usage(model="m", total_tokens=1, status="success")
        deleted = repo.cleanup_old(retention_days=90)
        assert deleted == 0
        assert repo.get_stats().total_calls == 1

    def test_cleanup_uses_default_when_negative(self, temp_dir, reset_singletons):
        """retention_days <= 0 时回退到默认值 365。"""
        db = self._make_db(temp_dir)
        repo = db.llm_usage
        # 不应抛出异常
        deleted = repo.cleanup_old(retention_days=0)
        assert deleted == 0

    # ── default retention days ──────────────────────────────────────

    def test_default_retention_days_is_365(self):
        from app.core.database.llm_usage import LLMUsageRepository

        assert LLMUsageRepository._DEFAULT_RETENTION_DAYS == 365

    # ── DatabaseManager wiring ───────────────────────────────────────

    def test_database_manager_has_llm_usage_attribute(self, temp_dir, reset_singletons):
        db = self._make_db(temp_dir)
        assert hasattr(db, "llm_usage")
        from app.core.database.llm_usage import LLMUsageRepository

        assert isinstance(db.llm_usage, LLMUsageRepository)

    def test_llm_usage_accessible_after_reconnect(self, temp_dir, reset_singletons):
        """log_usage still works after _conn is reset (simulated reconnect)."""
        db = self._make_db(temp_dir)
        db._conn = None  # simulate connection drop
        assert db.llm_usage.log_usage(model="reconnect-test", total_tokens=5)
        assert db.llm_usage.get_stats().total_calls == 1
