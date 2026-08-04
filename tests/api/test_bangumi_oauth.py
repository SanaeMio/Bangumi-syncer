"""Bangumi OAuth API 接口测试。

覆盖：
- GET /api/oauth/bangumi/start 的 redirect_uri 白名单校验
- POST /api/oauth/bangumi/disconnect 的 section 参数与 404 处理
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api import bangumi_oauth


@pytest.fixture
def app_with_auth():
    """已认证场景：_require_user 直接调用 get_current_user_flexible，
    需 patch 模块级引用（dependency_overrides 对直接调用不生效）。
    """
    app = FastAPI()
    app.include_router(bangumi_oauth.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "admin", "id": 1}

    with patch(
        "app.api.bangumi_oauth.get_current_user_flexible",
        mock_get_current_user,
    ):
        yield app


@pytest.fixture
def app_no_auth():
    """未认证场景：get_current_user_flexible 抛 401"""
    app = FastAPI()
    app.include_router(bangumi_oauth.router)

    async def mock_get_current_user(request=None, credentials=None):
        raise HTTPException(status_code=401, detail="未认证")

    with patch(
        "app.api.bangumi_oauth.get_current_user_flexible",
        mock_get_current_user,
    ):
        yield app


class TestStartRedirectUriWhitelist:
    """GET /api/oauth/bangumi/start 的 redirect_uri 白名单校验"""

    @pytest.mark.asyncio
    async def test_valid_redirect_uri_accepted(self, app_with_auth):
        """以 /api/oauth/bangumi/callback 结尾的 redirect_uri 应被接受"""
        with patch(
            "app.api.bangumi_oauth.bangumi_auth_service.get_auth_url",
            return_value=("https://bgm.tv/oauth/authorize?xxx", "state-abc"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/bangumi/start",
                    params={
                        "redirect_uri": "http://192.168.1.10:8000/api/oauth/bangumi/callback"
                    },
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_url" in data
        assert "state" in data
        # 响应不应再包含 redirect_uri 死字段
        assert "redirect_uri" not in data

    @pytest.mark.asyncio
    async def test_empty_redirect_uri_accepted(self, app_with_auth):
        """空 redirect_uri 应被接受（回退到 INI 默认值）"""
        with patch(
            "app.api.bangumi_oauth.bangumi_auth_service.get_auth_url",
            return_value=("https://bgm.tv/oauth/authorize?xxx", "state-abc"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/oauth/bangumi/start")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_malicious_redirect_uri_rejected(self, app_with_auth):
        """指向外部站点的 redirect_uri 应被拒绝（400）"""
        with patch(
            "app.api.bangumi_oauth.bangumi_auth_service.get_auth_url"
        ) as mock_get:
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/bangumi/start",
                    params={"redirect_uri": "https://evil.com/cb"},
                )
        assert resp.status_code == 400
        assert "回调地址" in resp.json()["detail"]
        # get_auth_url 不应被调用
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_redirect_uri_not_ending_with_callback_rejected(self, app_with_auth):
        """不以 /api/oauth/bangumi/callback 结尾的 redirect_uri 应被拒绝"""
        with patch(
            "app.api.bangumi_oauth.bangumi_auth_service.get_auth_url"
        ) as mock_get:
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/bangumi/start",
                    params={
                        "redirect_uri": "http://localhost:8000/api/oauth/bangumi/evil"
                    },
                )
        assert resp.status_code == 400
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, app_no_auth):
        """未认证时返回 401"""
        async with AsyncClient(
            transport=ASGITransport(app=app_no_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/oauth/bangumi/start")
        assert resp.status_code == 401


class TestDisconnectBySection:
    """POST /api/oauth/bangumi/disconnect 的 section 参数"""

    @pytest.mark.asyncio
    async def test_disconnect_by_section_success(self, app_with_auth):
        """指定 section 断开成功"""
        with patch(
            "app.api.bangumi_oauth.bangumi_auth_service.disconnect",
            return_value=True,
        ) as mock_disconnect:
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.post(
                    "/api/oauth/bangumi/disconnect",
                    params={"section": "bangumi-alice"},
                )
        assert resp.status_code == 200
        assert resp.json() == {"status": "success"}
        mock_disconnect.assert_called_once_with(section="bangumi-alice")

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_section_returns_404(self, app_with_auth):
        """断开不存在的 section 返回 404"""
        with patch(
            "app.api.bangumi_oauth.bangumi_auth_service.disconnect",
            return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.post(
                    "/api/oauth/bangumi/disconnect",
                    params={"section": "bangumi-ghost"},
                )
        assert resp.status_code == 404
        assert "未找到" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_disconnect_empty_section_falls_back_to_active(self, app_with_auth):
        """section 为空时回退到激活账号"""
        with patch(
            "app.api.bangumi_oauth.bangumi_auth_service.disconnect",
            return_value=True,
        ) as mock_disconnect:
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.post("/api/oauth/bangumi/disconnect")
        assert resp.status_code == 200
        mock_disconnect.assert_called_once_with(section=None)

    @pytest.mark.asyncio
    async def test_disconnect_unauthenticated_returns_401(self, app_no_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_no_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/oauth/bangumi/disconnect")
        assert resp.status_code == 401
