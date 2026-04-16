"""app.services.trakt.models 属性与边界。"""

from unittest.mock import patch

from app.services.trakt.models import (
    TraktCollectionItem,
    TraktHistoryItem,
    TraktRatingItem,
    TraktSyncResult,
    TraktSyncStats,
)


def test_trakt_history_item_movie_trakt_id():
    item = TraktHistoryItem(
        id=9,
        watched_at="2024-06-01T12:00:00Z",
        action="scrobble",
        type="movie",
        movie={"ids": {"trakt": 555}},
        show=None,
        episode=None,
    )
    assert item.media_type == "movie"
    assert item.trakt_item_id == "movie:555"


def test_trakt_history_item_episode_trakt_id():
    item = TraktHistoryItem(
        id=1,
        watched_at="2024-06-01T12:00:00Z",
        action="scrobble",
        type="episode",
        movie=None,
        show=None,
        episode={"ids": {"trakt": 999}},
    )
    assert item.trakt_item_id == "episode:999"


def test_trakt_history_item_fallback_id_when_no_ids_dict():
    item = TraktHistoryItem(
        id=42,
        watched_at="2024-01-01T00:00:00Z",
        action="checkin",
        type="show",
        movie=None,
        show={"title": "X"},
        episode=None,
    )
    assert item.trakt_item_id == "show:42"


def test_trakt_history_item_watched_timestamp_iso():
    item = TraktHistoryItem(
        id=1,
        watched_at="2020-01-15T08:30:00+00:00",
        action="scrobble",
        type="movie",
        movie={"ids": {"trakt": 1}},
        show=None,
        episode=None,
    )
    assert item.watched_timestamp >= 1579072200


def test_trakt_history_item_watched_timestamp_bad_uses_time():
    item = TraktHistoryItem(
        id=1,
        watched_at="not-a-timestamp",
        action="scrobble",
        type="movie",
        movie={"ids": {"trakt": 1}},
        show=None,
        episode=None,
    )
    with patch("app.services.trakt.models.time.time", return_value=12_345.6):
        assert item.watched_timestamp == 12345


def test_trakt_rating_and_collection_media_type():
    r = TraktRatingItem(
        rating=8,
        rated_at="2024-01-01T00:00:00Z",
        type="movie",
        movie={},
        show=None,
        episode=None,
    )
    assert r.media_type == "movie"
    c = TraktCollectionItem(
        collected_at="2024-01-01T00:00:00Z",
        type="episode",
        movie=None,
        show={},
        episode={},
    )
    assert c.media_type == "episode"


def test_trakt_sync_result_and_stats_models():
    TraktSyncResult(success=True, message="ok", synced_count=1)
    TraktSyncStats(total_items=10, movies=3, episodes=7, start_time=1.0, end_time=2.0)
