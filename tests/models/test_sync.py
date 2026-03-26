"""
Sync models tests
"""


class TestSyncModels:
    """Test sync-related Pydantic models"""

    def test_custom_item_model(self):
        """Test CustomItem model"""
        from app.models.sync import CustomItem

        item = CustomItem(
            media_type="anime",
            title="测试动画",
            ori_title="Test Anime",
            season=1,
            episode=12,
            release_date="2024-01-01",
            user_name="test_user",
            source="custom",
        )
        assert item.media_type == "anime"
        assert item.title == "测试动画"
        assert item.season == 1
        assert item.episode == 12

    def test_custom_item_optional_fields(self):
        """Test CustomItem with optional fields"""
        from app.models.sync import CustomItem

        item = CustomItem(
            media_type="anime",
            title="测试动画",
            season=1,
            episode=12,
            release_date="2024-01-01",
            user_name="test_user",
        )
        assert item.ori_title is None
        assert item.source is None

    def test_sync_response_model(self):
        """Test SyncResponse model"""
        from app.models.sync import SyncResponse

        # With data
        resp = SyncResponse(
            status="success", message="Sync completed", data={"synced": 10}
        )
        assert resp.status == "success"
        assert resp.data == {"synced": 10}

        # Without data
        resp = SyncResponse(status="error", message="Sync failed")
        assert resp.status == "error"
        assert resp.data is None

    def test_plex_webhook_data_model(self):
        """Test PlexWebhookData model"""
        from app.models.sync import PlexWebhookData

        data = PlexWebhookData(
            event="media.scrobble",
            Account={"title": "test_user"},
            Metadata={
                "type": "episode",
                "title": "第01话",
                "grandparentTitle": "番剧名称",
                "parentIndex": 1,
                "index": 1,
            },
        )
        assert data.event == "media.scrobble"
        assert data.Account["title"] == "test_user"

    def test_plex_webhook_data_with_optional_fields(self):
        """Test PlexWebhookData with optional fields"""
        from app.models.sync import PlexWebhookData

        data = PlexWebhookData(
            event="media.scrobble",
            Account={"title": "test_user"},
            Metadata={
                "type": "episode",
                "title": "第01话",
                "grandparentTitle": "番剧名称",
                "parentIndex": 1,
                "index": 1,
            },
            user=True,
            owner=False,
        )
        assert data.user is True
        assert data.owner is False

    def test_emby_webhook_data_model(self):
        """Test EmbyWebhookData model"""
        from app.models.sync import EmbyWebhookData

        data = EmbyWebhookData(
            Event="item.markplayed",
            User={"Name": "test_user", "Id": "user-id"},
            Item={
                "Type": "Episode",
                "SeriesName": "番剧名称",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
            },
        )
        assert data.Event == "item.markplayed"
        assert data.User["Name"] == "test_user"

    def test_emby_webhook_data_with_optional_fields(self):
        """Test EmbyWebhookData with optional fields"""
        from app.models.sync import EmbyWebhookData

        data = EmbyWebhookData(
            Event="item.markplayed",
            User={"Name": "test_user", "Id": "user-id"},
            Item={
                "Type": "Episode",
                "SeriesName": "番剧名称",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
            },
            Title="Test Title",
            Description="Test Description",
        )
        assert data.Title == "Test Title"
        assert data.Description == "Test Description"

    def test_jellyfin_webhook_data_model(self):
        """Test JellyfinWebhookData model"""
        from app.models.sync import JellyfinWebhookData

        data = JellyfinWebhookData(
            NotificationType="PlaybackStop",
            PlayedToCompletion="True",
            media_type="episode",
            title="番剧名称",
            ori_title="Original Title",
            season=1,
            episode=12,
            user_name="test_user",
            release_date="2024-01-01",
        )
        assert data.NotificationType == "PlaybackStop"
        assert data.season == 1

    def test_jellyfin_webhook_data_optional_release_date(self):
        """Test JellyfinWebhookData with optional release_date"""
        from app.models.sync import JellyfinWebhookData

        data = JellyfinWebhookData(
            NotificationType="PlaybackStop",
            PlayedToCompletion="True",
            media_type="episode",
            title="番剧名称",
            ori_title="Original Title",
            season=1,
            episode=12,
            user_name="test_user",
        )
        assert data.release_date is None

    def test_sync_record_model(self):
        """Test SyncRecord model"""
        from app.models.sync import SyncRecord

        record = SyncRecord(
            id=1,
            timestamp="2024-01-01 12:00:00",
            user_name="test_user",
            title="测试动画",
            ori_title="Test Anime",
            season=1,
            episode=12,
            subject_id="123456",
            episode_id="789",
            status="success",
            message="Synced",
            source="custom",
        )
        assert record.id == 1
        assert record.status == "success"

    def test_sync_stats_model(self):
        """Test SyncStats model"""
        from app.models.sync import SyncStats

        stats = SyncStats(
            total_syncs=100,
            success_syncs=95,
            error_syncs=5,
            today_syncs=10,
            success_rate=95.0,
            user_stats=[{"user": "user1", "count": 50}],
            daily_stats=[{"date": "2024-01-01", "count": 10}],
        )
        assert stats.total_syncs == 100
        assert stats.success_rate == 95.0
        assert len(stats.user_stats) == 1

    def test_test_sync_request_model(self):
        """Test TestSyncRequest model"""
        from app.models.sync import TestSyncRequest

        req = TestSyncRequest(
            title="测试动画",
            ori_title="Test Anime",
            season=1,
            episode=12,
            release_date="2024-01-01",
            user_name="test_user",
            source="test",
        )
        assert req.title == "测试动画"
        assert req.season == 1

    def test_test_sync_request_defaults(self):
        """Test TestSyncRequest default values"""
        from app.models.sync import TestSyncRequest

        req = TestSyncRequest(title="测试动画")
        assert req.season == 1
        assert req.episode == 1
        assert req.user_name == "test_user"
        assert req.source == "test"
