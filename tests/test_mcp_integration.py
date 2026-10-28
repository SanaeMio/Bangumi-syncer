"""
MCP 端到端集成测试

覆盖 BDD 场景：
1. list_tools → 3 工具且 schema 正确
2. 未认证调工具 → 401
3. 走完授权流程 → token → 调工具成功
4. /.well-known/oauth-authorization-server 含 client_id_metadata_document_supported: true
"""

from __future__ import annotations

import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from fastmcp import Client
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from tests.mcp_helpers import build_test_mcp_app

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_code_challenge_b64(verifier: str) -> str:
    """由 verifier 生成 S256 code challenge（base64url）。"""
    import base64

    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _extract_csrf_token(html_content: str) -> str:
    """从 consent 表单 HTML 中提取 CSRF token。"""
    import re

    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html_content)
    if not match:
        raise ValueError("CSRF token not found in consent form")
    return match.group(1)


# ---------------------------------------------------------------------------
# 1. list_tools → 3 工具且 schema 正确
# ---------------------------------------------------------------------------


class TestListTools:
    """验证 MCP server 注册了 3 个工具且 schema 正确。"""

    @pytest.mark.asyncio
    async def test_list_tools_返回3个工具(self):
        """list_tools 应返回 3 个工具：get_logs / get_current_config / update_config。"""
        from app.mcp.server import create_mcp_server

        mcp = create_mcp_server()
        client = Client(mcp)
        async with client:
            tools = await client.list_tools()

        tool_names = [t.name for t in tools]
        assert len(tool_names) == 3, f"应注册 3 个工具，实际: {len(tool_names)}"
        assert "get_logs" in tool_names
        assert "get_current_config" in tool_names
        assert "update_config" in tool_names

    @pytest.mark.asyncio
    async def test_get_logs_schema_包含预期参数(self):
        """get_logs 工具的 schema 应包含 level/search/limit/since/until 参数。"""
        from app.mcp.server import create_mcp_server

        mcp = create_mcp_server()
        client = Client(mcp)
        async with client:
            tools = await client.list_tools()

        get_logs_tool = next(t for t in tools if t.name == "get_logs")
        params = get_logs_tool.input_schema.get("properties", {})
        assert "level" in params
        assert "search" in params
        assert "limit" in params
        assert "since" in params
        assert "until" in params

    @pytest.mark.asyncio
    async def test_get_current_config_schema_无参数(self):
        """get_current_config 工具应无参数。"""
        from app.mcp.server import create_mcp_server

        mcp = create_mcp_server()
        client = Client(mcp)
        async with client:
            tools = await client.list_tools()

        get_config_tool = next(t for t in tools if t.name == "get_current_config")
        params = get_config_tool.input_schema.get("properties", {})
        assert len(params) == 0, (
            f"get_current_config 应无参数，实际: {list(params.keys())}"
        )

    @pytest.mark.asyncio
    async def test_update_config_schema_包含必填参数(self):
        """update_config 工具的 schema 应包含 section/key/value 三个必填参数。"""
        from app.mcp.server import create_mcp_server

        mcp = create_mcp_server()
        client = Client(mcp)
        async with client:
            tools = await client.list_tools()

        update_tool = next(t for t in tools if t.name == "update_config")
        params = update_tool.input_schema.get("properties", {})
        required = update_tool.input_schema.get("required", [])
        assert "section" in params
        assert "key" in params
        assert "value" in params
        assert set(required) == {"section", "key", "value"}


# ---------------------------------------------------------------------------
# 2. 配置接线：auth_enabled/base_url/auth_username 从 BS 配置读取
# ---------------------------------------------------------------------------


