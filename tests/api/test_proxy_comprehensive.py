"""
代理 API 完整测试
"""

import socket
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, proxy


@pytest.fixture
def app_with_auth():
    """创建带有认证的测试应用"""
    app = FastAPI()
    app.include_router(proxy.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app

    app.dependency_overrides.clear()


class TestProxyAPICOMPREHENSIVE:
    """代理 API 综合测试"""

    def test_proxy_router_prefix(self):
        """测试代理路由器前缀"""
        assert proxy.router.prefix == "/api"

    def test_proxy_router_routes(self):
        """测试代理路由有路由"""
        assert len(proxy.router.routes) > 0


@pytest.mark.asyncio
async def test_get_proxy_suggestions_success(app_with_auth):
    """测试获取代理建议成功"""
    # docker_helper is imported inside the function, need to patch at source
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = {
            "is_docker": False,
            "network_mode": "native",
        }
        mock_dh.get_proxy_suggestions.return_value = [
            {"proxy": "http://127.0.0.1:7890", "name": "Clash"}
        ]

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/proxy/suggestions")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "suggestions" in data["data"]


@pytest.mark.asyncio
async def test_get_proxy_suggestions_exception(app_with_auth):
    """测试获取代理建议异常"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.side_effect = Exception("Test error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/proxy/suggestions")

            assert response.status_code == 200  # 返回错误状态而非抛出异常
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_test_proxy_connectivity_success(app_with_auth):
    """测试代理连通性成功"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.test_proxy_connectivity.return_value = {
            "success": True,
            "message": "Connection successful",
        }

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/proxy/test", json={"proxy_url": "http://127.0.0.1:7890"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_test_proxy_connectivity_exception(app_with_auth):
    """测试代理连通性异常"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.test_proxy_connectivity.side_effect = Exception("Test error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/proxy/test", json={"proxy_url": "http://127.0.0.1:7890"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_get_environment_info_success(app_with_auth):
    """测试获取环境信息成功"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = {
            "is_docker": True,
            "network_mode": "bridge",
        }

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/proxy/environment")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "data" in data


@pytest.mark.asyncio
async def test_get_environment_info_exception(app_with_auth):
    """测试获取环境信息异常"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.side_effect = Exception("Test error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/proxy/environment")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_test_host_connectivity_success(app_with_auth):
    """测试主机连通性成功"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.test_host_connectivity.return_value = {
            "success": True,
            "message": "Connection successful",
        }

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/proxy/test-host",
                json={"host": "google.com", "port": 80, "timeout": 5},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_test_host_connectivity_exception(app_with_auth):
    """测试主机连通性异常"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.test_host_connectivity.side_effect = Exception("Test error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/proxy/test-host",
                json={"host": "google.com", "port": 80, "timeout": 5},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_diagnose_network_success_dns_fail(app_with_auth):
    """测试网络诊断 DNS 解析失败"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = {"is_docker": False}

        with patch("app.core.config.config_manager") as mock_cm:
            mock_cm.get.return_value = ""

            with patch(
                "app.api.proxy.socket.getaddrinfo",
                side_effect=socket.gaierror("DNS error"),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app_with_auth), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/network/diagnose", json={"url": "https://example.com"}
                    )

                    assert response.status_code == 200
                    data = response.json()
                    assert data["status"] == "success"


@pytest.mark.asyncio
async def test_diagnose_network_tcp_fail(app_with_auth):
    """测试网络诊断 TCP 连接失败"""
    mock_env_info = {"is_docker": False}

    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = mock_env_info

        with patch("app.core.config.config_manager") as mock_cm:
            mock_cm.get.return_value = ""

            # Mock socket.getaddrinfo to return a valid IP
            with patch("app.api.proxy.socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
                ]

                # Mock connect_ex to return non-zero (connection failed)
                with patch("app.api.proxy.socket.socket") as mock_socket:
                    mock_sock_instance = MagicMock()
                    mock_sock_instance.connect_ex.return_value = 1  # Connection refused
                    mock_socket.return_value = mock_sock_instance

                    async with AsyncClient(
                        transport=ASGITransport(app=app_with_auth),
                        base_url="http://test",
                    ) as client:
                        response = await client.post(
                            "/api/network/diagnose", json={"url": "https://example.com"}
                        )

                        assert response.status_code == 200


