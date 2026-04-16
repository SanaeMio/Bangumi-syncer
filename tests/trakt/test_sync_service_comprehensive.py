"""
Trakt 同步服务完整测试
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.trakt.models import TraktHistoryItem, TraktSyncResult
from app.services.trakt.sync_service import TraktSyncService


class TestTraktSyncServiceComprehensive:
    """Trakt 同步服务综合测试"""

    def test_init(self):
        """测试初始化"""
        service = TraktSyncService()
        assert service._active_syncs == {}
        assert service._sync_results == {}


@pytest.mark.asyncio
async def test_sync_user_trakt_data_no_config():
    """测试同步时配置不存在"""
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.database_manager"),
    ):
        mock_auth.get_user_trakt_config.return_value = None

        service = TraktSyncService()
        result = await service.sync_user_trakt_data("user1")

        assert result.success is False
        assert "不存在" in result.message


@pytest.mark.asyncio
async def test_sync_user_trakt_data_no_token():
    """测试同步时没有令牌"""
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.database_manager"),
    ):
        mock_config = MagicMock()
        mock_config.access_token = None
        mock_auth.get_user_trakt_config.return_value = mock_config

        service = TraktSyncService()
        result = await service.sync_user_trakt_data("user1")

        assert result.success is False
        assert "未授权" in result.message


@pytest.mark.asyncio
async def test_sync_user_trakt_data_token_expired():
    """测试令牌过期"""
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.database_manager"),
        patch("app.services.trakt.sync_service.TraktClientFactory"),
    ):
        mock_config = MagicMock()
        mock_config.access_token = "old_token"
        mock_config.is_token_expired.return_value = True
        mock_auth.get_user_trakt_config.return_value = mock_config
        mock_auth.refresh_token = AsyncMock(return_value=False)

        service = TraktSyncService()
        result = await service.sync_user_trakt_data("user1")

        assert result.success is False
        assert "过期" in result.message


class TestTraktSyncResult:
    """Trakt 同步结果测试"""

    def test_sync_result_creation(self):
        """测试创建同步结果"""
        result = TraktSyncResult(
            success=True,
            message="Test message",
            synced_count=10,
            skipped_count=5,
            error_count=1,
            details={"test": "data"},
        )

        assert result.success is True
        assert result.synced_count == 10
        assert result.skipped_count == 5
        assert result.error_count == 1


def test_trakt_sync_service_module_import():
    """测试模块导入"""
    from app.services.trakt.sync_service import TraktSyncService

    assert TraktSyncService is not None


@pytest.mark.asyncio
async def test_sync_user_trakt_data_refresh_ok_but_config_missing_after():
    with patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth:
        first = MagicMock()
        first.access_token = "t"
        first.is_token_expired.return_value = True
        mock_auth.get_user_trakt_config.side_effect = [first, None]
        mock_auth.refresh_token = AsyncMock(return_value=True)

        svc = TraktSyncService()
        r = await svc.sync_user_trakt_data("u1")
        assert r.success is False
        assert "不存在" in r.message


@pytest.mark.asyncio
async def test_sync_user_trakt_data_client_factory_returns_none():
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.TraktClientFactory") as mock_factory,
        patch("app.services.trakt.sync_service.database_manager"),
    ):
        cfg = MagicMock()
        cfg.access_token = "tok"
        cfg.is_token_expired.return_value = False
        mock_auth.get_user_trakt_config.return_value = cfg
        mock_factory.create_client = AsyncMock(return_value=None)

        svc = TraktSyncService()
        r = await svc.sync_user_trakt_data("u1")
        assert r.success is False
        assert "客户端" in r.message


@pytest.mark.asyncio
async def test_sync_user_trakt_data_outer_exception_from_history():
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.TraktClientFactory") as mock_factory,
        patch("app.services.trakt.sync_service.database_manager"),
    ):
        cfg = MagicMock()
        cfg.access_token = "tok"
        cfg.is_token_expired.return_value = False
        mock_auth.get_user_trakt_config.return_value = cfg
        client = MagicMock()
        client.get_all_watched_history = AsyncMock(side_effect=RuntimeError("boom"))
        client.close = AsyncMock()
        mock_factory.create_client = AsyncMock(return_value=client)

        svc = TraktSyncService()
        r = await svc.sync_user_trakt_data("u1")
        assert r.success is False
        assert r.error_count == 1
        assert "失败" in r.message


@pytest.mark.asyncio
async def test_sync_user_trakt_data_empty_history_success():
    with (
        patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
        patch("app.services.trakt.sync_service.TraktClientFactory") as mock_factory,
        patch("app.services.trakt.sync_service.database_manager") as mock_db,
    ):
        cfg = MagicMock()
        cfg.access_token = "tok"
        cfg.is_token_expired.return_value = False
        mock_auth.get_user_trakt_config.return_value = cfg
        client = MagicMock()
        client.get_all_watched_history = AsyncMock(return_value=[])
        client.close = AsyncMock()
        mock_factory.create_client = AsyncMock(return_value=client)

        svc = TraktSyncService()
        r = await svc.sync_user_trakt_data("u1")
        assert r.success is True
        mock_db.save_trakt_config.assert_called()


@pytest.mark.asyncio
async def test_sync_ratings_and_collection_stubs():
    svc = TraktSyncService()
    client = MagicMock()
    cfg = MagicMock()
    r1 = await svc._sync_ratings("u", client, cfg, False)
    r2 = await svc._sync_collection("u", client, cfg, False)
    assert r1.success and r2.success
    assert "暂未实现" in r1.message


def test_should_sync_item_true_when_no_records():
    with patch("app.services.trakt.sync_service.database_manager") as mock_db:
        mock_db.get_trakt_sync_history.return_value = None
        svc = TraktSyncService()
        item = MagicMock()
        item.trakt_item_id = "e:1"
        item.watched_timestamp = 1
        assert svc._should_sync_item("u", item) is True


def test_should_sync_item_false_when_duplicate():
    with patch("app.services.trakt.sync_service.database_manager") as mock_db:
        mock_db.get_trakt_sync_history.return_value = {
            "records": [
                {"trakt_item_id": "e:1", "watched_at": 100},
            ]
        }
        svc = TraktSyncService()
        item = MagicMock()
        item.trakt_item_id = "e:1"
        item.watched_timestamp = 100
        assert svc._should_sync_item("u", item) is False


def test_should_sync_item_true_when_history_check_raises():
    with patch("app.services.trakt.sync_service.database_manager") as mock_db:
        mock_db.get_trakt_sync_history.side_effect = OSError("db")
        svc = TraktSyncService()
        item = MagicMock()
        item.trakt_item_id = "e:1"
        item.watched_timestamp = 1
        assert svc._should_sync_item("u", item) is True


def test_record_sync_history_save_false_logs():
    with patch("app.services.trakt.sync_service.database_manager") as mock_db:
        mock_db.save_trakt_sync_history.return_value = False
        svc = TraktSyncService()
        item = MagicMock()
        item.trakt_item_id = "e:1"
        item.type = "episode"
        item.watched_timestamp = 5
        svc._record_sync_history("u", item, "tid")
        mock_db.save_trakt_sync_history.assert_called_once()


def test_record_sync_history_exception_swallowed():
    with patch("app.services.trakt.sync_service.database_manager") as mock_db:
        mock_db.save_trakt_sync_history.side_effect = RuntimeError("x")
        svc = TraktSyncService()
        item = MagicMock()
        item.trakt_item_id = "e:1"
        item.type = "episode"
        item.watched_timestamp = 5
        svc._record_sync_history("u", item, "tid")


def test_convert_trakt_history_non_episode_returns_none():
    svc = TraktSyncService()
    item = TraktHistoryItem(
        id=1,
        watched_at="2024-01-01T00:00:00Z",
        action="scrobble",
        type="movie",
        movie={"ids": {"trakt": 1}},
        show=None,
        episode=None,
    )
    assert svc._convert_trakt_history_to_custom_item("u", item) is None


def test_convert_trakt_history_missing_tmdb_returns_none():
    svc = TraktSyncService()
    item = TraktHistoryItem(
        id=1,
        watched_at="2024-01-01T00:00:00Z",
        action="scrobble",
        type="episode",
        movie=None,
        show={"ids": {}, "title": "S"},
        episode={"ids": {"trakt": 9}, "season": 1, "number": 1},
    )
    assert svc._convert_trakt_history_to_custom_item("u", item) is None


def test_convert_trakt_history_no_bangumi_title_returns_none():
    with patch("app.services.trakt.sync_service.bangumi_data") as bd:
        bd.get_title_by_tmdb_id.return_value = None
        svc = TraktSyncService()
        item = TraktHistoryItem(
            id=1,
            watched_at="2024-01-01T00:00:00Z",
            action="scrobble",
            type="episode",
            movie=None,
            show={"ids": {"tmdb": 42}, "title": "S", "original_title": "S"},
            episode={"ids": {"trakt": 9}, "season": 1, "number": 1},
        )
        assert svc._convert_trakt_history_to_custom_item("u", item) is None


def test_convert_trakt_history_success():
    with patch("app.services.trakt.sync_service.bangumi_data") as bd:
        bd.get_title_by_tmdb_id.return_value = "本地化标题"
        svc = TraktSyncService()
        item = TraktHistoryItem(
            id=1,
            watched_at="2024-06-15T12:00:00Z",
            action="scrobble",
            type="episode",
            movie=None,
            show={
                "ids": {"tmdb": 99},
                "title": "S",
                "original_title": "Orig",
                "first_aired": "2020-01-01",
            },
            episode={"ids": {"trakt": 9}, "season": 2, "number": 3},
        )
        out = svc._convert_trakt_history_to_custom_item("uid", item)
        assert out is not None
        assert out.title == "本地化标题"
        assert out.season == 2
        assert out.episode == 3
        assert out.user_name == "uid"


@pytest.mark.asyncio
async def test_start_user_sync_task_stores_result():
    svc = TraktSyncService()
    done = TraktSyncResult(
        success=True,
        message="ok",
        synced_count=0,
        skipped_count=0,
        error_count=0,
        details={},
    )
    real_sync = svc.sync_user_trakt_data
    mock_sync = AsyncMock(return_value=done)
    svc.sync_user_trakt_data = mock_sync
    try:
        with patch("app.services.trakt.sync_service.asyncio.sleep", new_callable=AsyncMock):
            tid = await svc.start_user_sync_task("u9", full_sync=False)
            inner_task = svc._active_syncs[tid]
            await asyncio.wait_for(inner_task, timeout=5.0)
        assert mock_sync.await_count >= 1
        got = svc.get_sync_result(tid)
        assert got is not None
        assert got.success is True
    finally:
        svc.sync_user_trakt_data = real_sync


def test_get_active_sync_tasks_running_only():
    svc = TraktSyncService()
    t = MagicMock()
    t.done.return_value = False
    svc._active_syncs["a"] = t
    assert svc.get_active_sync_tasks() == {"a": "running"}
    t.done.return_value = True
    assert svc.get_active_sync_tasks() == {}