class TestProviderConfigWiring:
    """验证 _create_provider 从 BS 配置读取 auth_enabled/auth_username/base_url。"""

    def test_auth_enabled_从security_manager读取(self, monkeypatch):
        """provider.auth_enabled 应跟随 security_manager.get_auth_config()["enabled"]。"""
        from app.mcp import server

        # 模拟 security_manager.get_auth_config
        monkeypatch.setattr(
            "app.mcp.server.security_manager.get_auth_config",
            lambda: {
                "enabled": True,
                "username": "testuser",
                "password": "hashed",
                "session_timeout": 3600,
                "secret_key": "key",
                "https_only": False,
                "max_login_attempts": 5,
                "lockout_duration": 900,
                "webhook_key": "",
                "webhook_auth_enabled": False,
            },
        )
        # 模拟 config_manager.get 以提供 base_url 回退
        monkeypatch.setattr(
            "app.mcp.server.config_manager.get",
            lambda section, key, fallback="": fallback,
        )

        provider = server._create_provider(base_url="http://localhost:8000")
        assert provider.auth_enabled is True
        assert provider.auth_username == "testuser"

    def test_auth_disabled_从security_manager读取(self, monkeypatch):
        """auth.enabled=False 时 provider.auth_enabled 应为 False。"""
        from app.mcp import server

        monkeypatch.setattr(
            "app.mcp.server.security_manager.get_auth_config",
            lambda: {
                "enabled": False,
                "username": "admin",
                "password": "hashed",
                "session_timeout": 3600,
                "secret_key": "key",
                "https_only": False,
                "max_login_attempts": 5,
                "lockout_duration": 900,
                "webhook_key": "",
                "webhook_auth_enabled": False,
            },
        )
        monkeypatch.setattr(
            "app.mcp.server.config_manager.get",
            lambda section, key, fallback="": fallback,
        )

        provider = server._create_provider(base_url="http://localhost:8000")
        assert provider.auth_enabled is False

    def test_base_url_优先环境变量MCP_BASE_URL(self, monkeypatch):
        """MCP_BASE_URL 环境变量应优先作为 base_url/issuer。"""
        from app.mcp import server

        monkeypatch.setenv("MCP_BASE_URL", "https://example.com")
        monkeypatch.setattr(
            "app.mcp.server.security_manager.get_auth_config",
            lambda: {
                "enabled": False,
                "username": "admin",
                "password": "hashed",
                "session_timeout": 3600,
                "secret_key": "key",
                "https_only": False,
                "max_login_attempts": 5,
                "lockout_duration": 900,
                "webhook_key": "",
                "webhook_auth_enabled": False,
            },
        )
        monkeypatch.setattr(
            "app.mcp.server.config_manager.get",
            lambda section, key, fallback="": fallback,
        )

        provider = server._create_provider()
        # AnyHttpUrl 标准化为带尾斜杠
        assert str(provider.base_url).rstrip("/") == "https://example.com"
        assert str(provider.issuer).rstrip("/") == "https://example.com"


# ---------------------------------------------------------------------------
# 3. 未认证调工具 → 401
# ---------------------------------------------------------------------------


class TestUnauthenticatedToolCall:
    """验证未认证时调用工具返回 401。"""

    @pytest.mark.asyncio
    async def test_未认证调工具_返回401(self):
        """未携带 Bearer Token 调用 /mcp 工具端点应返回 401。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "get_current_config", "arguments": {}},
                },
            )

        assert response.status_code == 401, (
            f"未认证调工具应返回 401，实际: {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_无效token_返回401(self):
        """携带无效 Bearer Token 调用工具应返回 401。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "get_current_config", "arguments": {}},
                },
                headers={"Authorization": "Bearer invalid.token.here"},
            )

        assert response.status_code == 401, (
            f"无效 token 应返回 401，实际: {response.status_code}"
        )


# ---------------------------------------------------------------------------
# 3. 走完授权流程 → token → 调工具成功
# ---------------------------------------------------------------------------


