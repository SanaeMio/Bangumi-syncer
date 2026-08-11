"""
同步API测试
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, sync
from app.models.sync import CustomItem, SyncResponse


@pytest.fixture
def app_with_auth():
    """创建带有认证禁用的测试应用"""
    app = FastAPI()
    app.include_router(sync.root_router)
    app.include_router(sync.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def mock_sync_service():
    """模拟同步服务

    同步业务方法（sync_custom_item 等）使用 mock；
    查询方法（get_sync_records 等）透传到真实 sync_service 实例，
    以便与 mock_database_manager 配合验证数据库调用。
    """
    from app.services.sync_service import sync_service as real_sync_service

    with patch("app.api.sync.sync_service") as mock_service:
        mock_service.sync_custom_item_async = AsyncMock(return_value="test_task_123")

        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.model_dump.return_value = {
            "status": "success",
            "message": "同步成功",
        }
        mock_service.sync_custom_item.return_value = mock_result

        mock_service.get_sync_task_status.return_value = {
            "task_id": "test_task_123",
            "status": "completed",
        }

        mock_service._sync_tasks = {"task_1": {}, "task_2": {}}
        mock_service.cleanup_old_tasks = MagicMock()

        # 查询方法透传到真实实例（真实实例内部调用 mock_database_manager）
        mock_service.get_sync_records = real_sync_service.get_sync_records
        mock_service.get_sync_record_by_id = real_sync_service.get_sync_record_by_id
        mock_service.update_sync_record_status = (
            real_sync_service.update_sync_record_status
        )
        mock_service.mark_pending_sync_synced_by_sync_record_id = (
            real_sync_service.mark_pending_sync_synced_by_sync_record_id
        )
        # _build_retry_item_from_record 仅读 record dict，透传以返回真实 CustomItem
        # （否则 MagicMock 无法被 f-string :02d 格式化，导致重试接口 500）
        mock_service._build_retry_item_from_record = (
            real_sync_service._build_retry_item_from_record
        )
        mock_service.get_sync_stats = real_sync_service.get_sync_stats
        mock_service.get_heatmap_stats = real_sync_service.get_heatmap_stats

        yield mock_service


@pytest.fixture
def mock_custom_sync_service():
    """模拟自定义 Webhook 同步服务"""
    with patch("app.api.sync.custom_sync_service") as mock_service:
        mock_service.sync_item_async = AsyncMock(return_value="test_task_123")

        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.model_dump.return_value = {
            "status": "success",
            "message": "同步成功",
        }
        mock_service.sync_item.return_value = mock_result

        yield mock_service


@pytest.fixture
def mock_database_manager():
    """模拟数据库管理器"""
    with patch("app.services.sync_service.database_manager") as mock_db:
        mock_db.get_sync_records.return_value = {
            "records": [
                {
                    "id": 1,
                    "title": "Test Show",
                    "season": 1,
                    "episode": 5,
                    "status": "success",
                }
            ],
            "total": 1,
        }
        mock_db.get_sync_record_by_id.return_value = {
            "id": 1,
            "title": "Test Show",
            "season": 1,
            "episode": 5,
            "status": "success",
            "source": "custom",
        }
        mock_db.get_sync_stats.return_value = {
            "total": 100,
            "success": 90,
            "error": 10,
        }
        mock_db.update_sync_record_status.return_value = True

        yield mock_db


# ========== 基础功能测试 ==========


@pytest.mark.asyncio
async def test_custom_sync_async_mode(
    app_with_auth, mock_custom_sync_service, mock_database_manager
):
    """测试自定义同步异步模式"""
    item = CustomItem(
        media_type="episode",
        title="Test Show",
        season=1,
        episode=5,
        release_date="2024-01-01",
        user_name="test_user",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/Custom",
            json=item.model_dump(),
            params={"async_mode": "true"},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert "task_id" in data


@pytest.mark.asyncio
async def test_custom_sync_with_key(mock_custom_sync_service, mock_database_manager):
    """测试带密钥的自定义同步接口"""
    app = FastAPI()
    app.include_router(sync.root_router)
    app.include_router(sync.router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=True
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Custom/test_key",
                json={
                    "media_type": "episode",
                    "title": "Test",
                    "season": 1,
                    "episode": 1,
                    "release_date": "2024-01-01",
                    "user_name": "user",
                },
                params={"async_mode": "true"},
            )
            assert response.status_code == 202


@pytest.mark.asyncio
async def test_get_sync_status(app_with_auth, mock_sync_service, mock_database_manager):
    """测试获取同步任务状态"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/sync/status/test_task_123")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


