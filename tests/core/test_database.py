"""
DatabaseManager tests
"""

import sqlite3
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
            assert result["records"][0]["media_type"] == "episode"

    def test_log_sync_record_movie_media_type(self, temp_dir, reset_singletons):
        db_path = temp_dir / "movie.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
            db.log_sync_record(
                user_name="u",
                title="剧场版",
                ori_title=None,
                season=1,
                episode=1,
                status="success",
                media_type="movie",
            )
            r = db.get_sync_record_by_id(1)
            assert r["media_type"] == "movie"

    def test_migrate_adds_media_type_column(self, temp_dir, reset_singletons):
        """旧表无 media_type 时自动 ALTER 并回填"""
        db_path = temp_dir / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE sync_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_name TEXT NOT NULL,
                title TEXT NOT NULL,
                ori_title TEXT,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                subject_id TEXT,
                episode_id TEXT,
                status TEXT NOT NULL,
                message TEXT,
                source TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """INSERT INTO sync_records
            (timestamp, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source)
            VALUES ('2020-01-01', 'u', 't', NULL, 1, 1, NULL, NULL, 'success', '', 'custom')
            """
        )
        conn.commit()
        conn.close()

        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
        row = db.get_sync_record_by_id(1)
        assert row["media_type"] == "episode"

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

    def test_feiniu_sync_history_save_and_batch_query(self, temp_dir, reset_singletons):
        db_path = temp_dir / "fn.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
            assert db.save_feiniu_sync_history("u1", "it1", 12345) is True
            synced = db.get_feiniu_synced_set(["u1"])
            assert ("u1", "it1") in synced
            assert ("u1", "it2") not in synced

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


