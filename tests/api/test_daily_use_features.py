"""日常常用功能：健康检查、Webhook 密钥、代理诊断、日志页、备份按日期清理。"""

import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api import config, deps, health, logs, proxy


@pytest.fixture
def app_auth():
    app = FastAPI()

    async def mock_user():
        return {"username": "admin", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


def test_health_returns_version():
    app = FastAPI()
    app.include_router(health.router)
    with patch("app.api.health.get_version", return_value="9.9.9-test"):
        r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "version": "9.9.9-test"}


@pytest.mark.asyncio
async def test_refresh_webhook_key_success(app_auth):
    app_auth.include_router(config.router)
    with patch(
        "app.api.config.security_manager.refresh_webhook_key",
        return_value="new_whk_value",
    ):
        transport = ASGITransport(app=app_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/config/auth/refresh-webhook-key")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["data"]["webhook_key"] == "new_whk_value"


@pytest.mark.asyncio
async def test_refresh_webhook_key_returns_500_on_error(app_auth):
    app_auth.include_router(config.router)
    with patch(
        "app.api.config.security_manager.refresh_webhook_key",
        side_effect=RuntimeError("ini locked"),
    ):
        transport = ASGITransport(app=app_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/config/auth/refresh-webhook-key")
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_proxy_environment_returns_data(app_auth):
    app_auth.include_router(proxy.router)
    fake = {"in_docker": False, "platform": "test"}
    with patch(
        "app.utils.docker_helper.docker_helper.get_environment_info",
        return_value=fake,
    ):
        transport = ASGITransport(app=app_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/proxy/environment")
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert r.json()["data"] == fake


@pytest.mark.asyncio
async def test_proxy_suggestions_returns_data(app_auth):
    app_auth.include_router(proxy.router)
    with patch(
        "app.utils.docker_helper.docker_helper.get_environment_info",
        return_value={"x": 1},
    ):
        with patch(
            "app.utils.docker_helper.docker_helper.get_proxy_suggestions",
            return_value=[{"url": "http://127.0.0.1:7890"}],
        ):
            transport = ASGITransport(app=app_auth)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.get("/api/proxy/suggestions?port=7890")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["data"]["suggestions"]


@pytest.mark.asyncio
async def test_proxy_test_connectivity(app_auth):
    app_auth.include_router(proxy.router)
    with patch(
        "app.utils.docker_helper.docker_helper.test_proxy_connectivity",
        return_value={"ok": True},
    ):
        transport = ASGITransport(app=app_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/proxy/test",
                json={"proxy_url": "http://127.0.0.1:7890"},
            )
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is True


@pytest.mark.asyncio
async def test_logs_api_when_log_file_missing_returns_empty(app_auth):
    app_auth.include_router(logs.router)
    with patch("app.api.logs.config_manager.get_config", return_value="./no_such_log.txt"):
        with patch("app.api.logs.os.path.exists", return_value=False):
            transport = ASGITransport(app=app_auth)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.get("/api/logs")
    assert r.status_code == 200
    payload = r.json()
    assert payload["data"]["content"] == ""
    assert payload["data"]["stats"]["lines"] == 0


@pytest.mark.asyncio
async def test_cleanup_config_backups_by_date_removes_old_only(
    app_auth, tmp_path, monkeypatch
):
    """按日期策略：只删早于 keep_days 的备份（配置页常用）。"""
    monkeypatch.chdir(tmp_path)
    rel = tmp_path / "config_backups"
    rel.mkdir()
    old_f = rel / "old.ini"
    new_f = rel / "new.ini"
    old_f.write_text("a", encoding="utf-8")
    new_f.write_text("b", encoding="utf-8")
    os.utime(old_f, (time.time() - 40 * 24 * 3600,) * 2)
    os.utime(new_f, (time.time() - 1 * 24 * 3600,) * 2)

    app_auth.include_router(config.router)
    transport = ASGITransport(app=app_auth)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/api/config/backups/cleanup",
            json={"strategy": "date", "keep_days": 30},
        )

    assert r.status_code == 200
    assert "清理" in r.json().get("message", "")
    assert not old_f.exists()
    assert new_f.exists()