@pytest.mark.asyncio
async def test_get_sync_status_not_found(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取不存在的任务状态"""
    mock_sync_service.get_sync_task_status.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/sync/status/nonexistent")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_sync_status_exception(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取同步任务状态时抛出异常"""
    mock_sync_service.get_sync_task_status.side_effect = RuntimeError("db error")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/sync/status/test")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_list_sync_tasks(app_with_auth, mock_sync_service, mock_database_manager):
    """测试列出同步任务"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/sync/tasks")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "tasks" in data["data"]


@pytest.mark.asyncio
async def test_list_sync_tasks_exception(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试列出同步任务时抛出异常"""
    mock_sync_service.cleanup_old_tasks.side_effect = RuntimeError("fail")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/sync/tasks")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_sync_records(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取同步记录"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/records")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "poster_url" not in data["data"]["records"][0]


@pytest.mark.asyncio
async def test_get_sync_records_include_poster(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试 records 批量附带 poster_url"""
    mock_database_manager.get_sync_records.return_value = {
        "records": [
            {
                "id": 1,
                "title": "Test Show",
                "season": 1,
                "episode": 5,
                "status": "success",
                "subject_id": 123,
            },
            {
                "id": 2,
                "title": "No Subject",
                "season": 1,
                "episode": 1,
                "status": "success",
                "subject_id": None,
            },
        ],
        "total": 2,
    }

    with patch(
        "app.api.sync.get_poster_urls",
        new_callable=AsyncMock,
        return_value={123: "https://img-proxy.example.com/pic/cover/s/a/b/c.jpg"},
    ) as mock_posters:
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/records?include_poster=true")

    assert response.status_code == 200
    data = response.json()
    records = data["data"]["records"]
    assert (
        records[0]["poster_url"]
        == "https://img-proxy.example.com/pic/cover/s/a/b/c.jpg"
    )
    assert records[1]["poster_url"] is None
    mock_posters.assert_awaited_once_with([123])


@pytest.mark.asyncio
async def test_get_sync_records_with_match_filters(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试同步记录支持匹配方式与放送形态筛选"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/records?match_method=custom_mapping&match_platform=TV"
        )

    assert response.status_code == 200
    mock_database_manager.get_sync_records.assert_called()
    call_kwargs = mock_database_manager.get_sync_records.call_args.kwargs
    assert call_kwargs.get("match_method") == "custom_mapping"
    assert call_kwargs.get("match_platform") == "TV"


@pytest.mark.asyncio
async def test_get_sync_record_by_id(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取单个同步记录"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/records/1")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


@pytest.mark.asyncio
async def test_get_sync_record_not_found(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取不存在的记录"""
    mock_database_manager.get_sync_record_by_id.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/records/999")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_sync_record_exception(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取记录详情时抛出异常"""
    mock_database_manager.get_sync_record_by_id.side_effect = RuntimeError("fail")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/records/1")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_sync_stats(app_with_auth, mock_sync_service, mock_database_manager):
    """测试获取同步统计"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


@pytest.mark.asyncio
async def test_get_sync_stats_exception(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取同步统计时抛出异常"""
    mock_database_manager.get_sync_stats.side_effect = RuntimeError("fail")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/stats")
        assert response.status_code == 500


# ========== Webhook 测试 ==========


@pytest.mark.asyncio
async def test_plex_webhook(mock_sync_service):
    """测试 Plex webhook 接口"""
    app = FastAPI()
    app.include_router(sync.root_router)

    plex_data = {
        "event": "media.play",
        "Account": {"title": "test_user"},
        "Metadata": {
            "title": "Test Show",
            "type": "episode",
            "grandparentTitle": "Test Series",
            "index": 5,
            "parentIndex": 1,
        },
    }

    with patch("app.api.sync.extract_plex_json") as mock_extract:
        mock_extract.return_value = json.dumps(plex_data)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Plex",
                content=json.dumps(plex_data),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "accepted"


@pytest.mark.asyncio
async def test_plex_webhook_with_key(mock_sync_service):
    """测试带密钥的 Plex webhook"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with (
        patch(
            "app.api.sync._verify_webhook_auth",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("app.api.sync.extract_plex_json", return_value='{"event":"play"}'),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Plex/test_key",
                content='{"event":"play"}',
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_plex_webhook_auth_failure():
    """测试 Plex webhook 认证失败"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=False
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Plex/bad_key",
                content=b"{}",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 401
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_plex_webhook_exception(mock_sync_service):
    """测试 Plex webhook 处理异常"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with (
        patch(
            "app.api.sync._verify_webhook_auth",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.api.sync.extract_plex_json", side_effect=RuntimeError("parse error")
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Plex/test_key",
                content=b"bad",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_emby_webhook(mock_sync_service):
    """测试 Emby webhook 接口"""
    app = FastAPI()
    app.include_router(sync.root_router)

    emby_data = {
        "EventType": "PlaybackStart",
        "UserName": "test_user",
        "Item": {
            "Name": "Test Show",
            "Type": "Episode",
            "SeriesName": "Test Series",
            "IndexNumber": 5,
            "ParentIndexNumber": 1,
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/Emby",
            content=json.dumps(emby_data),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"


@pytest.mark.asyncio
async def test_emby_webhook_with_key(mock_sync_service):
    """测试带密钥的 Emby webhook"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=True
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Emby/test_key",
                content=json.dumps({"EventType": "play", "Item": {"Name": "test"}}),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_emby_webhook_auth_failure():
    """测试 Emby webhook 认证失败"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=False
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Emby/bad_key",
                content=b"{}",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 401
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_emby_webhook_invalid_body():
    """测试 Emby webhook 无效请求体"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=True
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Body 不以 { 开头
            response = await client.post(
                "/Emby",
                content=b"not json at all",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_emby_webhook_malformed_dict():
    """测试 Emby webhook 无法解析的字典格式"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=True
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Emby",
                content=b"{not_valid_python}",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_emby_webhook_fallback_failure():
    """测试 Emby 异步和同步都失败"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with (
        patch(
            "app.api.sync._verify_webhook_auth",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("app.api.sync.sync_service") as mock_svc,
    ):
        mock_svc.sync_emby_item_async = AsyncMock(
            side_effect=RuntimeError("async fail")
        )
        mock_svc.sync_emby_item.side_effect = RuntimeError("sync fail")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Emby",
                content=json.dumps({"EventType": "play", "Item": {"Name": "test"}}),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "失败" in data["message"]


@pytest.mark.asyncio
async def test_jellyfin_webhook(mock_sync_service):
    """测试 Jellyfin webhook 接口"""
    app = FastAPI()
    app.include_router(sync.root_router)

    jellyfin_data = {
        "EventType": "PlaybackStart",
        "UserName": "test_user",
        "Item": {
            "Name": "Test Show",
            "Type": "Episode",
            "SeriesName": "Test Series",
            "IndexNumber": 5,
            "ParentIndexNumber": 1,
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/Jellyfin",
            content=json.dumps(jellyfin_data),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"


@pytest.mark.asyncio
async def test_jellyfin_webhook_with_key(mock_sync_service):
    """测试带密钥的 Jellyfin webhook"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=True
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Jellyfin/test_key",
                content=json.dumps({"EventType": "play", "Item": {"Name": "test"}}),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_jellyfin_webhook_auth_failure():
    """测试 Jellyfin webhook 认证失败"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=False
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Jellyfin/bad_key",
                content=b"{}",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 401
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_jellyfin_webhook_exception():
    """测试 Jellyfin webhook JSON 解析失败"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with patch(
        "app.api.sync._verify_webhook_auth", new_callable=AsyncMock, return_value=True
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Jellyfin",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_jellyfin_webhook_fallback_failure():
    """测试 Jellyfin 异步和同步都失败"""
    app = FastAPI()
    app.include_router(sync.root_router)

    with (
        patch(
            "app.api.sync._verify_webhook_auth",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("app.api.sync.sync_service") as mock_svc,
    ):
        mock_svc.sync_jellyfin_item_async = AsyncMock(
            side_effect=RuntimeError("async fail")
        )
        mock_svc.sync_jellyfin_item.side_effect = RuntimeError("sync fail")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/Jellyfin",
                content=json.dumps({"EventType": "play", "Item": {"Name": "test"}}),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "失败" in data["message"]


# ========== 自定义同步特殊路径 ==========


@pytest.mark.asyncio
async def test_custom_sync_exception(app_with_auth, mock_custom_sync_service):
    """测试自定义同步异常"""
    mock_custom_sync_service.sync_item_async = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/Custom",
            json={
                "media_type": "episode",
                "title": "Test",
                "season": 1,
                "episode": 1,
                "release_date": "2024-01-01",
                "user_name": "user",
            },
            params={"async_mode": "true"},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_custom_sync_ignored_status(app_with_auth, mock_custom_sync_service):
    """测试自定义同步忽略状态"""
    mock_result = MagicMock()
    mock_result.status = "ignored"
    mock_result.model_dump.return_value = {"status": "ignored", "message": "已忽略"}
    mock_custom_sync_service.sync_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/Custom?async_mode=false",
            json={
                "media_type": "episode",
                "title": "Test",
                "season": 1,
                "episode": 1,
                "release_date": "2024-01-01",
                "user_name": "user",
            },
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_custom_sync_error_status(app_with_auth, mock_custom_sync_service):
    """测试自定义同步错误状态"""
    mock_result = MagicMock()
    mock_result.status = "error"
    mock_result.model_dump.return_value = {"status": "error", "message": "失败"}
    mock_custom_sync_service.sync_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/Custom?async_mode=false",
            json={
                "media_type": "episode",
                "title": "Test",
                "season": 1,
                "episode": 1,
                "release_date": "2024-01-01",
                "user_name": "user",
            },
        )
        assert response.status_code == 500


# ========== 重试记录测试 ==========


@pytest.mark.asyncio
async def test_retry_sync_record_not_failed(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试重试 success 状态的记录被拒绝（只有 success 不可重试）"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "success",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_retry_sync_record_not_found(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试重试不存在的记录"""
    mock_database_manager.get_sync_record_by_id.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/999/retry")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_retry_sync_record_success(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试重试失败记录成功"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "error",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "user_name": "user",
        "media_type": "episode",
    }

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.model_dump.return_value = {"status": "success"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200
        mock_database_manager.update_sync_record_status.assert_called()
        call_args = mock_database_manager.update_sync_record_status.call_args[0]
        assert call_args[0] == 1
        assert call_args[1] == "retried"


@pytest.mark.asyncio
async def test_retry_sync_record_ignored(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试重试被忽略"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "error",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "emby",
        "user_name": "user",
        "media_type": "episode",
    }

    mock_result = MagicMock()
    mock_result.status = "ignored"
    mock_result.message = "已看过"
    mock_result.model_dump.return_value = {"status": "ignored", "message": "已看过"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200
        mock_database_manager.update_sync_record_status.assert_called()


@pytest.mark.asyncio
async def test_retry_sync_record_exception(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试重试记录时抛出异常"""
    mock_database_manager.get_sync_record_by_id.side_effect = RuntimeError("fail")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_retry_sync_record_invalid_media_type(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试重试时无效 media_type 回退为 episode"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "error",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "custom",
        "user_name": "user",
        "media_type": "invalid_type",
    }

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.model_dump.return_value = {"status": "success"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_retry_sync_record_restores_mark_watching_from_trace(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """重试应从 match_trace 恢复 sync_action=mark_watching"""
    import json

    trace = {
        "request_title": "Movie Test",
        "request_season": 1,
        "request_episode": 1,
        "request_media_type": "movie",
        "request_release_date": "2024-01-15",
        "request_sync_action": "mark_watching",
        "request_user_name": "user",
        "steps": [
            {
                "stage": "receive",
                "processed_payload": {
                    "sync_action": "mark_watching",
                    "release_date": "2024-01-15",
                },
                "raw_payload": {"event": "media.play"},
            }
        ],
    }
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "error",
        "title": "Movie Test",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "user_name": "user",
        "media_type": "movie",
        "match_trace": json.dumps(trace),
    }

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.model_dump.return_value = {"status": "success"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200

    retry_item = mock_sync_service.sync_custom_item.call_args[0][0]
    assert retry_item.sync_action == "mark_watching"
    assert retry_item.release_date == "2024-01-15"
    assert retry_item.raw_payload == {"event": "media.play"}
    assert retry_item.source == "retry-plex"


@pytest.mark.asyncio
async def test_retry_sync_record_queued_status_allowed(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """queued 状态的记录可以重试（不再限制为 error）"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "queued",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "user_name": "user",
        "media_type": "episode",
    }

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.model_dump.return_value = {"status": "success"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_retry_sync_record_ignored_status_allowed(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """ignored 状态的记录可以重试"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "ignored",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "user_name": "user",
        "media_type": "episode",
    }

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.model_dump.return_value = {"status": "success"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_retry_sync_record_retried_status_blocked(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """retried 状态（补发成功）的记录不允许重试"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "retried",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "user_name": "user",
        "media_type": "episode",
    }

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.model_dump.return_value = {"status": "success"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 400
        assert "无需重试" in response.json()["detail"]
    # 应直接拦截，不进入同步流程
    mock_sync_service.sync_custom_item.assert_not_called()


@pytest.mark.asyncio
async def test_retry_queued_cleans_pending_sync_queue(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """重试 queued 记录成功后，应清理对应的 pending_sync_queue 行"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "queued",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "user_name": "user",
        "media_type": "episode",
    }
    mock_database_manager.mark_pending_sync_synced_by_sync_record_id.return_value = 1

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.message = "mark_status=1"
    mock_result.model_dump.return_value = {"status": "success"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200

    # 应回写原记录为 retried
    mock_database_manager.update_sync_record_status.assert_called_once_with(
        1, "retried", "已重试成功: mark_status=1"
    )
    # 应清理 pending_sync_queue 中 sync_record_id=1 的 pending 行
    mock_database_manager.mark_pending_sync_synced_by_sync_record_id.assert_called_once_with(
        1
    )


@pytest.mark.asyncio
async def test_retry_queued_error_keeps_pending_sync_queue(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """重试 queued 记录结果为 error 时，不清理 pending 行（保持原状态可再次重试）"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "queued",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "user_name": "user",
        "media_type": "episode",
    }

    mock_result = MagicMock()
    mock_result.status = "error"
    mock_result.message = "still failing"
    mock_result.model_dump.return_value = {"status": "error"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200

    # error 时不回写原记录状态，也不清理 pending 行
    mock_database_manager.update_sync_record_status.assert_not_called()
    mock_database_manager.mark_pending_sync_synced_by_sync_record_id.assert_not_called()


@pytest.mark.asyncio
async def test_retry_non_queued_does_not_clean_pending(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """重试 error 状态记录成功时，不触发 pending_sync_queue 清理（无关联 pending 行）"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "status": "error",
        "title": "Test",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "user_name": "user",
        "media_type": "episode",
    }

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.message = "mark_status=1"
    mock_result.model_dump.return_value = {"status": "success"}
    mock_sync_service.sync_custom_item.return_value = mock_result

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post("/api/records/1/retry")
        assert response.status_code == 200

    mock_database_manager.update_sync_record_status.assert_called_once()
    # error 状态原记录无关联 pending 行，不应调用清理
    mock_database_manager.mark_pending_sync_synced_by_sync_record_id.assert_not_called()


# ========== 测试同步特殊路径 ==========


@pytest.mark.asyncio
async def test_test_sync_no_title(app_with_auth, mock_sync_service):
    """测试同步缺少标题"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/test-sync",
            json={"title": ""},
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_test_sync_invalid_media_type(app_with_auth, mock_database_manager):
    """测试同步无效 media_type 回退为 episode"""
    with patch("app.api.sync.sync_service") as mock_svc:
        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.model_dump.return_value = {"status": "success", "message": "ok"}
        mock_svc.sync_custom_item.return_value = mock_result

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/test-sync?async_mode=false",
                json={"title": "Test", "media_type": "invalid"},
            )
        assert response.status_code == 200
        item = mock_svc.sync_custom_item.call_args[0][0]
        assert item.media_type == "episode"


@pytest.mark.asyncio
async def test_test_sync_exception(app_with_auth, mock_sync_service):
    """测试同步时抛出异常"""
    mock_sync_service.sync_custom_item_async = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/test-sync",
            json={"title": "Test"},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_test_sync_movie_payload(app_with_auth, mock_database_manager):
    """/api/test-sync 接受 media_type=movie 且不强制 ori/release_date"""
    with patch("app.api.sync.sync_service") as mock_svc:
        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.message = "已标记为看过"
        mock_result.model_dump.return_value = {
            "status": "success",
            "message": "已标记为看过",
            "data": {"subject_id": "1", "episode_id": "2"},
        }
        mock_svc.sync_custom_item.return_value = mock_result

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/test-sync?async_mode=false",
                json={
                    "title": "剧场版",
                    "season": 1,
                    "episode": 1,
                    "user_name": "test_user",
                    "media_type": "movie",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["test_info"]["media_type"] == "movie"
        item = mock_svc.sync_custom_item.call_args[0][0]
        assert item.media_type == "movie"
        assert item.release_date == ""


# ========== 匹配记录 API 测试 ==========


@pytest.mark.asyncio
async def test_get_match_records_list(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取匹配记录列表"""
    mock_database_manager.get_match_records.return_value = {
        "records": [
            {
                "id": 1,
                "title": "Test",
                "season": 1,
                "episode": 1,
                "match_method": "api_search",
                "match_score": 0.95,
                "match_platform": "TV",
                "status": "success",
            }
        ],
        "total": 1,
        "limit": 10,
        "offset": 0,
    }
    mock_sync_service.get_match_records = mock_database_manager.get_match_records

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/match-records?page=1&limit=10")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["total"] == 1
    # 列表查询不应包含 match_trace 字段
    assert "match_trace" not in data["data"]["records"][0]


@pytest.mark.asyncio
async def test_get_match_trace_full_data(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取匹配详情：API 返回完整 record + trace（所有字段）"""
    full_trace = {
        "request_title": "鬼灭之刃",
        "request_ori_title": "Demon Slayer",
        "request_season": 2,
        "request_episode": 5,
        "request_media_type": "episode",
        "request_release_date": "2021-10-10",
        "request_user_name": "user1",
        "request_platform_hint": "",
        "normalized_title": "鬼灭之刃",
        "steps": [
            {
                "stage": "custom_mapping",
                "status": "miss",
                "subject_id": None,
                "score": None,
                "reason": "无自定义映射",
                "candidates": [],
                "elapsed_ms": 1,
            },
            {
                "stage": "bangumi_data",
                "status": "miss",
                "subject_id": None,
                "score": None,
                "reason": "本地数据未命中",
                "candidates": [],
                "elapsed_ms": 5,
            },
            {
                "stage": "api_search",
                "status": "hit",
                "subject_id": "245665",
                "score": 0.92,
                "reason": "标题完全匹配",
                "candidates": [
                    {
                        "subject_id": "245665",
                        "name": "鬼滅の刃",
                        "name_cn": "鬼灭之刃 游郭篇",
                        "score": 0.92,
                        "platform": "TV",
                        "air_date": "2021-12-05",
                        "source": "api_search",
                    },
                    {
                        "subject_id": "294993",
                        "name": "呪術廻戦",
                        "name_cn": "咒术回战",
                        "score": 0.45,
                        "platform": "TV",
                        "air_date": "2020-10-03",
                        "source": "api_search",
                    },
                ],
                "elapsed_ms": 230,
            },
        ],
        "final_subject_id": "245665",
        "final_episode_id": "1032",
        "final_match_method": "api_search",
        "final_score": 0.92,
    }
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 42,
        "timestamp": "2025-07-16 10:00:00",
        "user_name": "user1",
        "title": "鬼灭之刃",
        "ori_title": "Demon Slayer",
        "season": 2,
        "episode": 5,
        "subject_id": "245665",
        "episode_id": "1032",
        "status": "success",
        "message": "",
        "source": "plex",
        "media_type": "episode",
        "bgm_title": "鬼灭之刃 游郭篇",
        "match_method": "api_search",
        "match_score": 0.92,
        "match_platform": "TV",
        "match_trace": json.dumps(full_trace, ensure_ascii=False),
    }

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/match-records/42/trace")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    record = data["data"]["record"]
    trace = data["data"]["trace"]

    # 验证 record 全字段
    assert record["id"] == 42
    assert record["title"] == "鬼灭之刃"
    assert record["ori_title"] == "Demon Slayer"
    assert record["season"] == 2
    assert record["episode"] == 5
    assert record["subject_id"] == "245665"
    assert record["episode_id"] == "1032"
    assert record["status"] == "success"
    assert record["source"] == "plex"
    assert record["media_type"] == "episode"
    assert record["bgm_title"] == "鬼灭之刃 游郭篇"
    assert record["match_method"] == "api_search"
    assert record["match_score"] == 0.92
    assert record["match_platform"] == "TV"
    assert "match_trace" in record  # 详情查询仍返回 match_trace

    # 验证 trace 全字段
    assert trace is not None
    assert trace["request_title"] == "鬼灭之刃"
    assert trace["request_ori_title"] == "Demon Slayer"
    assert trace["request_season"] == 2
    assert trace["request_episode"] == 5
    assert trace["request_media_type"] == "episode"
    assert trace["request_release_date"] == "2021-10-10"
    assert trace["request_user_name"] == "user1"
    assert trace["normalized_title"] == "鬼灭之刃"
    assert trace["final_subject_id"] == "245665"
    assert trace["final_episode_id"] == "1032"
    assert trace["final_match_method"] == "api_search"
    assert trace["final_score"] == 0.92

    # 验证 steps 全字段
    assert len(trace["steps"]) == 3
    api_step = trace["steps"][2]
    assert api_step["stage"] == "api_search"
    assert api_step["status"] == "hit"
    assert api_step["subject_id"] == "245665"
    assert api_step["score"] == 0.92
    assert api_step["reason"] == "标题完全匹配"
    assert api_step["elapsed_ms"] == 230

    # 验证 candidates 全字段
    assert len(api_step["candidates"]) == 2
    cand = api_step["candidates"][0]
    assert cand["subject_id"] == "245665"
    assert cand["name"] == "鬼滅の刃"
    assert cand["name_cn"] == "鬼灭之刃 游郭篇"
    assert cand["score"] == 0.92
    assert cand["platform"] == "TV"
    assert cand["air_date"] == "2021-12-05"
    assert cand["source"] == "api_search"


@pytest.mark.asyncio
async def test_get_match_trace_not_found(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取不存在的匹配详情"""
    mock_database_manager.get_sync_record_by_id.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/match-records/999/trace")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_match_trace_no_trace(
    app_with_auth, mock_sync_service, mock_database_manager
):
    """测试获取无 match_trace 的旧记录（trace 应为 null）"""
    mock_database_manager.get_sync_record_by_id.return_value = {
        "id": 1,
        "title": "Old Record",
        "season": 1,
        "episode": 1,
        "status": "success",
        "source": "custom",
        "match_trace": "",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/match-records/1/trace")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["trace"] is None
    assert data["data"]["record"]["title"] == "Old Record"


# ===== 以下来源自 test_sync_main_flow.py（并入） =====


@pytest.fixture
def verify_ok():
    with patch("app.api.sync._verify_webhook_auth", new_callable=AsyncMock) as m:
        m.return_value = True
        yield m


def _plex_scrobble_dict():
    return {
        "event": "media.scrobble",
        "Account": {"title": "plex_user"},
        "Metadata": {
            "type": "episode",
            "grandparentTitle": "主站作品",
            "originalTitle": "Main",
            "parentIndex": 1,
            "index": 3,
            "originallyAvailableAt": "2024-01-01",
        },
    }


@pytest.mark.asyncio
async def test_custom_sync_sync_mode_returns_result(app_with_auth, verify_ok):
    item = CustomItem(
        media_type="episode",
        title="T",
        ori_title="",
        season=1,
        episode=1,
        release_date="2024-01-01",
        user_name="u",
    )
    result = SyncResponse(status="success", message="ok", data={})
    with patch("app.api.sync.custom_sync_service.sync_item", return_value=result) as sc:
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/Custom",
                json=item.model_dump(),
                params={"async_mode": "false"},
            )
    # 路由默认 status_code=202；同步成功分支不改为 200
    assert r.status_code == 202
    assert r.json()["status"] == "success"
    sc.assert_called_once()


@pytest.mark.asyncio
async def test_custom_webhook_auth_failed(app_with_auth):
    with patch("app.api.sync._verify_webhook_auth", new_callable=AsyncMock) as m:
        m.return_value = False
        item = CustomItem(
            media_type="episode",
            title="T",
            ori_title="",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="u",
        )
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/Custom", json=item.model_dump())
    assert r.status_code == 401
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_plex_webhook_fallback_to_sync_when_async_fails(app_with_auth, verify_ok):
    plex = _plex_scrobble_dict()
    raw = json.dumps(plex).encode("utf-8")
    with patch("app.api.sync.extract_plex_json", return_value=json.dumps(plex)):
        with patch(
            "app.api.sync.sync_service.sync_plex_item_async",
            side_effect=RuntimeError("queue full"),
        ):
            with patch(
                "app.api.sync.sync_service.sync_plex_item",
                return_value=SyncResponse(status="ignored", message="skip"),
            ) as sync_fn:
                transport = ASGITransport(app=app_with_auth)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post("/Plex", content=raw)
    assert r.status_code == 200
    body = r.json()
    assert "同步模式" in body.get("message", "") or body.get("status") == "accepted"
    sync_fn.assert_called_once()


@pytest.mark.asyncio
async def test_emby_body_parsed_with_ast_literal_eval(app_with_auth, verify_ok):
    # json.loads 失败（单引号），走 ast.literal_eval
    body = (
        "{'Event': 'item.markplayed', 'User': {'Name': 'emby_u'}, "
        "'Item': {'Type': 'Episode', 'SeriesName': 'Series', "
        "'ParentIndexNumber': 1, 'IndexNumber': 2}}"
    )
    with patch(
        "app.api.sync.sync_service.sync_emby_item_async",
        return_value="tid",
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/Emby", content=body.encode("utf-8"))
    assert r.status_code == 200
    assert r.json().get("status") == "accepted"


@pytest.mark.asyncio
async def test_jellyfin_webhook_fallback_sync(app_with_auth, verify_ok):
    jf = {
        "NotificationType": "PlaybackStop",
        "PlayedToCompletion": "True",
        "media_type": "Episode",
        "title": "Show",
        "ori_title": "O",
        "season": 1,
        "episode": 1,
        "user_name": "jf_u",
        "release_date": "2024-01-01",
    }
    raw = json.dumps(jf).encode("utf-8")
    with patch(
        "app.api.sync.sync_service.sync_jellyfin_item_async",
        side_effect=OSError("async"),
    ):
        with patch(
            "app.api.sync.sync_service.sync_jellyfin_item",
            return_value=SyncResponse(status="ignored", message="x"),
        ) as sync_fn:
            transport = ASGITransport(app=app_with_auth)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/Jellyfin", content=raw)
    assert r.status_code == 200
    sync_fn.assert_called_once()


@pytest.mark.asyncio
async def test_test_sync_browser_uses_sync_mode(app_with_auth):
    """浏览器 UA 时 async_mode 默认为同步，直接调 sync_custom_item。"""
    with patch(
        "app.api.sync.sync_service.sync_custom_item",
        return_value=SyncResponse(status="success", message="done"),
    ) as sc:
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/test-sync",
                json={
                    "title": "Browser Show",
                    "season": 1,
                    "episode": 2,
                    "user_name": "u1",
                },
                headers={"User-Agent": "Mozilla/5.0 Chrome/120"},
            )
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    sc.assert_called_once()


@pytest.mark.asyncio
async def test_test_sync_invalid_json_500(app_with_auth):
    transport = ASGITransport(app=app_with_auth)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/api/test-sync",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
    assert r.status_code == 500


def test_build_retry_item_preserves_all_media_types():
    """_build_retry_item 保留所有合法 media_type，无效类型回退为 episode"""
    from app.api.sync import _build_retry_item

    for media_type in ("episode", "movie", "ova", "oad", "real_action"):
        record = {
            "title": "测试",
            "season": 1,
            "episode": 1,
            "user_name": "u",
            "media_type": media_type,
        }
        item = _build_retry_item(record, "retry-custom")
        assert item.media_type == media_type

    # 无效类型仍回退为 episode
    item = _build_retry_item({"media_type": "invalid", "title": "t"}, "retry-x")
    assert item.media_type == "episode"


def test_build_retry_item_restores_fields_from_match_trace():
    """_build_retry_item 优先从 match_trace 还原原始请求（含日期与 sync_action）"""
    import json

    from app.api.sync import _build_retry_item

    trace = {
        "request_title": "Trace Title",
        "request_ori_title": "Ori",
        "request_season": 2,
        "request_episode": 5,
        "request_media_type": "episode",
        "request_release_date": "2024-03-01",
        "request_sync_action": "mark_watching",
        "request_user_name": "trace_user",
        "steps": [
            {
                "stage": "receive",
                "processed_payload": {
                    "title": "Trace Title",
                    "release_date": "2024-03-01",
                    "sync_action": "mark_watching",
                },
                "raw_payload": {"event": "play"},
            }
        ],
    }
    record = {
        "title": "Column Title",
        "ori_title": "Col Ori",
        "season": 1,
        "episode": 1,
        "user_name": "col_user",
        "media_type": "movie",
        "match_trace": json.dumps(trace),
    }
    item = _build_retry_item(record, "retry-plex")
    assert item.title == "Trace Title"
    assert item.ori_title == "Ori"
    assert item.season == 2
    assert item.episode == 5
    assert item.media_type == "episode"
    assert item.release_date == "2024-03-01"
    assert item.sync_action == "mark_watching"
    assert item.user_name == "trace_user"
    assert item.source == "retry-plex"
    assert item.raw_payload == {"event": "play"}


def test_build_retry_item_falls_back_to_record_columns_without_trace():
    """无 match_trace 时回退 sync_records 列"""
    from app.api.sync import _build_retry_item

    record = {
        "title": "Column Title",
        "ori_title": "Col Ori",
        "season": 3,
        "episode": 7,
        "user_name": "col_user",
        "media_type": "ova",
    }
    item = _build_retry_item(record, "retry-emby")
    assert item.title == "Column Title"
    assert item.ori_title == "Col Ori"
    assert item.season == 3
    assert item.episode == 7
    assert item.release_date == ""
    assert item.sync_action is None
    assert item.raw_payload is None


@pytest.mark.asyncio
async def test_get_sync_records_db_error(app_with_auth):
    with patch(
        "app.services.sync_service.database_manager.get_sync_records",
        side_effect=RuntimeError("db"),
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/records")
    assert r.status_code == 500
