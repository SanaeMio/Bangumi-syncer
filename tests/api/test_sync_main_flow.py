"""主同步 HTTP 流程：Custom 同步模式、Webhook 认证与 Plex/Emby/Jellyfin 回退。"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, sync
from app.models.sync import CustomItem, SyncResponse


@pytest.fixture
def app_root_and_api():
    app = FastAPI()
    app.include_router(sync.root_router)
    app.include_router(sync.router)

    async def mock_user():
        return {"username": "t", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


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
async def test_custom_sync_sync_mode_returns_result(app_root_and_api, verify_ok):
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
    with patch("app.api.sync.sync_service.sync_custom_item", return_value=result) as sc:
        transport = ASGITransport(app=app_root_and_api)
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
async def test_custom_sync_sync_mode_error_sets_500(app_root_and_api, verify_ok):
    item = CustomItem(
        media_type="episode",
        title="T",
        ori_title="",
        season=1,
        episode=1,
        release_date="2024-01-01",
        user_name="u",
    )
    err = SyncResponse(status="error", message="bad", data=None)
    with patch("app.api.sync.sync_service.sync_custom_item", return_value=err):
        transport = ASGITransport(app=app_root_and_api)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/Custom",
                json=item.model_dump(),
                params={"async_mode": "false"},
            )
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_custom_webhook_auth_failed(app_root_and_api):
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
        transport = ASGITransport(app=app_root_and_api)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/Custom", json=item.model_dump())
    assert r.status_code == 202
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_plex_webhook_fallback_to_sync_when_async_fails(
    app_root_and_api, verify_ok
):
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
                transport = ASGITransport(app=app_root_and_api)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post("/Plex", content=raw)
    assert r.status_code == 200
    body = r.json()
    assert "同步模式" in body.get("message", "") or body.get("status") == "accepted"
    sync_fn.assert_called_once()


@pytest.mark.asyncio
async def test_plex_webhook_async_and_sync_both_fail_still_accepted(
    app_root_and_api, verify_ok
):
    plex = _plex_scrobble_dict()
    raw = json.dumps(plex).encode("utf-8")
    with patch("app.api.sync.extract_plex_json", return_value=json.dumps(plex)):
        with patch(
            "app.api.sync.sync_service.sync_plex_item_async",
            side_effect=RuntimeError("a"),
        ):
            with patch(
                "app.api.sync.sync_service.sync_plex_item",
                side_effect=RuntimeError("b"),
            ):
                transport = ASGITransport(app=app_root_and_api)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post("/Plex", content=raw)
    assert r.status_code == 200
    assert "错误" in r.json().get("message", "")


@pytest.mark.asyncio
async def test_emby_body_parsed_with_ast_literal_eval(app_root_and_api, verify_ok):
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
        transport = ASGITransport(app=app_root_and_api)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/Emby", content=body.encode("utf-8"))
    assert r.status_code == 200
    assert r.json().get("status") == "accepted"


@pytest.mark.asyncio
async def test_emby_invalid_wrapper_returns_error(app_root_and_api, verify_ok):
    transport = ASGITransport(app=app_root_and_api)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/Emby", content=b"not-braced-json")
    assert r.status_code == 200
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_jellyfin_webhook_fallback_sync(app_root_and_api, verify_ok):
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
            transport = ASGITransport(app=app_root_and_api)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/Jellyfin", content=raw)
    assert r.status_code == 200
    sync_fn.assert_called_once()


@pytest.mark.asyncio
async def test_test_sync_empty_title_400(app_root_and_api):
    transport = ASGITransport(app=app_root_and_api)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/api/test-sync",
            json={"title": "", "season": 1, "episode": 1},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_test_sync_browser_uses_sync_mode(app_root_and_api):
    """浏览器 UA 时 async_mode 默认为同步，直接调 sync_custom_item。"""
    with patch(
        "app.api.sync.sync_service.sync_custom_item",
        return_value=SyncResponse(status="success", message="done"),
    ) as sc:
        transport = ASGITransport(app=app_root_and_api)
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
async def test_test_sync_invalid_json_500(app_root_and_api):
    transport = ASGITransport(app=app_root_and_api)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/api/test-sync",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_retry_sync_record_success_updates_db(app_root_and_api):
    with patch("app.api.sync.database_manager") as dbm:
        dbm.get_sync_record_by_id.return_value = {
            "id": 7,
            "title": "Retry Show",
            "ori_title": "",
            "season": 1,
            "episode": 4,
            "status": "error",
            "user_name": "u",
            "source": "plex",
        }
        ok = SyncResponse(status="success", message="marked", data={})
        with patch("app.api.sync.sync_service.sync_custom_item", return_value=ok):
            transport = ASGITransport(app=app_root_and_api)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/api/records/7/retry")
    assert r.status_code == 200
    dbm.update_sync_record_status.assert_called_once()
    call_kw = dbm.update_sync_record_status.call_args[1]
    assert call_kw["status"] == "retried"


@pytest.mark.asyncio
async def test_get_sync_records_db_error(app_root_and_api):
    with patch(
        "app.api.sync.database_manager.get_sync_records", side_effect=RuntimeError("db")
    ):
        transport = ASGITransport(app=app_root_and_api)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/records")
    assert r.status_code == 500
