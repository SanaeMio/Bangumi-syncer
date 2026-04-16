"""
DatabaseManager tests
"""

from unittest.mock import patch

import pytest


class TestDatabaseManager:
    """Test DatabaseManager class"""

    def test_database_init(self, temp_dir, reset_singletons):
        """Test database initialization"""
        db_path = temp_dir / "test.db"

        from app.core.database import DatabaseManager

        # Patch logging to avoid output during test
        with patch("app.core.database.logger"):
            db = DatabaseManager(str(db_path))

            assert db.db_path == db_path
            assert db_path.exists()

    def test_log_sync_record(self, temp_dir, reset_singletons):
        """Test logging sync record"""
        db_path = temp_dir / "test.db"

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

            # Log a record
            db.log_sync_record(
                user_name="test_user",
                title="测试动画",
                ori_title=None,
                season=1,
                episode=12,
                subject_id="123456",
                episode_id="789",
                status="success",
                message="Test message",
                source="custom",
            )

            # Verify the record was saved
            result = db.get_sync_records(limit=10)
            assert result["total"] == 1
            assert result["records"][0]["user_name"] == "test_user"
            assert result["records"][0]["title"] == "测试动画"
            assert result["records"][0]["season"] == 1
            assert result["records"][0]["episode"] == 12

    def test_get_sync_records_basic(self, temp_dir, reset_singletons):
        """Test getting sync records basic"""
        db_path = temp_dir / "test.db"

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

            # Add multiple records
            for i in range(5):
                db.log_sync_record(
                    user_name="test_user",
                    title=f"测试动画{i}",
                    ori_title=f"Test Anime {i}",
                    season=1,
                    episode=i + 1,
                    status="success",
                )

            # Get records
            result = db.get_sync_records(limit=10)

            assert result["total"] == 5
            assert len(result["records"]) == 5
            assert result["limit"] == 10
            assert result["offset"] == 0

    def test_get_sync_records_with_filters(self, temp_dir, reset_singletons):
        """Test getting sync records with filters"""
        db_path = temp_dir / "test.db"

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

            # Add records with different statuses
            db.log_sync_record(
                user_name="user1",
                title="动画1",
                ori_title=None,
                season=1,
                episode=1,
                status="success",
            )
            db.log_sync_record(
                user_name="user1",
                title="动画2",
                ori_title=None,
                season=1,
                episode=1,
                status="error",
            )
            db.log_sync_record(
                user_name="user2",
                title="动画3",
                ori_title=None,
                season=1,
                episode=1,
                status="success",
            )

            # Filter by status
            result = db.get_sync_records(status="success")
            assert result["total"] == 2

            # Filter by user_name
            result = db.get_sync_records(user_name="user1")
            assert result["total"] == 2

            # Filter by source
            db.log_sync_record(
                user_name="user3",
                title="动画4",
                ori_title=None,
                season=1,
                episode=1,
                status="success",
                source="plex",
            )
            result = db.get_sync_records(source_prefix="p")
            assert result["total"] == 1

    def test_get_sync_record_by_id(self, temp_dir, reset_singletons):
        """Test getting sync record by ID"""
        db_path = temp_dir / "test.db"

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

            # Add a record
            db.log_sync_record(
                user_name="test_user",
                title="测试动画",
                ori_title="Test Anime",
                season=1,
                episode=12,
                status="success",
            )

            # Get by ID
            result = db.get_sync_record_by_id(1)
            assert result is not None
            assert result["user_name"] == "test_user"
            assert result["title"] == "测试动画"

            # Get non-existent ID
            result = db.get_sync_record_by_id(999)
            assert result is None

    def test_update_sync_record_status(self, temp_dir, reset_singletons):
        """Test updating sync record status"""
        db_path = temp_dir / "test.db"

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

            # Add a record
            db.log_sync_record(
                user_name="test_user",
                title="测试动画",
                ori_title=None,
                season=1,
                episode=1,
                status="pending",
            )

            # Update status
            success = db.update_sync_record_status(1, "success", "Updated message")
            assert success is True

            # Verify update
            result = db.get_sync_record_by_id(1)
            assert result["status"] == "success"
            assert result["message"] == "Updated message"

            # Update non-existent record
            success = db.update_sync_record_status(999, "success")
            assert success is False

    def test_get_sync_stats(self, temp_dir, reset_singletons):
        """Test getting sync statistics"""
        db_path = temp_dir / "test.db"

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

            # Add various records
            db.log_sync_record(
                user_name="user1",
                title="动画1",
                ori_title=None,
                season=1,
                episode=1,
                status="success",
            )
            db.log_sync_record(
                user_name="user1",
                title="动画2",
                ori_title=None,
                season=1,
                episode=1,
                status="success",
            )
            db.log_sync_record(
                user_name="user2",
                title="动画3",
                ori_title=None,
                season=1,
                episode=1,
                status="error",
            )

            # Get stats
            stats = db.get_sync_stats()

            assert stats["total_syncs"] == 3
            assert stats["success_syncs"] == 2
            assert stats["error_syncs"] == 1
            assert stats["success_rate"] == pytest.approx(66.67, rel=0.1)
            assert len(stats["user_stats"]) == 2

    def test_pagination(self, temp_dir, reset_singletons):
        """Test pagination of records"""
        db_path = temp_dir / "test.db"

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

            # Add many records
            for i in range(15):
                db.log_sync_record(
                    user_name="test_user",
                    title=f"动画{i}",
                    ori_title=None,
                    season=1,
                    episode=i + 1,
                    status="success",
                )

            # Test pagination
            result1 = db.get_sync_records(limit=5, offset=0)
            result2 = db.get_sync_records(limit=5, offset=5)
            result3 = db.get_sync_records(limit=5, offset=10)

            assert result1["total"] == 15
            assert len(result1["records"]) == 5
            assert len(result2["records"]) == 5
            assert len(result3["records"]) == 5

    def test_feiniu_sync_history(self, temp_dir, reset_singletons):
        db_path = temp_dir / "fn.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
            assert db.is_feiniu_item_synced("u1", "it1") is False
            assert db.save_feiniu_sync_history("u1", "it1", 12345) is True
            assert db.is_feiniu_item_synced("u1", "it1") is True
            assert db.is_feiniu_item_synced("u1", "it2") is False

    def test_feiniu_watermark_meta(self, temp_dir, reset_singletons):
        db_path = temp_dir / "fnwm.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
            db.clear_feiniu_min_update_watermark()
            v1 = db.get_or_create_feiniu_min_update_watermark_ms()
            v2 = db.get_or_create_feiniu_min_update_watermark_ms()
            assert v1 == v2
            db.set_feiniu_min_update_watermark_now()
            v3 = db.get_or_create_feiniu_min_update_watermark_ms()
            assert v3 >= v1
            db.clear_feiniu_min_update_watermark()
            from app.core.database import FEINIU_MIN_UPDATE_WATERMARK_META_KEY

            assert db.get_feiniu_meta(FEINIU_MIN_UPDATE_WATERMARK_META_KEY) is None