class TestFullOAuthFlowWithToolCall:
    """验证完整 OAuth 流程后能成功调用工具。"""

    @pytest.fixture
    def tmp_keys(self, tmp_path):
        """创建临时密钥文件。"""
        return {
            "private": str(tmp_path / "private.pem"),
            "public": str(tmp_path / "public.pem"),
        }

    @pytest.fixture
    def server_app(self, tmp_keys):
        """创建 auth.enabled=False 且注册了 tools 的测试 server。"""
        return build_test_mcp_app(
            private_key_path=tmp_keys["private"],
            public_key_path=tmp_keys["public"],
            issuer="http://localhost:8000",
            audience="bangumi-syncer",
            auth_enabled=False,
            auth_username="admin",
        )

    def _make_test_client(self, app):
        return TestClient(app, raise_server_exceptions=False)

    @pytest.mark.asyncio
    async def test_授权后_token有效且能调用工具函数(self, server_app):
        """完整 OAuth 流程后，access_token 应有效且能调用工具函数。

        注意：MCP /mcp 端点需要 FastMCP 内部任务组（通过 FastAPI combine_lifespan 初始化），
        这里测试 OAuth 流程 + 工具函数直接调用，验证端到端集成。
        """
        # 步骤 1-7：OAuth 流程（同步 TestClient 处理重定向/表单）
        client = self._make_test_client(server_app)

        # 步骤 1：DCR 注册客户端（显式注册 read write scope，因默认 scope 已改为 read）
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
                "scope": "read write",
            },
        )
        assert reg_response.status_code == 201
        client_id = reg_response.json()["client_id"]

        # 步骤 2：发起授权
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = _make_code_challenge_b64(code_verifier)
        auth_response = client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://localhost/callback",
                "response_type": "code",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "read write",
                "state": "test-state",
            },
            follow_redirects=False,
        )
        assert auth_response.status_code == 302
        consent_url = auth_response.headers["location"]
        assert "/consent" in consent_url

        # 步骤 3：获取 consent 页面并提取 CSRF token
        consent_get = client.get(consent_url)
        assert consent_get.status_code == 200
        csrf_token = _extract_csrf_token(consent_get.text)

        # 步骤 4：解析 request_token
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        # 步骤 5：同意授权
        consent_post = client.post(
            "/consent",
            data={
                "action": "allow",
                "request_token": request_token,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert consent_post.status_code == 302
        redirect_url = consent_post.headers["location"]
        assert "code=" in redirect_url

        # 步骤 6：提取 authorization code
        parsed_redirect = urlparse(redirect_url)
        code = parse_qs(parsed_redirect.query)["code"][0]

        # 步骤 7：换取 access token
        token_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://localhost/callback",
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
        )
        assert token_response.status_code == 200
        token_data = token_response.json()
        assert token_data["token_type"] == "Bearer"
        assert token_data["access_token"]
        access_token = token_data["access_token"]

        # 步骤 8：验证 access_token 有效（通过 provider 验签）
        provider = server_app.state.provider
        loaded_token = await provider.load_access_token(access_token)
        assert loaded_token is not None, "access_token 应能通过 provider 验签"
        assert "read" in loaded_token.scopes
        assert "write" in loaded_token.scopes

        # 步骤 9：直接调用工具函数（验证工具在 server 注册后可正常执行）
        from unittest.mock import MagicMock, patch

        from app.mcp.tools import get_current_config

        mock_token = MagicMock()
        mock_token.scopes = ["read", "write"]
        with patch("app.mcp.tools.get_access_token", return_value=mock_token):
            result = await get_current_config()
        assert result["status"] == "success"
        assert "data" in result


# ---------------------------------------------------------------------------
# 3b. 真实 HTTP 端到端：获取 token → AsyncClient 携带 Bearer 调用 /mcp
# ---------------------------------------------------------------------------


class TestRealHttpEndToEnd:
    """真实 HTTP 端到端测试：获取 access_token 后通过 TestClient 调用 /mcp。

    覆盖评审 A#6：验证 auth middleware → 工具完整路径。

    注意：MCP /mcp 端点需要 FastMCP 内部 session manager（通过 lifespan 初始化），
    因此使用 TestClient 作为上下文管理器（运行 lifespan），而非 ASGITransport。
    Streamable HTTP 协议需要先发送 initialize 请求获取 session ID，
    后续请求携带 Mcp-Session-Id header。
    """

    @pytest.fixture
    def tmp_keys(self, tmp_path):
        """创建临时密钥文件。"""
        return {
            "private": str(tmp_path / "private.pem"),
            "public": str(tmp_path / "public.pem"),
        }

    @pytest.fixture
    def server_app(self, tmp_keys):
        """创建 auth.enabled=False 且注册了 tools 的测试 server。"""
        return build_test_mcp_app(
            private_key_path=tmp_keys["private"],
            public_key_path=tmp_keys["public"],
            issuer="http://localhost:8000",
            audience="bangumi-syncer",
            auth_enabled=False,
            auth_username="admin",
        )

    def _make_test_client(self, app):
        return TestClient(app, raise_server_exceptions=False)

    def _do_full_oauth_flow(self, client) -> str:
        """执行完整 OAuth 流程，返回 access_token。"""
        # 步骤 1：DCR（显式注册 read write scope，因默认 scope 已改为 read）
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
                "scope": "read write",
            },
        )
        assert reg_response.status_code == 201
        client_id = reg_response.json()["client_id"]

        # 步骤 2：发起授权
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = _make_code_challenge_b64(code_verifier)
        auth_response = client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://localhost/callback",
                "response_type": "code",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "read write",
                "state": "test-state",
            },
            follow_redirects=False,
        )
        assert auth_response.status_code == 302
        consent_url = auth_response.headers["location"]

        # 步骤 3：获取 consent 表单
        consent_get = client.get(consent_url)
        assert consent_get.status_code == 200
        csrf_token = _extract_csrf_token(consent_get.text)

        # 步骤 4：解析 request_token
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        # 步骤 5：同意授权
        consent_post = client.post(
            "/consent",
            data={
                "action": "allow",
                "request_token": request_token,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert consent_post.status_code == 302
        redirect_url = consent_post.headers["location"]
        code = parse_qs(urlparse(redirect_url).query)["code"][0]

        # 步骤 6：换取 token
        token_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://localhost/callback",
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
        )
        assert token_response.status_code == 200
        return token_response.json()["access_token"]

    def _initialize_session(self, client, access_token: str) -> str:
        """发送 initialize 请求，返回 session ID。"""
        init_response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert init_response.status_code == 200, (
            f"initialize 应返回 200，实际: {init_response.status_code}"
        )
        session_id = init_response.headers.get("mcp-session-id")
        assert session_id is not None, "initialize 响应应包含 mcp-session-id"
        return session_id

    def test_获取token后_真实HTTP调用list_tools成功(self, server_app):
        """获取 access_token 后，TestClient 携带 Bearer 调用 /mcp list_tools 应成功。"""
        with self._make_test_client(server_app) as client:
            # OAuth 流程获取 token
            access_token = self._do_full_oauth_flow(client)

            # 初始化 session
            session_id = self._initialize_session(client, access_token)

            # 调用 tools/list
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Mcp-Session-Id": session_id,
                },
            )

        assert response.status_code == 200, (
            f"真实 HTTP 调用应返回 200，实际: {response.status_code}, body: {response.text[:200]}"
        )
        # Streamable HTTP 返回 SSE 格式，需要解析
        body = response.text
        assert "tools" in body
        data_line = [line for line in body.split("\n") if line.startswith("data: ")]
        assert len(data_line) > 0
        import json as _json

        data = _json.loads(data_line[0].removeprefix("data: "))
        assert "result" in data
        tool_names = [t["name"] for t in data["result"]["tools"]]
        assert "get_current_config" in tool_names
        assert "get_logs" in tool_names
        assert "update_config" in tool_names

    def test_获取token后_真实HTTP调用tools_call成功(self, server_app):
        """获取 access_token 后，TestClient 携带 Bearer 调用 /mcp tools/call 应成功。"""
        with self._make_test_client(server_app) as client:
            # OAuth 流程获取 token
            access_token = self._do_full_oauth_flow(client)

            # 初始化 session
            session_id = self._initialize_session(client, access_token)

            # 调用 tools/call
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "get_current_config", "arguments": {}},
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Mcp-Session-Id": session_id,
                },
            )

        assert response.status_code == 200, (
            f"真实 HTTP 调用应返回 200，实际: {response.status_code}, body: {response.text[:200]}"
        )
        # 解析 SSE 响应
        body = response.text
        data_line = [line for line in body.split("\n") if line.startswith("data: ")]
        assert len(data_line) > 0
        import json as _json

        data = _json.loads(data_line[0].removeprefix("data: "))
        assert "result" in data
        content = data["result"].get("content", [])
        assert len(content) > 0
        tool_result = _json.loads(content[0]["text"])
        assert tool_result["status"] == "success"
        assert "data" in tool_result

    def test_真实HTTP_无token调用工具返回401(self, server_app):
        """未携带 Bearer Token 调用 /mcp 应返回 401。"""
        with self._make_test_client(server_app) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "get_current_config", "arguments": {}},
                },
            )

        assert response.status_code == 401

    def test_真实HTTP_无效token调用工具返回401(self, server_app):
        """携带无效 Bearer Token 调用 /mcp 应返回 401。"""
        with self._make_test_client(server_app) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "get_current_config", "arguments": {}},
                },
                headers={"Authorization": "Bearer invalid.token.here"},
            )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 4. /.well-known 端点含 client_id_metadata_document_supported: true
