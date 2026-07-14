"""fongmi API 测试"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, fongmi
from app.services.fongmi.sync_service import FongmiSyncResult


def _fongmi_cfg(**overrides):
    base = {
        "enabled": False,
        "devices": "",
        "subnet": "",
        "auto_scan": True,
        "sync_interval": "*/3 * * * *",
        "min_percent": 80,
    }
    base.update(overrides)
    return base


@pytest.fixture
def app_fongmi():
    app = FastAPI()
    app.include_router(fongmi.router)

    async def mock_user():
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_fongmi_status_returns_config(app_fongmi):
    with patch("app.api.fongmi.config_manager") as m:
        m.get_fongmi_config.return_value = _fongmi_cfg(
            enabled=True,
            devices="192.168.1.10:9978",
            subnet="192.168.1.0/24",
            auto_scan=False,
            sync_interval="*/5 * * * *",
            min_percent=90,
        )
        transport = ASGITransport(app=app_fongmi)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/fongmi/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    data = body["data"]
    assert data["enabled"] is True
    assert data["devices"] == "192.168.1.10:9978"
    assert data["subnet"] == "192.168.1.0/24"
    assert data["auto_scan"] is False
    assert data["sync_interval"] == "*/5 * * * *"
    assert data["min_percent"] == 90


@pytest.mark.asyncio
async def test_fongmi_status_disabled_by_default(app_fongmi):
    with patch("app.api.fongmi.config_manager") as m:
        m.get_fongmi_config.return_value = _fongmi_cfg()
        transport = ASGITransport(app=app_fongmi)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/fongmi/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["data"]["enabled"] is False


@pytest.mark.asyncio
async def test_fongmi_manual_sync_disabled(app_fongmi):
    with patch("app.api.fongmi.config_manager") as m:
        m.get_fongmi_config.return_value = _fongmi_cfg(enabled=False)
        transport = ASGITransport(app=app_fongmi)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/fongmi/sync/manual")
    assert r.status_code == 400
    assert "未启用" in r.json()["detail"]


@pytest.mark.asyncio
async def test_fongmi_manual_sync_success(app_fongmi):
    result = FongmiSyncResult(
        success=True,
        message="fongmi 同步: 已同步 3, 跳过 1, 失败 0",
        synced_count=3,
        skipped_count=1,
        error_count=0,
        discovered_devices=2,
    )
    with patch("app.api.fongmi.config_manager") as m:
        m.get_fongmi_config.return_value = _fongmi_cfg(enabled=True)
        with patch(
            "app.api.fongmi.fongmi_sync_service.run_sync", new_callable=AsyncMock
        ) as rs:
            rs.return_value = result
            transport = ASGITransport(app=app_fongmi)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/api/fongmi/sync/manual")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["message"] == result.message
    assert body["data"]["synced_count"] == 3
    assert body["data"]["skipped_count"] == 1
    assert body["data"]["error_count"] == 0
    assert body["data"]["discovered_devices"] == 2
    rs.assert_awaited_once_with(ignore_enabled=False)


@pytest.mark.asyncio
async def test_fongmi_manual_sync_result_error_status(app_fongmi):
    """run_sync 返回 success=False 时，HTTP 仍是 200，但响应 status=error。"""
    result = FongmiSyncResult(
        success=False,
        message="无可用设备",
        synced_count=0,
        skipped_count=0,
        error_count=0,
        discovered_devices=0,
    )
    with patch("app.api.fongmi.config_manager") as m:
        m.get_fongmi_config.return_value = _fongmi_cfg(enabled=True)
        with patch(
            "app.api.fongmi.fongmi_sync_service.run_sync", new_callable=AsyncMock
        ) as rs:
            rs.return_value = result
            transport = ASGITransport(app=app_fongmi)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/api/fongmi/sync/manual")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["message"] == "无可用设备"


@pytest.mark.asyncio
async def test_fongmi_manual_sync_raises_500(app_fongmi):
    with patch("app.api.fongmi.config_manager") as m:
        m.get_fongmi_config.return_value = _fongmi_cfg(enabled=True)
        with patch(
            "app.api.fongmi.fongmi_sync_service.run_sync", new_callable=AsyncMock
        ) as rs:
            rs.side_effect = RuntimeError("network down")
            transport = ASGITransport(app=app_fongmi)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/api/fongmi/sync/manual")
    assert r.status_code == 500
    assert "network down" in r.json()["detail"]


@pytest.mark.asyncio
async def test_fongmi_debug_scan_success(app_fongmi):
    scan_data = {
        "discovered_devices": 2,
        "devices": [
            {"ip": "192.168.1.10", "port": 9978, "name": "客厅盒"},
            {"ip": "192.168.1.11", "port": 9978, "name": "卧室盒"},
        ],
        "media": [],
    }
    with patch(
        "app.api.fongmi.fongmi_sync_service.debug_scan", new_callable=AsyncMock
    ) as ds:
        ds.return_value = scan_data
        transport = ASGITransport(app=app_fongmi)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/fongmi/debug/scan")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert "2" in body["message"]
    assert body["data"]["discovered_devices"] == 2
    ds.assert_awaited_once()


@pytest.mark.asyncio
async def test_fongmi_debug_scan_timeout(app_fongmi):
    async def _hang():
        await asyncio.sleep(60)

    with patch(
        "app.api.fongmi.fongmi_sync_service.debug_scan", new_callable=AsyncMock
    ) as ds:
        ds.side_effect = _hang
        # 缩短 wait_for 超时以加速测试
        with patch(
            "app.api.fongmi.asyncio.wait_for",
            AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            transport = ASGITransport(app=app_fongmi)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post("/api/fongmi/debug/scan")
    assert r.status_code == 504
    assert "超时" in r.json()["detail"]


@pytest.mark.asyncio
async def test_fongmi_debug_scan_exception(app_fongmi):
    with patch(
        "app.api.fongmi.fongmi_sync_service.debug_scan", new_callable=AsyncMock
    ) as ds:
        ds.side_effect = RuntimeError("scan boom")
        transport = ASGITransport(app=app_fongmi)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/fongmi/debug/scan")
    assert r.status_code == 500
    assert "scan boom" in r.json()["detail"]


@pytest.mark.asyncio
async def test_fongmi_debug_sync_one_success(app_fongmi):
    sync_data = {
        "before": {"message": "未同步"},
        "after": {"message": "已标记看过 第3集", "episode": 3},
    }
    with patch(
        "app.api.fongmi.fongmi_sync_service.debug_sync_one", new_callable=AsyncMock
    ) as ds:
        ds.return_value = sync_data
        transport = ASGITransport(app=app_fongmi)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/fongmi/debug/sync",
                json={
                    "device_ip": "192.168.1.10",
                    "device_port": 9978,
                    "device_name": "客厅盒",
                },
            )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["message"] == "已标记看过 第3集"
    assert body["data"]["after"]["episode"] == 3
    ds.assert_awaited_once_with(
        device_ip="192.168.1.10",
        device_port=9978,
        device_name="客厅盒",
    )


@pytest.mark.asyncio
async def test_fongmi_debug_sync_one_timeout(app_fongmi):
    async def _hang(*args, **kwargs):
        await asyncio.sleep(60)

    with patch(
        "app.api.fongmi.fongmi_sync_service.debug_sync_one", new_callable=AsyncMock
    ) as ds:
        ds.side_effect = _hang
        with patch(
            "app.api.fongmi.asyncio.wait_for",
            AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            transport = ASGITransport(app=app_fongmi)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post(
                    "/api/fongmi/debug/sync",
                    json={"device_ip": "10.0.0.1", "device_port": 9978},
                )
    assert r.status_code == 504
    assert "超时" in r.json()["detail"]


@pytest.mark.asyncio
async def test_fongmi_debug_sync_one_exception(app_fongmi):
    with patch(
        "app.api.fongmi.fongmi_sync_service.debug_sync_one", new_callable=AsyncMock
    ) as ds:
        ds.side_effect = RuntimeError("bangumi 502")
        transport = ASGITransport(app=app_fongmi)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/fongmi/debug/sync",
                json={"device_ip": "10.0.0.1"},
            )
    assert r.status_code == 500
    assert "bangumi 502" in r.json()["detail"]


@pytest.mark.asyncio
async def test_fongmi_debug_sync_one_invalid_body(app_fongmi):
    """缺少必填字段 device_ip → 422 校验失败。"""
    transport = ASGITransport(app=app_fongmi)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/fongmi/debug/sync", json={"device_port": 9978})
    assert r.status_code == 422


@pytest.mark.filterwarnings("ignore:coroutine .*:RuntimeWarning")
@pytest.mark.asyncio
async def test_fongmi_debug_sync_one_default_port(app_fongmi):
    """未传 device_port 时使用默认 9978。"""
    sync_data = {"after": {"message": "ok"}}
    with patch(
        "app.api.fongmi.fongmi_sync_service.debug_sync_one", new_callable=AsyncMock
    ) as ds:
        ds.return_value = sync_data
        transport = ASGITransport(app=app_fongmi)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/fongmi/debug/sync", json={"device_ip": "192.168.1.10"}
            )
    assert r.status_code == 200
    ds.assert_awaited_once_with(
        device_ip="192.168.1.10",
        device_port=9978,
        device_name="",
    )
