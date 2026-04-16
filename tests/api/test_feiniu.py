"""飞牛 API 测试"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, feiniu
from app.services.feiniu.models import FeiniuUser
from app.services.feiniu.sync_service import FeiniuSyncResult


def _feiniu_cfg(**overrides):
    base = {
        "enabled": False,
        "db_path": "",
        "user_filter": "all",
        "time_range": "all",
        "sync_interval": "*/15 * * * *",
        "min_percent": 85,
        "limit": 100,
    }
    base.update(overrides)
    return base


@pytest.fixture
def app_feiniu():
    app = FastAPI()
    app.include_router(feiniu.router)

    async def mock_user():
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feiniu_status(app_feiniu):
    with patch("app.api.feiniu.config_manager") as m:
        m.get_feiniu_config.return_value = _feiniu_cfg()
        transport = ASGITransport(app=app_feiniu)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/feiniu/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["data"]["enabled"] is False


@pytest.mark.asyncio
async def test_feiniu_manual_disabled(app_feiniu):
    with patch("app.api.feiniu.config_manager") as m:
        m.get_feiniu_config.return_value = _feiniu_cfg(
            enabled=False, db_path="/nope.db"
        )
        transport = ASGITransport(app=app_feiniu)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/feiniu/sync/manual")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_feiniu_manual_db_missing_when_enabled(app_feiniu):
    with patch("app.api.feiniu.config_manager") as m:
        m.get_feiniu_config.return_value = _feiniu_cfg(
            enabled=True, db_path="/nonexistent/trimmedia.db"
        )
        transport = ASGITransport(app=app_feiniu)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/feiniu/sync/manual")
    assert r.status_code == 400
    assert "路径" in r.json()["detail"] or "不存在" in r.json()["detail"]


@pytest.mark.asyncio
async def test_feiniu_manual_success(tmp_path, app_feiniu):
    dbf = tmp_path / "trimmedia.db"
    dbf.write_bytes(b"")
    result = FeiniuSyncResult(True, "飞牛同步: 已提交 2, 跳过 0, 失败 0", 2, 0, 0)
    with patch("app.api.feiniu.config_manager") as m:
        m.get_feiniu_config.return_value = _feiniu_cfg(enabled=True, db_path=str(dbf))
        with patch(
            "app.api.feiniu.feiniu_sync_service.run_sync", new_callable=AsyncMock
        ) as rs:
            rs.return_value = result
            transport = ASGITransport(app=app_feiniu)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/api/feiniu/sync/manual")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["data"]["synced_count"] == 2
    rs.assert_awaited_once_with(user_filter=None, ignore_enabled=False)


@pytest.mark.asyncio
async def test_feiniu_manual_sync_raises_500(tmp_path, app_feiniu):
    dbf = tmp_path / "e.db"
    dbf.write_bytes(b"")
    with patch("app.api.feiniu.config_manager") as m:
        m.get_feiniu_config.return_value = _feiniu_cfg(enabled=True, db_path=str(dbf))
        with patch(
            "app.api.feiniu.feiniu_sync_service.run_sync", new_callable=AsyncMock
        ) as rs:
            rs.side_effect = RuntimeError("db locked")
            transport = ASGITransport(app=app_feiniu)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/api/feiniu/sync/manual")
    assert r.status_code == 500
    assert "db locked" in r.json()["detail"]


@pytest.mark.asyncio
async def test_feiniu_manual_with_user_query(tmp_path, app_feiniu):
    dbf = tmp_path / "t.db"
    dbf.write_bytes(b"")
    result = FeiniuSyncResult(True, "ok", 0, 0, 0)
    with patch("app.api.feiniu.config_manager") as m:
        m.get_feiniu_config.return_value = _feiniu_cfg(enabled=True, db_path=str(dbf))
        with patch(
            "app.api.feiniu.feiniu_sync_service.run_sync", new_callable=AsyncMock
        ) as rs:
            rs.return_value = result
            transport = ASGITransport(app=app_feiniu)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/api/feiniu/sync/manual?user=" + "guid-abc-123")
    assert r.status_code == 200
    rs.assert_awaited_once_with(user_filter="guid-abc-123", ignore_enabled=False)


@pytest.mark.asyncio
async def test_feiniu_users_empty_when_no_db(app_feiniu):
    with patch("app.api.feiniu.config_manager") as m:
        m.get_feiniu_config.return_value = _feiniu_cfg(db_path="")
        transport = ASGITransport(app=app_feiniu)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/feiniu/users")
    assert r.status_code == 200
    assert r.json()["data"]["users"] == []


@pytest.mark.asyncio
async def test_feiniu_users_list(tmp_path, app_feiniu):
    dbf = tmp_path / "u.db"
    dbf.write_bytes(b"")
    users = [FeiniuUser(guid="g1", username="n1")]
    with patch("app.api.feiniu.config_manager") as m:
        m.get_feiniu_config.return_value = _feiniu_cfg(db_path=str(dbf))
        with patch("app.api.feiniu.list_feiniu_users", return_value=users):
            transport = ASGITransport(app=app_feiniu)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.get("/api/feiniu/users")
    assert r.status_code == 200
    data = r.json()["data"]["users"]
    assert len(data) == 1
    assert data[0]["id"] == "g1"
    assert data[0]["name"] == "n1"
