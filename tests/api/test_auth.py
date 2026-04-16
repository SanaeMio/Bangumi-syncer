"""
认证API测试
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import auth


@pytest.fixture
def mock_security_manager():
    """模拟安全管理器"""
    with patch("app.api.auth.security_manager") as mock_security:
        # 模拟认证成功
        mock_security.authenticate_user.return_value = True

        # 模拟会话管理
        mock_security.create_session.return_value = "test_session_token_12345"
        mock_security.remove_session.return_value = None
        mock_security.reset_login_attempts.return_value = None

        # 模拟 IP 检查
        mock_security.is_ip_locked.return_value = False
        mock_security.get_lockout_info.return_value = {"locked_until": 0}
        mock_security.get_login_attempts.return_value = {"attempts": 0}
        mock_security.get_auth_config.return_value = {"max_login_attempts": 5}
        mock_security.record_login_failure.return_value = None

        yield mock_security


@pytest.fixture
def mock_request():
    """创建模拟请求对象"""
    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers.get.side_effect = lambda key, default=None: {
        "X-Forwarded-For": None,
        "X-Real-IP": None,
    }.get(key, default)
    request.cookies.get.return_value = None
    return request


@pytest.mark.asyncio
async def test_login_success(mock_security_manager):
    """测试登录成功"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/login",
            json={"username": "testuser", "password": "testpass"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        # 检查 cookie 设置
        cookies = response.cookies
        assert "session_token" in cookies


@pytest.mark.asyncio
async def test_login_missing_credentials(mock_security_manager):
    """测试缺少凭据"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/login", json={})

        assert response.status_code == 400


@pytest.mark.asyncio
async def test_login_invalid_credentials(mock_security_manager):
    """测试无效凭据"""
    mock_security_manager.authenticate_user.return_value = False

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/login",
            json={"username": "testuser", "password": "wrongpass"},
        )

        assert response.status_code == 401
        data = response.json()
        assert "错误" in data["detail"]


@pytest.mark.asyncio
async def test_login_ip_locked(mock_security_manager):
    """测试 IP 被锁定"""
    mock_security_manager.is_ip_locked.return_value = True
    mock_security_manager.get_lockout_info.return_value = {"locked_until": 9999999999}

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/login",
            json={"username": "testuser", "password": "testpass"},
        )

        assert response.status_code == 423


@pytest.mark.asyncio
async def test_login_too_many_attempts(mock_security_manager):
    """测试登录尝试次数过多"""
    mock_security_manager.authenticate_user.return_value = False
    mock_security_manager.is_ip_locked.return_value = True
    mock_security_manager.get_lockout_info.return_value = {"locked_until": 9999999999}

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/login",
            json={"username": "testuser", "password": "wrongpass"},
        )

        assert response.status_code == 423


@pytest.mark.asyncio
async def test_logout(mock_security_manager):
    """测试登出"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/logout")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


@pytest.mark.asyncio
async def test_auth_status_authenticated():
    """测试已认证状态"""
    from fastapi import FastAPI

    # 模拟已认证用户 - 禁用认证
    with patch("app.api.deps.security_manager") as mock_sm:
        mock_sm.get_auth_config.return_value = {"enabled": False}
        mock_sm.validate_session.return_value = {"username": "testuser", "id": 1}

        app = FastAPI()
        app.include_router(auth.router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/auth/status")

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["authenticated"] is True


@pytest.mark.asyncio
async def test_auth_status_not_authenticated():
    """测试未认证状态"""
    from fastapi import FastAPI

    # 模拟认证被禁用，返回默认用户
    with patch("app.api.deps.security_manager") as mock_sm:
        mock_sm.get_auth_config.return_value = {"enabled": False}
        mock_sm.validate_session.return_value = None

        app = FastAPI()
        app.include_router(auth.router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/auth/status")

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["authenticated"] is True


def test_get_client_ip_forwarded():
    """测试从 X-Forwarded-For 获取 IP"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # 创建一个简单的路由来测试
    app = FastAPI()

    @app.get("/test-ip")
    async def test_ip(request: auth.Request):
        return {"ip": auth.get_client_ip(request)}

    client = TestClient(app)

    response = client.get(
        "/test-ip", headers={"X-Forwarded-For": "192.168.1.1, 10.0.0.1"}
    )

    assert response.status_code == 200
    assert response.json()["ip"] == "192.168.1.1"


def test_get_client_ip_real_ip():
    """测试从 X-Real-IP 获取 IP"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/test-ip")
    async def test_ip(request: auth.Request):
        return {"ip": auth.get_client_ip(request)}

    client = TestClient(app)

    response = client.get("/test-ip", headers={"X-Real-IP": "192.168.1.100"})

    assert response.status_code == 200
    assert response.json()["ip"] == "192.168.1.100"


def test_get_client_ip_direct():
    """测试直接连接获取 IP"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/test-ip")
    async def test_ip(request: auth.Request):
        return {"ip": auth.get_client_ip(request)}

    client = TestClient(app)

    response = client.get("/test-ip")

    assert response.status_code == 200
    assert response.json()["ip"] == "testclient"


def test_get_client_ip_unknown_when_no_client():
    """request.client 为空时返回 unknown"""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": None,
        "server": ("testserver", 80),
    }
    req = Request(scope)
    assert auth.get_client_ip(req) == "unknown"


@pytest.mark.asyncio
async def test_login_wrong_password_401_with_remaining(mock_security_manager):
    """登录失败且未锁 IP：返回 401 并带剩余次数"""
    mock_security_manager.is_ip_locked.return_value = False
    mock_security_manager.authenticate_user.return_value = False
    mock_security_manager.get_login_attempts.return_value = {"attempts": 2}
    mock_security_manager.get_auth_config.return_value = {"max_login_attempts": 5}

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/login",
            json={"username": "u", "password": "bad"},
        )

    assert response.status_code == 401
    assert "剩余" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_invalid_json_returns_500():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/login",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_logout_remove_session_error_returns_500(mock_security_manager):
    mock_security_manager.remove_session.side_effect = RuntimeError("db")

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth.router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"session_token": "tok"},
    ) as client:
        response = await client.post("/api/logout")

    assert response.status_code == 500
