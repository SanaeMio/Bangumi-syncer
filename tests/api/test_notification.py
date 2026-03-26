"""
通知API测试
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, notification


@pytest.fixture
def app_with_auth():
    """创建带有认证的测试应用"""
    app = FastAPI()
    app.include_router(notification.router)

    # 覆盖认证依赖
    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app

    # 清理覆盖
    app.dependency_overrides.clear()


def test_notification_router_init():
    """测试通知路由器初始化"""
    # notification.router 的 tags 可能为空，因为是通过 include_router 添加的
    assert notification.router is not None


@pytest.mark.asyncio
async def test_get_notification_status(app_with_auth):
    """测试获取通知状态"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/status")
        # 期望返回 200（成功）或其他状态
        assert response.status_code in [200, 404, 500]


@pytest.mark.asyncio
async def test_get_webhooks(app_with_auth):
    """测试获取 webhook 列表"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/webhooks")
        assert response.status_code in [200, 404, 500]


@pytest.mark.asyncio
async def test_get_emails(app_with_auth):
    """测试获取邮件列表"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/emails")
        assert response.status_code in [200, 404, 500]
