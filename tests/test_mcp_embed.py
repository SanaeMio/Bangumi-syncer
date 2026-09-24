"""
FastMCP 嵌入骨架测试

覆盖 BDD 场景：
1. /mcp 端点存在（未认证 / 无效 Bearer → 401）
2. 未匹配路径返回 JSON 404（与 FastAPI 语义一致）
3. /.well-known/oauth-authorization-server 在根路径可达（200）
4. /authorize、/token 在根路径存在（不 404）、/consent 缺参返回 400（可达）
5. 现有路由（/health、/openapi.json、/docs、/redoc）未被 catch-all 破坏
6. lifespan 合并后可正常启停
7. 生产组合携带合法 Bearer 可调用 /mcp
"""

import base64
import hashlib
import re
import secrets
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient  # noqa: I001

from tests.mcp_helpers import build_test_mcp_app

# ---------------------------------------------------------------------------
# 1. /mcp 端点存在且鉴权链路生效
# ---------------------------------------------------------------------------


class TestMcpEndpointExists:
    """验证 /mcp 端点注册到生产 app，且未认证 / 未匹配路径语义正确。"""

    def test_mcp_endpoint_no_bearer_returns_401(self, embed_mocks):
        """不带 Authorization 请求 /mcp 应返回 401（Mount 后鉴权链路生效）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/mcp")
            assert response.status_code == 401, (
                f"/mcp 未认证应返回 401，实际: {response.status_code}"
            )

    def test_mcp_endpoint_invalid_bearer_returns_401(self, embed_mocks):
        """携带无效 Bearer 请求 /mcp 应返回 401。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get(
                    "/mcp", headers={"Authorization": "Bearer invalid-token"}
                )
            assert response.status_code == 401, (
                f"/mcp 无效 Bearer 应返回 401，实际: {response.status_code}"
            )

    def test_unmatched_path_returns_json_404(self, embed_mocks):
        """未匹配路径应返回与 FastAPI 一致的 JSON 404（非 text/plain）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/no-such-route-xyz")
            assert response.status_code == 404, (
                f"未匹配路径应返回 404，实际: {response.status_code}"
            )
            assert response.headers.get("content-type", "").startswith(
                "application/json"
            ), (
                f"应为 JSON 404，实际 content-type: {response.headers.get('content-type')}"
            )
            assert response.json() == {"detail": "Not Found"}


# ---------------------------------------------------------------------------
# 2. /.well-known 路由在根路径
# ---------------------------------------------------------------------------


class TestWellKnownRoutesAtRoot:
    """验证 OAuth 发现文档在根路径可达。"""

    def test_oauth_authorization_server_metadata_at_root(self, embed_mocks):
        """/.well-known/oauth-authorization-server 在根路径返回 200。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/.well-known/oauth-authorization-server")
            assert response.status_code == 200, (
                f"应在根路径返回 200，实际: {response.status_code}"
            )

    def test_oauth_protected_resource_metadata_at_root(self, embed_mocks):
        """/.well-known/oauth-protected-resource/mcp 在根路径返回 200。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200, (
                f"应在根路径返回 200，实际: {response.status_code}"
            )


# ---------------------------------------------------------------------------
# 3. /authorize 和 /token 在根路径
# ---------------------------------------------------------------------------


class TestOperationalRoutesAtRoot:
    """验证 /authorize、/token 和 /consent 在根路径存在。"""

    def test_authorize_not_404(self, embed_mocks):
        """/authorize 在根路径存在（不 404）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/authorize")
            assert response.status_code != 404, "/authorize 不应返回 404"

    def test_token_not_404(self, embed_mocks):
        """/token 在根路径存在（不 404）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/token")
            assert response.status_code != 404, "/token 不应返回 404"

    def test_consent_get_without_request_token_returns_400(self, embed_mocks):
        """GET /consent（缺 request_token）应返回 400，确认端点未被 Mount 吞掉。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/consent")
            assert response.status_code == 400, (
                f"/consent 缺参应返回 400（可达但缺参），实际: {response.status_code}"
            )


# ---------------------------------------------------------------------------
# 4. 现有路由未被破坏
# ---------------------------------------------------------------------------


class TestExistingRoutesNotBroken:
    """验证现有 25 个 router + 中间件未被破坏。"""

    def test_health_endpoint_still_works(self, embed_mocks):
        """GET /health 仍返回 200。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/health")
            assert response.status_code == 200
            assert response.json().get("status") == "healthy"

    def test_app_has_many_routes(self, embed_mocks):
        """app 应保留大量现有路由（远多于新增的 MCP 路由）。"""
        with embed_mocks():
            from app.main import app

            paths = [route.path for route in app.routes if hasattr(route, "path")]
            # 实测 0bc1293 之后 len(paths) 约 164（25 个 router + 静态/文档 + MCP 子应用）。
            # 阈值取 100：贴近实际，且 catch-all 意外吞掉整批路由时能真正报警。
            assert len(paths) >= 100, f"路由数量应 >= 100，实际: {len(paths)}"

    def test_fastapi_builtin_doc_routes_not_swallowed(self, embed_mocks):
        """GET /openapi.json、/docs、/redoc 均返回 200，确认 root catch-all 未吞掉自带路由。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                for path in ("/openapi.json", "/docs", "/redoc"):
                    response = client.get(path)
                    assert response.status_code == 200, (
                        f"{path} 应返回 200，实际: {response.status_code}"
                    )