@pytest.mark.asyncio
async def test_diagnose_network_tcp_success(app_with_auth):
    """测试网络诊断 TCP 连接成功"""
    mock_env_info = {"is_docker": False}

    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = mock_env_info

        with patch("app.core.config.config_manager") as mock_cm:
            mock_cm.get.return_value = ""

            with patch("app.api.proxy.socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
                ]

                with patch("app.api.proxy.socket.socket") as mock_socket:
                    mock_sock_instance = MagicMock()
                    mock_sock_instance.connect_ex.return_value = 0  # Success
                    mock_socket.return_value = mock_sock_instance

                    # Mock requests.get to raise exception
                    with patch("requests.get") as mock_requests:
                        mock_requests.side_effect = Exception("Request failed")

                        async with AsyncClient(
                            transport=ASGITransport(app=app_with_auth),
                            base_url="http://test",
                        ) as client:
                            response = await client.post(
                                "/api/network/diagnose",
                                json={"url": "https://example.com"},
                            )

                            assert response.status_code == 200


@pytest.mark.asyncio
async def test_diagnose_network_with_proxy(app_with_auth):
    """测试使用代理的网络诊断"""
    mock_env_info = {"is_docker": False}

    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = mock_env_info

        with patch("app.core.config.config_manager") as mock_cm:
            mock_cm.get.side_effect = lambda *args, **kwargs: (
                "http://proxy:7890" if args[1] == "script_proxy" else True
            )

            with patch("app.api.proxy.socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
                ]

                with patch("app.api.proxy.socket.socket") as mock_socket:
                    mock_sock_instance = MagicMock()
                    mock_sock_instance.connect_ex.return_value = 0
                    mock_socket.return_value = mock_sock_instance

                    with patch("requests.get") as mock_requests:
                        mock_response = MagicMock()
                        mock_response.status_code = 200
                        mock_requests.return_value = mock_response

                        async with AsyncClient(
                            transport=ASGITransport(app=app_with_auth),
                            base_url="http://test",
                        ) as client:
                            response = await client.post(
                                "/api/network/diagnose",
                                json={"url": "https://example.com"},
                            )

                            assert response.status_code == 200
                            data = response.json()
                            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_diagnose_network_exception(app_with_auth):
    """测试网络诊断异常"""
    with patch("app.utils.docker_helper.docker_helper") as mock_dh:
        mock_dh.get_environment_info.side_effect = Exception("Test error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/network/diagnose", json={"url": "https://example.com"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"


@pytest.mark.asyncio
async def test_get_system_dns_servers_default(app_with_auth):
    """测试获取系统 DNS 服务器使用默认值"""
    # Test when socket.getaddrinfo fails
    with patch("app.api.proxy.socket.getaddrinfo", side_effect=Exception("DNS error")):
        with patch("app.api.proxy.platform.system", return_value="unknown"):
            result = proxy.get_system_dns_servers()
            assert "无法获取DNS服务器信息" in result


def test_get_system_dns_servers_windows_fallback():
    """测试 Windows 系统的 DNS 获取回退逻辑"""
    with patch("app.api.proxy.platform.system", return_value="windows"):
        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch("app.api.proxy.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError

                result = proxy.get_system_dns_servers()
                # Should have default value since all methods fail
                assert len(result) > 0


def test_get_system_dns_servers_linux():
    """测试 Linux 系统的 DNS 获取"""
    with patch("app.api.proxy.platform.system", return_value="linux"):
        # First call to /etc/resolv.conf fails
        with patch("builtins.open") as mock_open:
            mock_file = MagicMock()
            mock_file.__enter__ = MagicMock(return_value=mock_file)
            mock_file.__exit__ = MagicMock(return_value=False)
            mock_file.read.return_value = "nameserver 8.8.8.8\n"
            mock_open.return_value = mock_file

            with patch("app.api.proxy.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError

                result = proxy.get_system_dns_servers()
                assert "8.8.8.8" in result