# ---------------------------------------------------------------------------


class TestWellKnownEndpoints:
    """验证 OAuth 发现文档端点。"""

    @pytest.mark.asyncio
    async def test_metadata_包含_cimd_support_flag(self):
        """/.well-known/oauth-authorization-server 应包含 client_id_metadata_document_supported: true。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()
        assert data.get("client_id_metadata_document_supported") is True

    @pytest.mark.asyncio
    async def test_metadata_包含必要字段(self):
        """metadata 应包含 issuer / authorization_endpoint / token_endpoint / registration_endpoint。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()
        assert "issuer" in data
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data

    @pytest.mark.asyncio
    async def test_metadata_声明scopes_supported为read_write(self):
        """AS metadata 与 PRM metadata 均应声明 scopes_supported == ["read", "write"]。

        ``scopes_supported`` 来自 provider 默认的
        ``ClientRegistrationOptions(valid_scopes=["read", "write"])``，是客户端发现
        可用 scope 的依据；两处（授权服务器 metadata 与受保护资源 metadata）必须同值，
        否则客户端拿到的 scope 契约会自相矛盾。此处用精确相等锁住契约，防止默认允许集
        被无声改动。
        """
        from app.mcp.server import create_mcp_app

        app = create_mcp_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            as_response = await client.get("/.well-known/oauth-authorization-server")
            prm_response = await client.get("/.well-known/oauth-protected-resource/mcp")

        assert as_response.status_code == 200
        assert prm_response.status_code == 200
        assert as_response.json().get("scopes_supported") == ["read", "write"], (
            "AS metadata.scopes_supported 应为 ['read', 'write']，实际: "
            f"{as_response.json().get('scopes_supported')}"
        )
        assert prm_response.json().get("scopes_supported") == ["read", "write"], (
            "PRM metadata.scopes_supported 应为 ['read', 'write']，实际: "
            f"{prm_response.json().get('scopes_supported')}"
        )

    @pytest.mark.asyncio
    async def test_protected_resource_metadata_可达(self):
        """/.well-known/oauth-protected-resource/mcp 应返回 200。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. 生产装配注册 /consent 路由
# ---------------------------------------------------------------------------


class TestProductionConsentRoute:
    """验证生产装配 create_mcp_app() 注册了 /consent 路由。"""

    def test_create_mcp_app_注册consent路由(self):
        """create_mcp_app() 返回的 app 应包含 /consent 路由。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app()
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/consent" in paths, f"生产装配应注册 /consent 路由，实际路径: {paths}"

    @pytest.mark.asyncio
    async def test_consent路由_GET_返回200或400(self):
        """GET /consent 在生产装配上应可达（非 404）。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 无 request_token → 400，但路由存在
            response = await client.get("/consent")

        assert response.status_code != 404, (
            f"/consent 路由应存在，实际状态码: {response.status_code}"
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 6. 生产装配支持注入自定义 provider
# ---------------------------------------------------------------------------


class TestProviderInjection:
    """验证 create_mcp_server / create_mcp_app 支持注入自定义 provider。

    注入后必须完全绕过 _resolve_base_url / _create_provider 的配置解析，
    同时仍走同一处装配逻辑注册 /consent 路由。
    """

    @pytest.fixture
    def custom_provider(self, tmp_path):
        """构造 issuer 为可辨识自定义值的 BangumiOAuthProvider。"""
        from app.mcp.keys import RSAKeyManager
        from app.mcp.provider import BangumiOAuthProvider

        rsa_manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        rsa_manager.load_or_generate()
        # 自定义 issuer 取一个既合法（SDK 要求非 loopback 必须 HTTPS）又
        # 与默认解析值（http://localhost:8000）可区分的主机。
        return BangumiOAuthProvider(
            base_url="https://custom-issuer.test:9000",
            rsa_manager=rsa_manager,
            issuer="https://custom-issuer.test:9000",
            audience="bangumi-syncer",
            auth_enabled=False,
            auth_username="admin",
        )

    def test_create_mcp_server_注入provider_返回FastMCP(self, custom_provider):
        """create_mcp_server(provider=...) 应接受注入并返回 FastMCP。"""
        from fastmcp import FastMCP

        from app.mcp.server import create_mcp_server

        mcp = create_mcp_server(provider=custom_provider)
        assert isinstance(mcp, FastMCP)

    @pytest.mark.asyncio
    async def test_注入provider_metadata使用自定义issuer(self, custom_provider):
        """注入 provider 后 metadata.issuer 应来自注入对象，而非配置/默认值。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app(provider=custom_provider)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        # AnyHttpUrl 会补尾斜杠，比较时归一化；关键是它未被默认解析值覆盖。
        issuer = response.json()["issuer"].rstrip("/")
        assert issuer == "https://custom-issuer.test:9000"

    def test_注入provider_仍注册consent路由(self, custom_provider):
        """即使注入 provider，create_mcp_app 返回的 app 仍应包含 /consent 路由。"""
        from app.mcp.server import create_mcp_app

        app = create_mcp_app(provider=custom_provider)
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/consent" in paths, (
            f"注入 provider 时也应注册 /consent，实际路径: {paths}"
        )


