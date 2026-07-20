"""
Tests for SyncRecordsRepository.get_records_in_date_range
"""

import sqlite3
from unittest.mock import patch

# ── helpers ──────────────────────────────────────────────────────────────


def _insert_record(
    conn,
    timestamp,
    user_name="test_user",
    title="Test",
    source="custom",
    status="success",
):
    conn.execute(
        """INSERT INTO sync_records
        (timestamp, user_name, title, ori_title, season, episode,
         subject_id, episode_id, status, message, source, media_type, bgm_title)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            timestamp,
            user_name,
            title,
            None,
            1,
            1,
            None,
            None,
            status,
            "",
            source,
            "episode",
            "",
        ),
    )


class TestGetRecordsInDateRange:
    """Tests for the get_records_in_date_range method."""

    # ── basic date filtering ──────────────────────────────────────────

    def test_date_range_basic_inclusive_from_exclusive_to(
        self, temp_dir, reset_singletons
    ):
        """Records from date_from through the full day of date_to."""
        db_path = temp_dir / "rd.db"

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            _insert_record(raw, "2024-06-01 12:00:00", title="T1")
            _insert_record(raw, "2024-06-03 00:00:00", title="T3")
            _insert_record(raw, "2024-06-03 23:59:59", title="T3-late")
            _insert_record(raw, "2024-06-05 00:00:00", title="T5")
            _insert_record(raw, "2024-06-05 12:00:00", title="T5-noon")
            _insert_record(raw, "2024-06-06 00:00:00", title="T6")
            raw.commit()

        records = db.get_records_in_date_range("2024-06-03", "2024-06-05")
        titles = [r["title"] for r in records]
        # T3 (start of date_from), T3-late, T5 (midnight of date_to),
        # T5-noon -- all should be included since date_to is inclusive.
        assert len(records) == 4
        assert "T3" in titles
        assert "T3-late" in titles
        assert "T5" in titles
        assert "T5-noon" in titles
        assert "T1" not in titles
        assert "T6" not in titles

    def test_date_range_empty_when_no_matches(self, temp_dir, reset_singletons):
        db_path = temp_dir / "empty_range.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            _insert_record(raw, "2020-01-01 10:00:00")
            raw.commit()

        records = db.get_records_in_date_range("2025-01-01", "2025-01-02")
        assert records == []

    # ── ordering ──────────────────────────────────────────────────────

    def test_records_sorted_by_timestamp_desc(self, temp_dir, reset_singletons):
        db_path = temp_dir / "order.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            _insert_record(raw, "2024-07-10 09:00:00", title="A")
            _insert_record(raw, "2024-07-10 10:00:00", title="B")
            _insert_record(raw, "2024-07-10 11:00:00", title="C")
            raw.commit()

        records = db.get_records_in_date_range("2024-07-10", "2024-07-10")
        assert [r["title"] for r in records] == ["C", "B", "A"]

    # ── user_name filter ──────────────────────────────────────────────

    def test_filter_by_user_name(self, temp_dir, reset_singletons):
        db_path = temp_dir / "user_filter.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            _insert_record(raw, "2024-08-01 12:00:00", user_name="alice", title="A1")
            _insert_record(raw, "2024-08-01 13:00:00", user_name="bob", title="B1")
            _insert_record(raw, "2024-08-02 12:00:00", user_name="alice", title="A2")
            raw.commit()

        records = db.get_records_in_date_range(
            "2024-08-01", "2024-08-02", user_name="alice"
        )
        assert len(records) == 2
        assert all(r["user_name"] == "alice" for r in records)

    # ── source filter ─────────────────────────────────────────────────

    def test_filter_by_source(self, temp_dir, reset_singletons):
        db_path = temp_dir / "src_filter.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            _insert_record(raw, "2024-09-01 12:00:00", source="custom", title="C1")
            _insert_record(raw, "2024-09-01 13:00:00", source="plex", title="P1")
            _insert_record(raw, "2024-09-02 12:00:00", source="custom", title="C2")
            raw.commit()

        records = db.get_records_in_date_range(
            "2024-09-01", "2024-09-02", source="custom"
        )
        assert len(records) == 2
        assert all(r["source"] == "custom" for r in records)

    # ── combined filters ──────────────────────────────────────────────

    def test_combined_filters(self, temp_dir, reset_singletons):
        db_path = temp_dir / "combined.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            _insert_record(
                raw,
                "2024-10-01 12:00:00",
                user_name="alice",
                source="custom",
                title="AC",
            )
            _insert_record(
                raw, "2024-10-01 13:00:00", user_name="alice", source="plex", title="AP"
            )
            _insert_record(
                raw, "2024-10-01 14:00:00", user_name="bob", source="custom", title="BC"
            )
            raw.commit()

        records = db.get_records_in_date_range(
            "2024-10-01",
            "2024-10-01",
            user_name="alice",
            source="custom",
        )
        assert len(records) == 1
        assert records[0]["title"] == "AC"

    # ── limit ─────────────────────────────────────────────────────────

    def test_limit(self, temp_dir, reset_singletons):
        db_path = temp_dir / "limit.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            for h in range(24):
                _insert_record(raw, f"2024-11-01 {h:02d}:00:00", title=f"H{h:02d}")
            raw.commit()

        records = db.get_records_in_date_range("2024-11-01", "2024-11-01", limit=5)
        assert len(records) == 5
        # Since we inserted from 00 to 23, DESC should return top 5 (23..19)
        assert all(int(r["title"][1:]) >= 19 for r in records)

    def test_default_limit_is_200(self, temp_dir, reset_singletons):
        db_path = temp_dir / "def_limit.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            for i in range(250):
                _insert_record(raw, f"2024-12-01 12:{i % 60:02d}:00", title=f"R{i}")
            raw.commit()

        records = db.get_records_in_date_range("2024-12-01", "2024-12-01")
        assert len(records) == 200

    # ── index ─────────────────────────────────────────────────────────

    def test_timestamp_index_exists(self, temp_dir, reset_singletons):
        """Verify idx_sync_records_timestamp index is present."""
        db_path = temp_dir / "idx.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='idx_sync_records_timestamp'"
            )
            assert cursor.fetchone() is not None

    # ── returned dict structure ───────────────────────────────────────

    def test_returned_dict_has_all_columns(self, temp_dir, reset_singletons):
        db_path = temp_dir / "cols.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        with sqlite3.connect(str(db_path)) as raw:
            _insert_record(
                raw,
                "2025-01-15 10:30:00",
                user_name="u",
                title="Test",
                source="api",
                status="success",
            )
            raw.commit()

        records = db.get_records_in_date_range("2025-01-15", "2025-01-15")
        assert len(records) == 1
        r = records[0]
        expected_keys = {
            "id",
            "timestamp",
            "user_name",
            "title",
            "ori_title",
            "season",
            "episode",
            "subject_id",
            "episode_id",
            "status",
            "message",
            "source",
            "media_type",
            "bgm_title",
        }
        assert set(r.keys()) == expected_keys
        assert r["user_name"] == "u"
        assert r["title"] == "Test"
        assert r["source"] == "api"
        assert r["media_type"] == "episode"
        assert r["bgm_title"] == ""
