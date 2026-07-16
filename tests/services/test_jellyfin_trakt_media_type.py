"""Jellyfin 和 Trakt 驱动接入 detect_media_type 测试

覆盖：
- Jellyfin extractor：movie/episode 分支检测 OVA/OAD/三次元
- Trakt sync_service：movie/episode 分支检测 OVA/OAD/三次元
"""

from unittest.mock import patch

from app.services.jellyfin.extractor import extract_jellyfin_data
from app.services.trakt.models import TraktHistoryItem
from app.services.trakt.sync_service import TraktSyncService

# ===== Jellyfin =====


def _jellyfin_episode_data(title="测试番剧", ori_title="", media_type="episode"):
    return {
        "title": title,
        "ori_title": ori_title,
        "media_type": media_type,
        "season": 1,
        "episode": 5,
        "release_date": "2024-01-01",
        "user_name": "tester",
    }


def _jellyfin_movie_data(title="测试电影", ori_title=""):
    return {
        "title": title,
        "ori_title": ori_title,
        "media_type": "movie",
        "season": 1,
        "episode": 1,
        "release_date": "2024-01-01",
        "user_name": "tester",
    }


class TestJellyfinExtractorMediaTypeDetection:
    """Jellyfin extractor 接入 detect_media_type"""

    def test_episode_with_ova_keyword_detected_as_ova(self):
        """剧集标题含 OVA 关键词检测为 ova"""
        data = _jellyfin_episode_data(title="某番剧 OVA", media_type="episode")
        item = extract_jellyfin_data(data)
        assert item.media_type == "ova"

    def test_episode_with_oad_keyword_detected_as_oad(self):
        """剧集标题含 OAD 关键词检测为 oad"""
        data = _jellyfin_episode_data(title="某番剧 OAD", media_type="episode")
        item = extract_jellyfin_data(data)
        assert item.media_type == "oad"

    def test_episode_with_real_action_keyword_detected(self):
        """剧集标题含三次元关键词检测为 real_action"""
        data = _jellyfin_episode_data(title="某日剧 第一季", media_type="episode")
        item = extract_jellyfin_data(data)
        assert item.media_type == "real_action"

    def test_episode_normal_title_keeps_episode(self):
        """普通剧集标题保持 episode"""
        data = _jellyfin_episode_data(title="普通番剧", media_type="episode")
        item = extract_jellyfin_data(data)
        assert item.media_type == "episode"

    def test_movie_with_real_action_keyword_detected(self):
        """电影标题含真人版关键词检测为 real_action"""
        data = _jellyfin_movie_data(title="真人版 某电影")
        item = extract_jellyfin_data(data)
        assert item.media_type == "real_action"

    def test_movie_normal_title_keeps_movie(self):
        """普通电影标题保持 movie"""
        data = _jellyfin_movie_data(title="普通剧场版")
        item = extract_jellyfin_data(data)
        assert item.media_type == "movie"

    def test_movie_ori_title_used_for_detection(self):
        """原始标题参与检测"""
        data = _jellyfin_movie_data(title="普通标题", ori_title="Live Action Movie")
        item = extract_jellyfin_data(data)
        assert item.media_type == "real_action"


# ===== Trakt =====


def _make_trakt_episode_item(title="Test Show", original_title=None):
    show = {"ids": {"tmdb": "123"}, "title": title}
    if original_title:
        show["original_title"] = original_title
    episode = {"season": 1, "number": 1, "ids": {"trakt": 100}}
    return TraktHistoryItem(
        id=1,
        watched_at="2024-06-01T12:00:00.000Z",
        action="watch",
        type="episode",
        show=show,
        episode=episode,
    )


def _make_trakt_movie_item(title="Test Movie", original_title=None, tmdb="456"):
    movie = {
        "title": title,
        "ids": {"trakt": 200, "tmdb": tmdb},
        "released": "2024-01-15",
    }
    if original_title:
        movie["original_title"] = original_title
    return TraktHistoryItem(
        id=2,
        watched_at="2024-06-01T12:00:00.000Z",
        action="watch",
        type="movie",
        movie=movie,
    )


class TestTraktMediaTypeDetection:
    """Trakt sync_service 接入 detect_media_type"""

    def _make_service(self):
        return TraktSyncService()

    def test_episode_with_ova_keyword_detected_as_ova(self):
        """剧集标题含 OVA 关键词检测为 ova"""
        svc = self._make_service()
        item = _make_trakt_episode_item(title="Some Anime OVA")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "Some Anime OVA"
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "ova"

    def test_episode_with_oad_keyword_detected_as_oad(self):
        """剧集标题含 OAD 关键词检测为 oad"""
        svc = self._make_service()
        item = _make_trakt_episode_item(title="Some Anime OAD")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "Some Anime OAD"
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "oad"

    def test_episode_with_real_action_keyword_detected(self):
        """剧集标题含日剧关键词检测为 real_action"""
        svc = self._make_service()
        item = _make_trakt_episode_item(title="某日剧 第一季")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "某日剧 第一季"
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "real_action"

    def test_episode_normal_title_keeps_episode(self):
        """普通剧集标题保持 episode"""
        svc = self._make_service()
        item = _make_trakt_episode_item(title="Normal Anime Show")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "Normal Anime Show"
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "episode"

    def test_movie_with_real_action_keyword_detected(self):
        """电影标题含真人版关键词检测为 real_action"""
        svc = self._make_service()
        item = _make_trakt_movie_item(title="真人版 某电影")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = None
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "real_action"

    def test_movie_normal_title_keeps_movie(self):
        """普通电影标题保持 movie"""
        svc = self._make_service()
        item = _make_trakt_movie_item(title="Normal Movie Title")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = None
            result = svc._convert_trakt_history_to_custom_item("user1", item)
        assert result is not None
        assert result.media_type == "movie"