class TestDatabaseDockerAndTrakt:
    """Docker 默认路径、Trakt CRUD 与部分异常分支。"""

    def test_database_auto_path_docker_migrates_legacy(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOCKER_CONTAINER", "1")
        legacy = tmp_path / "sync_records.db"
        conn = sqlite3.connect(str(legacy))
        conn.execute("CREATE TABLE IF NOT EXISTS t(i INTEGER)")
        conn.commit()
        conn.close()
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(None)
        assert db.db_path.resolve() == (tmp_path / "data" / "sync_records.db").resolve()
        assert (tmp_path / "data" / "sync_records.db").is_file()
        assert not legacy.exists()

    def test_database_auto_path_non_docker_relative(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DOCKER_CONTAINER", raising=False)
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(None)
        assert db.db_path.resolve() == (tmp_path / "sync_records.db").resolve()

    def test_trakt_save_insert_update_delete_and_list(self, temp_dir, reset_singletons):
        db_path = temp_dir / "trakt.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
            now = 1_700_000_000
            assert (
                db.save_trakt_config(
                    {
                        "user_id": "u1",
                        "access_token": "t1",
                        "refresh_token": "r1",
                        "expires_at": now,
                        "enabled": True,
                        "sync_interval": "0 */6 * * *",
                        "last_sync_time": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                is True
            )
            row = db.get_trakt_config("u1")
            assert row is not None
            assert row["access_token"] == "t1"
            assert (
                db.save_trakt_config(
                    {
                        "user_id": "u1",
                        "access_token": "t2",
                        "refresh_token": "r1",
                        "expires_at": now,
                        "enabled": True,
                        "sync_interval": "0 */6 * * *",
                        "last_sync_time": 1,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                is True
            )
            assert db.get_trakt_config("u1")["access_token"] == "t2"
            assert db.delete_trakt_config("u1") is True
            assert db.get_trakt_config("u1") is None
            assert db.delete_trakt_config("u1") is False

    def test_trakt_configs_with_sync_enabled_and_history(
        self, temp_dir, reset_singletons
    ):
        db_path = temp_dir / "t2.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
            now = 1_700_000_001
            db.save_trakt_config(
                {
                    "user_id": "a",
                    "access_token": "x",
                    "refresh_token": None,
                    "expires_at": None,
                    "enabled": True,
                    "sync_interval": "0 */6 * * *",
                    "last_sync_time": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            db.save_trakt_config(
                {
                    "user_id": "b",
                    "access_token": "y",
                    "refresh_token": None,
                    "expires_at": None,
                    "enabled": False,
                    "sync_interval": "0 */6 * * *",
                    "last_sync_time": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            enabled = db.get_trakt_configs_with_sync_enabled()
            assert len(enabled) == 1
            assert enabled[0]["user_id"] == "a"
            assert (
                db.save_trakt_sync_history(
                    {
                        "user_id": "a",
                        "trakt_item_id": "episode:1",
                        "media_type": "episode",
                        "watched_at": 100,
                        "synced_at": 200,
                    }
                )
                is True
            )
            hist = db.get_trakt_sync_history("a", limit=10, offset=0)
            assert hist["total"] == 1
            assert db.get_last_sync_time("a") == 100
            assert db.get_last_sync_time("ghost") is None

    def test_feiniu_watermark_invalid_meta_resets(self, temp_dir, reset_singletons):
        db_path = temp_dir / "wm_bad.db"
        with patch("app.core.database.logger"):
            from app.core.database import (
                FEINIU_MIN_UPDATE_WATERMARK_META_KEY,
                DatabaseManager,
            )

            db = DatabaseManager(str(db_path))
            db.set_feiniu_meta(FEINIU_MIN_UPDATE_WATERMARK_META_KEY, "not-a-number")
            v = db.get_or_create_feiniu_min_update_watermark_ms()
            assert isinstance(v, int)

    def test_log_sync_record_swallows_db_error(self, temp_dir, reset_singletons):
        db_path = temp_dir / "err.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
        # 断开持久连接，让下次操作触发 sqlite3.connect 失败
        db._conn = None
        with patch("app.core.database.sqlite3.connect", side_effect=OSError("disk")):
            db.log_sync_record(
                user_name="u",
                title="t",
                ori_title=None,
                season=1,
                episode=1,
                status="success",
            )

    def test_get_sync_records_raises_on_query_error(self, temp_dir, reset_singletons):
        db_path = temp_dir / "q.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
        # 断开持久连接，让下次操作触发 sqlite3.connect 失败
        db._conn = None
        with patch(
            "app.core.database.sqlite3.connect", side_effect=RuntimeError("bad")
        ):
            with pytest.raises(RuntimeError, match="bad"):
                db.get_sync_records()

    def test_update_sync_record_status_returns_false_on_db_error(
        self, temp_dir, reset_singletons
    ):
        db_path = temp_dir / "u.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))
        db._conn = None
        with patch("app.core.database.sqlite3.connect", side_effect=OSError("x")):
            assert db.update_sync_record_status(1, "ok") is False

    def test_error_sync_record_creates_in_app_notification(
        self, temp_dir, reset_singletons
    ):
        db_path = temp_dir / "inbox_notify.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        record_id = db.log_sync_record(
            user_name="tester",
            title="测试番剧",
            ori_title=None,
            season=1,
            episode=5,
            status="error",
            message="未找到番剧",
            source="custom",
        )
        assert record_id == 1

        notifs = db.list_in_app_notifications()
        assert len(notifs) == 1
        assert notifs[0]["type"] == "sync_failed"
        assert "测试番剧" in notifs[0]["title"]
        assert notifs[0]["ref_id"] == 1
        assert notifs[0]["read_at"] is None

    def test_inbox_read_state(self, temp_dir, reset_singletons):
        db_path = temp_dir / "inbox_read.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        db.log_sync_record(
            user_name="u",
            title="A",
            ori_title=None,
            season=1,
            episode=1,
            status="error",
            message="e",
            source="test",
        )
        assert db.count_unread_notifications() == 1

        notif_id = db.list_in_app_notifications()[0]["id"]
        assert db.mark_notification_read(notif_id) is True
        assert db.count_unread_notifications() == 0

        assert db.mark_announcement_read("ann-a") is True
        assert "ann-a" in db.get_read_announcement_ids()
        assert db.mark_all_announcements_read(["ann-b", "ann-c"]) == 2

        db.log_sync_record(
            user_name="u",
            title="B",
            ori_title=None,
            season=1,
            episode=2,
            status="error",
            message="e2",
            source="test",
        )
        assert db.mark_all_notifications_read() == 1
        assert db.count_unread_notifications() == 0

    def test_backfill_historical_error_notifications_read(
        self, temp_dir, reset_singletons
    ):
        import sqlite3

        from app.core.database import (
            INBOX_ERROR_BACKFILL_META_KEY,
            DatabaseManager,
        )

        db_path = temp_dir / "inbox_backfill.db"
        with patch("app.core.database.logger"):
            db = DatabaseManager(str(db_path))

        db.delete_feiniu_meta(INBOX_ERROR_BACKFILL_META_KEY)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO sync_records
                (timestamp, user_name, title, ori_title, season, episode,
                 subject_id, episode_id, status, message, source, media_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-01-01 10:00:00",
                    "u",
                    "旧番剧",
                    None,
                    1,
                    3,
                    None,
                    None,
                    "error",
                    "历史失败",
                    "test",
                    "episode",
                ),
            )
            conn.commit()

        assert db.backfill_historical_error_notifications() == 1
        notifs = db.list_in_app_notifications()
        assert len(notifs) == 1
        assert notifs[0]["read_at"] is not None
        assert notifs[0]["ref_id"] == 1
        assert db.backfill_historical_error_notifications() == 0

    def test_update_sync_record_status_marks_notification_read(
        self, temp_dir, reset_singletons
    ):
        db_path = temp_dir / "inbox_link.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        record_id = db.log_sync_record(
            user_name="u",
            title="联动番剧",
            ori_title=None,
            season=1,
            episode=1,
            status="error",
            message="fail",
            source="test",
        )
        assert db.count_unread_notifications() == 1
        assert db.update_sync_record_status(record_id, "success", "ok") is True
        assert db.count_unread_notifications() == 0

    def test_mark_notification_group_read(self, temp_dir, reset_singletons):
        db_path = temp_dir / "inbox_group.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        db.log_sync_record(
            user_name="u",
            title="组内番剧",
            ori_title=None,
            season=1,
            episode=1,
            status="error",
            message="e1",
            source="test",
        )
        db.log_sync_record(
            user_name="u",
            title="组内番剧",
            ori_title=None,
            season=1,
            episode=2,
            status="error",
            message="e2",
            source="test",
        )
        assert db.count_unread_notifications() == 2
        first_id = db.list_in_app_notifications()[0]["id"]
        assert db.mark_notification_group_read(first_id) == 2
        assert db.count_unread_notifications() == 0


class TestSyncRecordsListNoMatchTrace:
    """列表查询不再返回 match_trace（改动 A）"""

    def test_get_sync_records_list_has_no_match_trace(self, temp_dir, reset_singletons):
        """get_sync_records 列表结果不含 match_trace 字段"""
        db_path = temp_dir / "list_no_trace.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        db.log_sync_record(
            user_name="u",
            title="标题",
            ori_title=None,
            season=1,
            episode=1,
            status="success",
            source="test",
            match_trace={"phase": "custom_mapping", "hit": True},
        )
        result = db.get_sync_records(limit=10)
        assert result["total"] == 1
        record = result["records"][0]
        assert "match_trace" not in record
        # 其余 match_* 字段仍在
        assert record["match_method"] == ""

    def test_get_match_records_list_has_no_match_trace(
        self, temp_dir, reset_singletons
    ):
        """get_match_records 列表结果不含 match_trace 字段"""
        db_path = temp_dir / "match_no_trace.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        db.log_sync_record(
            user_name="u",
            title="标题",
            ori_title=None,
            season=1,
            episode=1,
            status="success",
            source="test",
            match_method="api_search",
            match_score=0.92,
            match_platform="TV",
            match_trace={"phase": "api_search", "candidates": [{"id": 1}]},
        )
        result = db.get_match_records(limit=10)
        assert result["total"] == 1
        record = result["records"][0]
        assert "match_trace" not in record
        assert record["match_method"] == "api_search"
        assert record["match_score"] == 0.92
        assert record["match_platform"] == "TV"

    def test_get_sync_record_by_id_still_has_match_trace(
        self, temp_dir, reset_singletons
    ):
        """详情查询仍返回完整 match_trace"""
        db_path = temp_dir / "detail_has_trace.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        record_id = db.log_sync_record(
            user_name="u",
            title="标题",
            ori_title=None,
            season=1,
            episode=1,
            status="success",
            source="test",
            match_trace={"phase": "custom_mapping", "hit": True},
        )
        assert record_id is not None
        detail = db.get_sync_record_by_id(record_id)
        assert detail is not None
        assert "match_trace" in detail
        assert "custom_mapping" in detail["match_trace"]


class TestCleanupOldRecords:
    """retention 自动清理（改动 C）"""

    def test_cleanup_removes_old_records(self, temp_dir, reset_singletons):
        """清理超过保留天数的记录"""
        db_path = temp_dir / "cleanup_old.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        db.log_sync_record(
            user_name="u",
            title="旧记录",
            ori_title=None,
            season=1,
            episode=1,
            status="success",
            source="test",
        )
        # 将 timestamp 改为 40 天前
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE sync_records SET timestamp = datetime('now', '-40 days')"
            )
            conn.commit()

        deleted = db.cleanup_old_records(30)
        assert deleted == 1
        result = db.get_sync_records(limit=10)
        assert result["total"] == 0

    def test_cleanup_keeps_recent_records(self, temp_dir, reset_singletons):
        """保留期内的记录不被清理"""
        db_path = temp_dir / "cleanup_keep.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        db.log_sync_record(
            user_name="u",
            title="新记录",
            ori_title=None,
            season=1,
            episode=1,
            status="success",
            source="test",
        )
        # 5 天前的记录，retention=30 不应清理
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE sync_records SET timestamp = datetime('now', '-5 days')"
            )
            conn.commit()

        deleted = db.cleanup_old_records(30)
        assert deleted == 0
        result = db.get_sync_records(limit=10)
        assert result["total"] == 1

    def test_cleanup_zero_retention_does_nothing(self, temp_dir, reset_singletons):
        """retention_days <= 0 时不清理（永不清理语义）"""
        db_path = temp_dir / "cleanup_zero.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        db.log_sync_record(
            user_name="u",
            title="远古记录",
            ori_title=None,
            season=1,
            episode=1,
            status="success",
            source="test",
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE sync_records SET timestamp = datetime('now', '-365 days')"
            )
            conn.commit()

        # retention=0 不清理
        assert db.cleanup_old_records(0) == 0
        # 负数也不清理
        assert db.cleanup_old_records(-1) == 0
        result = db.get_sync_records(limit=10)
        assert result["total"] == 1

    def test_cleanup_facade_forwards(self, temp_dir, reset_singletons):
        """DatabaseManager facade 正确转发 cleanup_old_records"""
        db_path = temp_dir / "cleanup_facade.db"
        with patch("app.core.database.logger"):
            from app.core.database import DatabaseManager

            db = DatabaseManager(str(db_path))

        db.log_sync_record(
            user_name="u",
            title="facade 旧记录",
            ori_title=None,
            season=1,
            episode=1,
            status="success",
            source="test",
        )
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE sync_records SET timestamp = datetime('now', '-100 days')"
            )
            conn.commit()

        # 通过 facade 调用，retention=50 应清理 100 天前的记录
        deleted = db.cleanup_old_records(50)
        assert deleted == 1
        result = db.get_sync_records(limit=10)
        assert result["total"] == 0