# ---------------------------------------------------------------------------
# 5. lifespan 合并
# ---------------------------------------------------------------------------


class TestLifespanCombination:
    """验证 lifespan 合并后正常启停。"""

    @pytest.mark.asyncio
    async def test_combined_lifespan_starts_and_stops(self, embed_mocks):
        """合并后的 lifespan 应能正常进入和退出。"""
        with embed_mocks():
            from app.main import lifespan

            @asynccontextmanager
            async def test_ls(app):
                yield

            from fastapi import FastAPI

            test_app = FastAPI()

            async with lifespan(test_app):
                pass


# ---------------------------------------------------------------------------
# 6. 生产组合：携带合法 Bearer 调用 /mcp 必须成功
# ---------------------------------------------------------------------------


def _make_code_challenge_b64(verifier: str) -> str:
    """由 verifier 生成 S256 code challenge（base64url，无填充）。"""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _extract_csrf_token(html_content: str) -> str:
    """从 consent 表单 HTML 中提取 CSRF token。"""
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html_content)
    if not match:
        raise ValueError("CSRF token not found in consent form")
    return match.group(1)


def _do_full_oauth_flow(
    client, session_token: str | None = None, scope: str = "read write"
) -> str:
    """在生产组合上走完 OAuth 流程，返回 access_token。

    session_token 仅用于 auth.enabled=True 时通过 /consent 的 Web 会话校验。
    scope 同时用于 DCR 注册与 /authorize 请求，据此可构造只含特定 scope 的
    合法 token（如 scope="write" 得到仅含 write 的 token）。
    """
    reg_response = client.post(
        "/register",
        json={
            "redirect_uris": ["http://localhost/callback"],
            "grant_types": ["authorization_code"],
            "token_endpoint_auth_method": "none",
            "scope": scope,
        },
    )
    assert reg_response.status_code == 201, (
        f"DCR 应返回 201，实际: {reg_response.status_code}, body: {reg_response.text[:200]}"
    )
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
            "scope": scope,
            "state": "test-state",
        },
        follow_redirects=False,
    )
    assert auth_response.status_code == 302, (
        f"/authorize 应返回 302，实际: {auth_response.status_code}, body: {auth_response.text[:200]}"
    )
    consent_url = auth_response.headers["location"]

    cookies = {"session_token": session_token} if session_token else None
    consent_get = client.get(consent_url, cookies=cookies)
    assert consent_get.status_code == 200, (
        f"/consent GET 应返回 200，实际: {consent_get.status_code}, body: {consent_get.text[:200]}"
    )
    csrf_token = _extract_csrf_token(consent_get.text)
    request_token = parse_qs(urlparse(consent_url).query)["request_token"][0]

    consent_post = client.post(
        "/consent",
        data={
            "action": "allow",
            "request_token": request_token,
            "csrf_token": csrf_token,
        },
        cookies=cookies,
        follow_redirects=False,
    )
    assert consent_post.status_code == 302, (
        f"/consent POST 应返回 302，实际: {consent_post.status_code}, body: {consent_post.text[:200]}"
    )
    code = parse_qs(urlparse(consent_post.headers["location"]).query)["code"][0]

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
    assert token_response.status_code == 200, (
        f"/token 应返回 200，实际: {token_response.status_code}, body: {token_response.text[:200]}"
    )
    return token_response.json()["access_token"]


class TestProductionAppMcpCall:
    """验证生产组合（app.main.app 挂载 MCP 子应用后）携带合法 Bearer 能调用 /mcp。"""

    def test_生产App_携带合法Bearer_调用mcp成功(self, monkeypatch, embed_mocks):
        """生产 app 上走完 OAuth 后，携带 Bearer 调 /mcp initialize 应返回 200。

        修复前：由于 mcp_app.user_middleware 未迁移，/mcp 端点的
        RequireAuthMiddleware 读不到 scope["user"]，永远返回 401。
        """
        from app.mcp import provider as provider_module

        # 生产配置 auth.enabled=True，/consent 需要 Web 会话；打桩会话校验，
        # 仅绕过 Web 会话检查，不影响 Bearer JWT 的验签链路。
        monkeypatch.setattr(
            provider_module.security_manager,
            "validate_session",
            lambda token: {"username": "admin", "created_at": 0},
        )

        with embed_mocks():
            from app.main import app

            with TestClient(app, raise_server_exceptions=False) as client:
                access_token = _do_full_oauth_flow(
                    client, session_token="valid-session-token"
                )

                response = client.post(
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

                assert response.status_code == 200, (
                    f"携带合法 Bearer 调用 /mcp 应返回 200，实际: {response.status_code}, "
                    f"body: {response.text[:300]}"
                )
                assert response.headers.get("mcp-session-id") is not None
                session_id = response.headers["mcp-session-id"]

                # 进一步验证 get_access_token() 依赖的 contextvar 链路：
                # tools/call 内部要求解析出含 read scope 的 access token。
                tool_response = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "get_current_config", "arguments": {}},
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Mcp-Session-Id": session_id,
                    },
                )

        assert tool_response.status_code == 200, (
            f"携带合法 Bearer 调用 tools/call 应返回 200，实际: {tool_response.status_code}, "
            f"body: {tool_response.text[:300]}"
        )