# ---------------------------------------------------------------------------
# S2: JWT 含 client_id → /revoke 生产路径可触发
# ---------------------------------------------------------------------------


class TestRevocationFlow:
    """验证 access_token 的 client_id 写入 JWT claims，/revoke 可触发。"""

    @pytest.fixture
    def tmp_keys(self, tmp_path):
        return {
            "private": str(tmp_path / "private.pem"),
            "public": str(tmp_path / "public.pem"),
        }

    @pytest.fixture
    def server_app(self, tmp_keys):
        return build_test_mcp_app(
            private_key_path=tmp_keys["private"],
            public_key_path=tmp_keys["public"],
            issuer="http://localhost:8000",
            audience="bangumi-syncer",
            auth_enabled=False,
            auth_username="admin",
        )

    def _make_test_client(self, app):
        return TestClient(app, raise_server_exceptions=False)

    def _do_full_oauth_flow(self, client) -> tuple[str, str]:
        """执行完整 OAuth 流程，返回 (access_token, client_id)。"""
        # 显式注册 read write scope，因默认 scope 已改为 read
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
                "scope": "read write",
            },
        )
        assert reg_response.status_code == 201
        client_id = reg_response.json()["client_id"]

        code_verifier = secrets.token_urlsafe(32)
        code_challenge = _make_code_challenge_b64(code_verifier)
        auth_response = client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://localhost/callback",
                "response_type": "code",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "read write",
                "state": "test-state",
            },
            follow_redirects=False,
        )
        assert auth_response.status_code == 302
        consent_url = auth_response.headers["location"]

        consent_get = client.get(consent_url)
        assert consent_get.status_code == 200
        csrf_token = _extract_csrf_token(consent_get.text)

        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        consent_post = client.post(
            "/consent",
            data={
                "action": "allow",
                "request_token": request_token,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert consent_post.status_code == 302
        redirect_url = consent_post.headers["location"]
        code = parse_qs(urlparse(redirect_url).query)["code"][0]

        token_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://localhost/callback",
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
        )
        assert token_response.status_code == 200
        return token_response.json()["access_token"], client_id

    @pytest.mark.asyncio
    async def test_access_token_的_client_id_与注册客户端一致(self, server_app):
        """load_access_token 返回的 client_id 应等于注册时的 client_id。"""
        client = self._make_test_client(server_app)
        access_token, client_id = self._do_full_oauth_flow(client)

        provider = server_app.state.provider
        loaded_token = await provider.load_access_token(access_token)
        assert loaded_token is not None
        assert loaded_token.client_id == client_id, (
            f"access_token.client_id 应等于注册 client_id {client_id}，"
            f"实际: {loaded_token.client_id!r}"
        )

    def test_revoke_access_token_后_调用工具返回401(self, server_app):
        """撤销 access_token 后，用该 token 调用 /mcp 工具应返回 401。"""
        with self._make_test_client(server_app) as client:
            access_token, client_id = self._do_full_oauth_flow(client)

            # 撤销前：token 应有效
            provider = server_app.state.provider
            import asyncio

            loaded = asyncio.run(provider.load_access_token(access_token))
            assert loaded is not None

            # 调用 /revoke 撤销 access_token
            revoke_response = client.post(
                "/revoke",
                data={
                    "token": access_token,
                    "client_id": client_id,
                    "client_secret": "",
                },
            )
            assert revoke_response.status_code == 200, (
                f"/revoke 应返回 200，实际: {revoke_response.status_code}, "
                f"body: {revoke_response.text}"
            )

            # 撤销后：token 应无效
            loaded_after = asyncio.run(provider.load_access_token(access_token))
            assert loaded_after is None, (
                "撤销后的 access_token 应无法通过 load_access_token 验签"
            )
