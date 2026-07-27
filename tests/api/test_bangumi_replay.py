"""Bangumi Replay API 测试

验证：
1. GET /api/bangumi_replay/status 返回调度器状态
2. GET /api/bangumi_replay/queue 返回队列列表（分页）
3. POST /api/bangumi_replay/replay 触发批量补发
4. DELETE /api/bangumi_replay/queue/{id} 删除单条
5. POST /api/bangumi_replay/probe 探测 API 可达性
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import bangumi_replay, deps


@pytest.fixture
def app_with_auth():
    app = FastAPI()
    app.include_router(bangumi_replay.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def mock_scheduler():
    scheduler = MagicMock()
    scheduler.get_status.return_value = {
        "enabled": True,
        "cron": "*/10 * * * *",
        "running": True,
        "queue_stats": {"pending": 3, "synced": 10, "abandoned": 1},
    }
    scheduler._probe_api = AsyncMock(return_value=True)
    with patch(
        "app.services.bangumi_replay_scheduler.bangumi_replay_scheduler", scheduler
    ):
        yield scheduler


@pytest.fixture
def mock_database():
    db = MagicMock()
    db.get_pending_sync_queue.return_value = {
        "records": [
            {
                "id": 1,
                "title": "番剧A",
                "subject_id": "111",
                "status": "pending",
                "attempts": 0,
            }
        ],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }
    db.get_pending_sync_record_by_id.return_value = {
        "id": 1,
        "title": "番剧A",
        "subject_id": "111",
        "payload_json": "{}",
        "status": "pending",
    }
    db.get_pending_sync_stats.return_value = {
        "pending": 3,
        "synced": 10,
        "abandoned": 1,
    }
    db.delete_pending_sync_record.return_value = True
    db.mark_pending_sync_synced.return_value = True
    db.increment_pending_sync_attempts.return_value = True
    # 同时 patch 源模块与路由模块的本地引用：
    # 路由用 `from ..core.database import database_manager` 已建立本地引用，
    # 仅 patch 源模块不会替换路由作用域内的引用，会导致真实 DB 被调用。
    with (
        patch("app.core.database.database_manager", db),
        patch("app.api.bangumi_replay.database_manager", db),
    ):
        yield db


@pytest.fixture
def mock_sync_service():
    svc = MagicMock()
    svc.replay_pending_batch.return_value = {
        "total": 3,
        "success": 2,
        "failed": 1,
        "still_unreachable": 0,
    }
    svc.replay_pending_item.return_value = {
        "success": True,
        "message": "补发成功 mark_status=1",
        "should_mark_synced": True,
        "mark_status": 1,
    }
    with patch("app.services.sync_service.sync_service", svc):
        yield svc


class TestReplayStatusEndpoint:
    """GET /api/bangumi_replay/status"""

    @pytest.mark.asyncio
    async def test_returns_status(self, app_with_auth, mock_scheduler):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi_replay/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["cron"] == "*/10 * * * *"
        assert data["running"] is True
        assert data["queue_stats"]["pending"] == 3


class TestQueueListEndpoint:
    """GET /api/bangumi_replay/queue"""

    @pytest.mark.asyncio
    async def test_returns_queue_list(
        self, app_with_auth, mock_scheduler, mock_database
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi_replay/queue?page=1&limit=20")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["data"]["total"] == 1
        assert data["data"]["records"][0]["subject_id"] == "111"

    @pytest.mark.asyncio
    async def test_returns_single_record(
        self, app_with_auth, mock_scheduler, mock_database
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi_replay/queue/1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["data"]["record"]["id"] == 1

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(
        self, app_with_auth, mock_scheduler, mock_database
    ):
        mock_database.get_pending_sync_record_by_id.return_value = None
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi_replay/queue/999")

        assert resp.status_code == 404


class TestReplayBatchEndpoint:
    """POST /api/bangumi_replay/replay"""

    @pytest.mark.asyncio
    async def test_triggers_batch_replay(
        self, app_with_auth, mock_scheduler, mock_database, mock_sync_service
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/bangumi_replay/replay?limit=20")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["data"]["total"] == 3
        assert data["data"]["success"] == 2


class TestReplaySingleEndpoint:
    """POST /api/bangumi_replay/replay/{id}"""

    @pytest.mark.asyncio
    async def test_triggers_single_replay(
        self, app_with_auth, mock_scheduler, mock_database, mock_sync_service
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/bangumi_replay/replay/1")

        assert resp.status_code == 200
        # 成功时应调用 mark_synced
        mock_database.mark_pending_sync_synced.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(
        self, app_with_auth, mock_scheduler, mock_database, mock_sync_service
    ):
        mock_database.get_pending_sync_record_by_id.return_value = None
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/bangumi_replay/replay/999")

        assert resp.status_code == 404


class TestDeleteEndpoint:
    """DELETE /api/bangumi_replay/queue/{id}"""

    @pytest.mark.asyncio
    async def test_deletes_record(self, app_with_auth, mock_scheduler, mock_database):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.delete("/api/bangumi_replay/queue/1")

        assert resp.status_code == 200
        mock_database.delete_pending_sync_record.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(
        self, app_with_auth, mock_scheduler, mock_database
    ):
        mock_database.delete_pending_sync_record.return_value = False
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.delete("/api/bangumi_replay/queue/999")

        assert resp.status_code == 404


class TestProbeEndpoint:
    """POST /api/bangumi_replay/probe"""

    @pytest.mark.asyncio
    async def test_returns_reachable(self, app_with_auth, mock_scheduler):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/bangumi_replay/probe")

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["reachable"] is True