# ---------------------------------------------------------------------------
# 7. 生产组合：传输层 scope 准入（write-only token 必须被 403 拒绝）
# ---------------------------------------------------------------------------


class TestMcpTransportScopeAdmission:
    """验证 /mcp 传输层的 scope 准入门槛（默认 read，可显式关闭）。

    默认（生产单例 ``app.main``）required_scopes=["read"]：仅含 write 的 token 是
    「认证通过但授权不足」——必须返回 403（而非 401，也不是放行 200），并携带
    scope 挑战头指引客户端补足 read。

    对照方向：provider 以 ``required_scopes=[]`` 显式关闭门槛时，同一枚仅含 write
    的合法 token 必须被放行（非 403），证明 ``[]`` 是真正的关闭开关而非被默认值兜底。
    """

    def test_仅含write的合法token_访问mcp_返回403且挑战read(
        self, monkeypatch, embed_mocks
    ):
        """仅含 write 的合法 JWT 请求 /mcp → 403 + insufficient_scope + scope="read" 挑战头。

        403（而非 401）证明 token 验签通过，只是 scope 不满足 required_scopes=["read"]；
        WWW-Authenticate 必须同时给出 error 与 scope 挑战，客户端才能据此重新授权。
        """
        from app.mcp import provider as provider_module

        # 生产配置 auth.enabled=True，/consent 需要 Web 会话；打桩会话校验，
        # 仅绕过 Web 会话检查，不影响 Bearer JWT 的验签链路。
        monkeypatch.setattr(
            provider_module.security_manager,
            "validate_session",
            lambda token: {"username": "admin", "created_at": 0},
        )

        with embed_mocks():
            from app.main import app

            with TestClient(app, raise_server_exceptions=False) as client:
                write_only_token = _do_full_oauth_flow(
                    client, session_token="valid-session-token", scope="write"
                )

                response = client.post(
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
                    headers={"Authorization": f"Bearer {write_only_token}"},
                )

        assert response.status_code == 403, (
            "仅含 write 的合法 Bearer 应因 scope 不足返回 403，实际: "
            f"{response.status_code}, body: {response.text[:300]}"
        )
        assert response.json().get("error") == "insufficient_scope", (
            f"403 响应体应携带 error=insufficient_scope，实际: {response.text[:300]}"
        )
        www_authenticate = response.headers.get("www-authenticate", "")
        assert www_authenticate.startswith("Bearer"), (
            f"401/403 响应应携带 Bearer 挑战头，实际: {www_authenticate!r}"
        )
        assert 'scope="read"' in www_authenticate, (
            f'挑战头应声明 scope="read"，实际: {www_authenticate!r}'
        )

    def test_空required_scopes时_仅含write的合法token_访问mcp不被403(self, tmp_path):
        """provider 以 required_scopes=[] 挂载时，仅含 write 的合法 token 应放行 /mcp。

        与上方「默认 required_scopes=["read"] → write-only 403」形成正反对照：
        证明 ``required_scopes=[]`` 确实关闭了传输层 scope 门槛（认证仍生效，
        只是跳过 scope 校验），而不是被默认值兜底成 ``["read"]``。

        装配走 ``tests.mcp_helpers.build_test_mcp_app(required_scopes=[])``：它复用
        生产装配入口 ``create_mcp_app``，且能表达生产单例 ``app.main``（固定
        required_scopes=["read"]）无法表达的 ``[]`` 配置。token 经完整 OAuth 链路
        （DCR → authorize → consent → token）真实签发，scope 为 write，不做 mock。
        """
        app = build_test_mcp_app(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
            issuer="http://localhost:8000",
            audience="bangumi-syncer",
            auth_enabled=False,
            auth_username="admin",
            required_scopes=[],
        )

        with TestClient(app, raise_server_exceptions=False) as client:
            write_only_token = _do_full_oauth_flow(client, scope="write")

            response = client.post(
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
                headers={"Authorization": f"Bearer {write_only_token}"},
            )

        assert response.status_code == 200, (
            "required_scopes=[] 应放行仅含 write 的合法 token（非 403），实际: "
            f"{response.status_code}, body: {response.text[:300]}"
        )
