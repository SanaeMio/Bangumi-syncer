"""升级 API 端点测试"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, upgrade


@pytest.fixture
def app():
    """创建测试应用"""
    app = FastAPI()
    app.include_router(upgrade.router)
    return app


@pytest.fixture
def auth_app(app):
    """带认证的测试应用"""

    async def _user():
        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_flexible] = _user
    return app


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


@pytest.fixture
def auth_transport(auth_app):
    return ASGITransport(app=auth_app)


class TestUpgradeStatus:
    """升级状态端点测试"""

    @pytest.mark.asyncio
    async def test_status_requires_auth(self, transport):
        """未认证时应返回 401"""
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/app/upgrade/status")

        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_status_direct_install(self, auth_transport):
        """直装模式应返回正确状态"""
        with patch("app.api.upgrade.upgrade_service") as mock_service:
            mock_service.is_upgrade_capable.return_value = True
            mock_service.is_upgrade_in_progress = False

            with patch("app.utils.docker_helper.docker_helper") as mock_docker:
                mock_docker.is_docker = False

                with patch("app.api.upgrade.get_version", return_value="1.0.0"):
                    async with AsyncClient(
                        transport=auth_transport, base_url="http://test"
                    ) as ac:
                        r = await ac.get("/api/app/upgrade/status")

        assert r.status_code == 200
        body = r.json()
        assert body["environment"] == "direct"
        assert body["upgrade_capable"] is True
        assert body["upgrade_in_progress"] is False
        assert body["current_version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_status_docker(self, auth_transport):
        """Docker 模式应返回正确状态"""
        with patch("app.api.upgrade.upgrade_service") as mock_service:
            mock_service.is_upgrade_capable.return_value = False
            mock_service.is_upgrade_in_progress = False

            with patch("app.utils.docker_helper.docker_helper") as mock_docker:
                mock_docker.is_docker = True

                with patch("app.api.upgrade.get_version", return_value="1.0.0"):
                    async with AsyncClient(
                        transport=auth_transport, base_url="http://test"
                    ) as ac:
                        r = await ac.get("/api/app/upgrade/status")

        assert r.status_code == 200
        body = r.json()
        assert body["environment"] == "docker"
        assert body["upgrade_capable"] is False


class TestTriggerUpgrade:
    """触发升级端点测试"""

    @pytest.mark.asyncio
    async def test_trigger_requires_auth(self, transport):
        """未认证时应返回 401"""
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/app/upgrade", json={})

        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_trigger_not_capable(self, auth_transport):
        """不支持升级时应返回 400"""
        with patch("app.api.upgrade.upgrade_service") as mock_service:
            mock_service.is_upgrade_capable.return_value = False

            async with AsyncClient(
                transport=auth_transport, base_url="http://test"
            ) as ac:
                r = await ac.post("/api/app/upgrade", json={})

        assert r.status_code == 400
        assert "不支持" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_trigger_already_in_progress(self, auth_transport):
        """已有升级进行中时应返回 409"""
        with patch("app.api.upgrade.upgrade_service") as mock_service:
            mock_service.is_upgrade_capable.return_value = True
            mock_service.is_upgrade_in_progress = True

            async with AsyncClient(
                transport=auth_transport, base_url="http://test"
            ) as ac:
                r = await ac.post("/api/app/upgrade", json={})

        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_trigger_success(self, auth_transport):
        """成功触发升级应返回 upgrade_id"""
        with patch("app.api.upgrade.upgrade_service") as mock_service:
            mock_service.is_upgrade_capable.return_value = True
            mock_service.is_upgrade_in_progress = False
            mock_service.start_upgrade = AsyncMock(return_value="abc123")

            async with AsyncClient(
                transport=auth_transport, base_url="http://test"
            ) as ac:
                r = await ac.post("/api/app/upgrade", json={})

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "started"
        assert body["upgrade_id"] == "abc123"


class TestUpgradeProgress:
    """升级进度端点测试"""

    @pytest.mark.asyncio
    async def test_progress_requires_auth(self, transport):
        """未认证时应返回 401"""
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/app/upgrade/progress?upgrade_id=abc")

        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_progress_not_found(self, auth_transport):
        """不存在的升级任务应返回 404"""
        with patch("app.api.upgrade.upgrade_service") as mock_service:
            mock_service.get_progress_queue.return_value = None
            mock_service.get_progress.return_value = None

            async with AsyncClient(
                transport=auth_transport, base_url="http://test"
            ) as ac:
                r = await ac.get("/api/app/upgrade/progress?upgrade_id=abc")

        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_progress_success(self, auth_transport):
        """应通过 SSE 推送正确的进度信息"""
        import asyncio
        import json as json_mod

        from app.services.upgrade_service import UpgradeProgress, UpgradeStage

        # 使用 done 阶段，SSE 会立即关闭连接
        progress = UpgradeProgress(
            upgrade_id="abc",
            stage=UpgradeStage.DONE,
            percent=100,
            message="升级完成",
        )

        queue = asyncio.Queue(maxsize=100)
        queue.put_nowait(progress)

        with patch("app.api.upgrade.upgrade_service") as mock_service:
            mock_service.get_progress_queue.return_value = queue

            async with AsyncClient(
                transport=auth_transport, base_url="http://test"
            ) as ac:
                async with ac.stream(
                    "GET", "/api/app/upgrade/progress?upgrade_id=abc"
                ) as r:
                    assert r.status_code == 200
                    body = b""
                    async for chunk in r.aiter_bytes():
                        body += chunk

        text = body.decode("utf-8")
        assert "event: progress" in text
        for line in text.split("\n"):
            if line.startswith("data: "):
                data = json_mod.loads(line[6:])
                assert data["stage"] == "done"
                assert data["percent"] == 100
                assert data["message"] == "升级完成"
                assert data["error"] is None
                break
        else:
            pytest.fail("未找到 SSE data 行")


class TestRestartEndpoint:
    """重启端点测试"""

    @pytest.mark.asyncio
    async def test_restart_requires_auth(self, transport):
        """未认证时应返回 401"""
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/app/upgrade/restart")

        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_restart_returns_restarting(self, auth_transport):
        """应返回 restarting 状态"""
        with patch("app.api.upgrade.asyncio"):
            async with AsyncClient(
                transport=auth_transport, base_url="http://test"
            ) as ac:
                r = await ac.post("/api/app/upgrade/restart")

        assert r.status_code == 200
        assert r.json()["status"] == "restarting"
