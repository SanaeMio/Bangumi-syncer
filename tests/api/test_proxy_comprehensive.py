"""
代理 API 完整测试
"""

import socket
from unittest.mock import MagicMock, patch

import httpx
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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
    with patch("app.api.proxy.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = {"is_docker": False}

        with patch("app.api.proxy.config_manager") as mock_cm:
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

    with patch("app.api.proxy.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = mock_env_info

        with patch("app.api.proxy.config_manager") as mock_cm:
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

    with patch("app.api.proxy.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = mock_env_info

        with patch("app.api.proxy.config_manager") as mock_cm:
            mock_cm.get.return_value = ""

            with patch("app.api.proxy.socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
                ]

                with patch("app.api.proxy.socket.socket") as mock_socket:
                    mock_sock_instance = MagicMock()
                    mock_sock_instance.connect_ex.return_value = 0  # Success
                    mock_socket.return_value = mock_sock_instance

                    # Mock httpx.Client to raise exception
                    with patch("httpx.Client") as mock_client_cls:
                        mock_client_cls.return_value.request.side_effect = Exception(
                            "Request failed"
                        )

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

    with patch("app.api.proxy.docker_helper") as mock_dh:
        mock_dh.get_environment_info.return_value = mock_env_info

        with patch("app.api.proxy.config_manager") as mock_cm:
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

                    with patch("httpx.Client") as mock_client_cls:
                        mock_response = MagicMock()
                        mock_response.status_code = 200
                        mock_response.elapsed.total_seconds.return_value = 0.01
                        mock_response.headers = {}
                        mock_response.text = ""
                        mock_client_cls.return_value.request.return_value = (
                            mock_response
                        )

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
    with patch("app.api.proxy.docker_helper") as mock_dh:
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


# ===== 以下自 smoke 并入 =====


@pytest.mark.asyncio
async def test_proxy_test_host(app_with_auth):
    with patch(
        "app.utils.docker_helper.docker_helper.test_host_connectivity",
        return_value={"reachable": True, "latency_ms": 1},
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/proxy/test-host",
                json={"host": "127.0.0.1", "port": 65534, "timeout": 1},
            )
    assert r.status_code == 200
    assert r.json()["data"]["reachable"] is True


@pytest.mark.asyncio
async def test_network_diagnose_dns_failure(app_with_auth):
    with patch(
        "app.api.proxy.socket.getaddrinfo",
        side_effect=socket.gaierror("Name or service not known"),
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/network/diagnose",
                json={"url": "https://this-host-should-not-exist-12345.invalid/"},
            )
    assert r.status_code == 200
    diagnosis = r.json()["data"]["diagnosis"]
    dns_step = next(x for x in diagnosis if x.get("test") == "DNS解析")
    assert dns_step["status"] == "failed"


@pytest.mark.asyncio
async def test_network_diagnose_dns_non_gaierror(app_with_auth):
    with patch(
        "app.api.proxy.socket.getaddrinfo",
        side_effect=RuntimeError("resolver boom"),
    ):
        with patch(
            "app.utils.docker_helper.docker_helper.get_environment_info",
            return_value={"is_docker": False},
        ):
            with patch("app.core.config.config_manager.get", return_value=""):
                transport = ASGITransport(app=app_with_auth)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post(
                        "/api/network/diagnose",
                        json={"url": "https://example.com/"},
                    )
    assert r.status_code == 200
    dns_step = next(x for x in r.json()["data"]["diagnosis"] if x["test"] == "DNS解析")
    assert dns_step["status"] == "error"


@pytest.mark.asyncio
async def test_network_diagnose_tcp_and_http_success(app_with_auth):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_resp.elapsed.total_seconds.return_value = 0.01
    mock_resp.headers = {}
    mock_resp.text = ""

    def fake_get(section, key, fallback=None):
        if key == "ssl_verify":
            return True
        if key == "script_proxy":
            return ""
        return fallback

    with patch(
        "app.api.proxy.socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
        ],
    ):
        with patch("app.api.proxy.socket.socket", return_value=mock_sock):
            with patch("httpx.Client") as mock_client_cls:
                mock_client_cls.return_value.request.return_value = mock_resp
                with patch(
                    "app.utils.docker_helper.docker_helper.get_environment_info",
                    return_value={"is_docker": False},
                ):
                    with patch(
                        "app.core.config.config_manager.get", side_effect=fake_get
                    ):
                        transport = ASGITransport(app=app_with_auth)
                        async with AsyncClient(
                            transport=transport, base_url="http://test"
                        ) as ac:
                            r = await ac.post(
                                "/api/network/diagnose",
                                json={"url": "https://127.0.0.1:443/"},
                            )

    assert r.status_code == 200
    diagnosis = r.json()["data"]["diagnosis"]
    tests = {d["test"]: d["status"] for d in diagnosis}
    assert tests.get("DNS解析") == "success"
    assert tests.get("TCP直连") == "success"
    assert tests.get("HTTP连接") == "success"
    mock_sock.close.assert_called_once()


@pytest.mark.asyncio
async def test_network_diagnose_tcp_connect_ex_failed(app_with_auth):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 61
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.elapsed.total_seconds.return_value = 0.01
    mock_resp.headers = {}
    mock_resp.text = ""

    p_gai, p_docker, p_cfg = _patch_network_diagnose_dns_ok_no_proxy()
    with p_gai, p_docker, p_cfg:
        with patch("app.api.proxy.socket.socket", return_value=mock_sock):
            with patch("httpx.Client") as mock_client_cls:
                mock_client_cls.return_value.request.return_value = mock_resp
                transport = ASGITransport(app=app_with_auth)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post(
                        "/api/network/diagnose",
                        json={"url": "https://127.0.0.1:443/"},
                    )

    steps = {d["test"]: d for d in r.json()["data"]["diagnosis"]}
    assert steps["TCP直连"]["status"] == "failed"
    assert steps["HTTP连接"]["status"] == "success"


@pytest.mark.asyncio
async def test_network_diagnose_tcp_socket_raises(app_with_auth):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.elapsed.total_seconds.return_value = 0.01
    mock_resp.headers = {}
    mock_resp.text = ""

    p_gai, p_docker, p_cfg = _patch_network_diagnose_dns_ok_no_proxy()
    with p_gai, p_docker, p_cfg:
        with patch(
            "app.api.proxy.socket.socket",
            side_effect=OSError("socket factory"),
        ):
            with patch("httpx.Client") as mock_client_cls:
                mock_client_cls.return_value.request.return_value = mock_resp
                transport = ASGITransport(app=app_with_auth)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post(
                        "/api/network/diagnose",
                        json={"url": "https://127.0.0.1:443/"},
                    )

    steps = {d["test"]: d for d in r.json()["data"]["diagnosis"]}
    assert steps["TCP直连"]["status"] == "error"
    assert "socket factory" in steps["TCP直连"]["message"]


@pytest.mark.asyncio
async def test_network_diagnose_http_request_exception(app_with_auth):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0

    def fake_get(section, key, fallback=None):
        if key == "ssl_verify":
            return True
        if key == "script_proxy":
            return ""
        return fallback

    p_gai, p_docker, _ = _patch_network_diagnose_dns_ok_no_proxy()
    with p_gai, p_docker:
        with patch("app.core.config.config_manager.get", side_effect=fake_get):
            with patch("app.api.proxy.socket.socket", return_value=mock_sock):
                with patch("httpx.Client") as mock_client_cls:
                    mock_client_cls.return_value.request.side_effect = (
                        httpx.ConnectError("refused")
                    )
                    transport = ASGITransport(app=app_with_auth)
                    async with AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as ac:
                        r = await ac.post(
                            "/api/network/diagnose",
                            json={"url": "https://127.0.0.1:443/"},
                        )

    steps = {d["test"]: d for d in r.json()["data"]["diagnosis"]}
    assert steps["HTTP连接"]["status"] == "failed"
    assert "refused" in steps["HTTP连接"]["message"]


@pytest.mark.asyncio
async def test_network_diagnose_http_non_request_exception(app_with_auth):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0

    def fake_get(section, key, fallback=None):
        if key == "ssl_verify":
            return True
        if key == "script_proxy":
            return ""
        return fallback

    p_gai, p_docker, _ = _patch_network_diagnose_dns_ok_no_proxy()
    with p_gai, p_docker:
        with patch("app.core.config.config_manager.get", side_effect=fake_get):
            with patch("app.api.proxy.socket.socket", return_value=mock_sock):
                with patch("httpx.Client") as mock_client_cls:
                    mock_client_cls.return_value.request.side_effect = ValueError(
                        "unexpected"
                    )
                    transport = ASGITransport(app=app_with_auth)
                    async with AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as ac:
                        r = await ac.post(
                            "/api/network/diagnose",
                            json={"url": "https://127.0.0.1:443/"},
                        )

    steps = {d["test"]: d for d in r.json()["data"]["diagnosis"]}
    assert steps["HTTP连接"]["status"] == "error"
    assert "unexpected" in steps["HTTP连接"]["message"]


def _patch_network_diagnose_dns_ok_no_proxy():
    """单次调用返回一组 patch：DNS 成功、docker 环境、无 script_proxy。"""
    return (
        patch(
            "app.api.proxy.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
            ],
        ),
        patch(
            "app.utils.docker_helper.docker_helper.get_environment_info",
            return_value={"is_docker": False},
        ),
        patch("app.core.config.config_manager.get", return_value=""),
    )
