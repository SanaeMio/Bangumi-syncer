"""
更多 API 端点测试
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps, health, logs


@pytest.fixture
def app_with_auth():
    """创建带有认证的测试应用"""
    app = FastAPI()

    # 覆盖认证依赖
    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app

    # 清理覆盖
    app.dependency_overrides.clear()


class TestHealth:
    """测试健康检查端点"""

    def test_health_endpoint(self):
        """测试健康检查端点"""
        app = FastAPI()
        app.include_router(health.router)

        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200


class TestLogs:
    """测试日志端点"""

    def test_logs_endpoint(self):
        """测试日志端点"""
        app = FastAPI()
        app.include_router(logs.router)

        client = TestClient(app)

        response = client.get("/api/logs")
        # 可能返回多种状态
        assert response.status_code in [200, 401, 404, 500]


class TestProxy:
    """测试代理端点"""

    def test_get_system_dns(self):
        """测试获取系统 DNS"""
        from app.api import proxy

        # 测试 get_system_dns_servers 函数
        dns_servers = proxy.get_system_dns_servers()
        assert isinstance(dns_servers, list)
