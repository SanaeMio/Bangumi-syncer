"""
CIMD 真实行为测试（对应评审 P1-5）

验证 CIMDClientManager 的真实抓取/校验逻辑，使用 mock ssrf_safe_fetch_response
（FastMCP CIMD 内部 HTTP 层）而非 mock get_client 本身。

覆盖 BDD 场景：
- 1.1: 合法 CIMD 文档 → 合成 client + scope 注入
- 1.3: client_id 不匹配 → 拒绝（返回 None）
- 1.5: loopback 端口灵活（RFC 8252 §7.3）
- 1.7: 抓取失败 → 返回 None（不崩溃）
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.server.auth.cimd import CIMDClientManager, CIMDDocument
from fastmcp.server.auth.ssrf import SSRFFetchError, SSRFFetchResponse
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import InvalidScopeError
from pydantic import AnyHttpUrl, AnyUrl

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

CIMD_URL = "https://claude.ai/oauth/claude-code-client-metadata"


def _make_cimd_doc_json(
    client_id: str = CIMD_URL,
    client_name: str = "Claude Code",
    redirect_uris: list[str] | None = None,
    token_endpoint_auth_method: str = "none",
    grant_types: list[str] | None = None,
    scope: str | None = "read write",
) -> bytes:
    """构造 CIMD 文档 JSON 字节。"""
    doc = {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris or ["http://localhost/callback"],
        "token_endpoint_auth_method": token_endpoint_auth_method,
        "grant_types": grant_types or ["authorization_code", "refresh_token"],
    }
    if scope is not None:
        doc["scope"] = scope
    return json.dumps(doc).encode()


def _make_ssrf_response(
    content: bytes,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> SSRFFetchResponse:
    """构造 SSRF 响应对象。"""
    return SSRFFetchResponse(
        content=content,
        status_code=status_code,
        headers=headers or {"content-type": "application/json"},
    )


# ---------------------------------------------------------------------------
# 1.1: 合法 CIMD 文档 → 合成 client + scope 注入
# ---------------------------------------------------------------------------


class TestCIMDRealFetch:
    """使用 mock ssrf_safe_fetch_response 验证真实抓取流程。"""

    @pytest.fixture
    def manager(self):
        return CIMDClientManager(enable_cimd=True, default_scope="read write")

    @pytest.mark.asyncio
    async def test_fetch_valid_document_returns_synthetic_client(self, manager):
        """合法 CIMD 文档应返回合成 ProxyDCRClient。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json())
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is not None
        assert result.client_id == CIMD_URL
        assert result.client_name == "Claude Code"
        assert result.token_endpoint_auth_method == "none"

    @pytest.mark.asyncio
    async def test_fetch_valid_document_preserves_grant_types(self, manager):
        """合成 client 应保留文档中的 grant_types。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json())
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is not None
        assert "authorization_code" in result.grant_types
        assert "refresh_token" in result.grant_types

    @pytest.mark.asyncio
    async def test_fetch_valid_document_attaches_cimd_document(self, manager):
        """合成 client 应附带 cimd_document 引用。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json())
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is not None
        assert result.cimd_document is not None
        assert isinstance(result.cimd_document, CIMDDocument)
        assert result.cimd_document.client_name == "Claude Code"

    @pytest.mark.asyncio
    async def test_fetch_valid_document_preserves_scope(self, manager):
        """文档含 scope 时应保留原始 scope。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json(scope="read write"))
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is not None
        assert result.scope == "read write"

    @pytest.mark.asyncio
    async def test_fetch_document_without_scope_injects_default(self, manager):
        """文档不含 scope 时应注入 default_scope。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json(scope=None))
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is not None
        assert result.scope == "read write"

    @pytest.mark.asyncio
    async def test_fetch_calls_ssrf_with_correct_url(self, manager):
        """应使用正确的 URL 调用 ssrf_safe_fetch_response。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json())
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            await manager.get_client(CIMD_URL)

        mock_fetch.assert_called_once()
        call_args = mock_fetch.call_args
        assert call_args[0][0] == CIMD_URL


# ---------------------------------------------------------------------------
# 1.3: client_id 不匹配 → 拒绝
# ---------------------------------------------------------------------------


class TestCIMDClientIdMismatch:
    """验证 client_id 不匹配时 get_client 返回 None。"""

    @pytest.fixture
    def manager(self):
        return CIMDClientManager(enable_cimd=True, default_scope="read write")

    @pytest.mark.asyncio
    async def test_fetch_client_id_mismatch_returns_none(self, manager):
        """文档 client_id 与 URL 不匹配时应返回 None。"""
        # 文档声明的 client_id 与请求 URL 不同
        evil_doc = _make_cimd_doc_json(
            client_id="https://evil.com/metadata",
            client_name="Evil Client",
        )
        mock_response = _make_ssrf_response(evil_doc)
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_client_id_trailing_slash_difference_ok(self, manager):
        """client_id 仅尾斜杠不同应视为匹配（Pydantic AnyHttpUrl 标准化）。"""
        # Pydantic AnyHttpUrl 会标准化尾斜杠
        doc_json = _make_cimd_doc_json(
            client_id=CIMD_URL + "/",  # 带尾斜杠
        )
        mock_response = _make_ssrf_response(doc_json)
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        # 尾斜杠差异在 CIMD 校验时被 rstrip("/") 处理，应匹配
        assert result is not None
        assert result.client_id == CIMD_URL


# ---------------------------------------------------------------------------
# 1.7: 抓取失败 → 返回 None（不崩溃）
# ---------------------------------------------------------------------------


class TestCIMDFetchFailure:
    """验证抓取失败时 get_client 返回 None 且不崩溃。"""

    @pytest.fixture
    def manager(self):
        return CIMDClientManager(enable_cimd=True, default_scope="read write")

    @pytest.mark.asyncio
    async def test_fetch_404_returns_none(self, manager):
        """ssrf_safe_fetch_response 返回 404 时应返回 None。"""
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = SSRFFetchError("404 Not Found")
            result = await manager.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_500_returns_none(self, manager):
        """ssrf_safe_fetch_response 返回 500 时应返回 None。"""
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = SSRFFetchError("500 Internal Server Error")
            result = await manager.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_network_error_returns_none(self, manager):
        """网络错误（连接超时等）时应返回 None。"""
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = SSRFFetchError("Connection timed out")
            result = await manager.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_invalid_json_returns_none(self, manager):
        """响应非有效 JSON 时应返回 None。"""
        mock_response = _make_ssrf_response(b"not json at all")
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_missing_required_fields_returns_none(self, manager):
        """文档缺少必填字段（如 redirect_uris）时应返回 None。"""
        bad_doc = json.dumps(
            {
                "client_id": CIMD_URL,
                "client_name": "Bad Client",
                # 缺少 redirect_uris
            }
        ).encode()
        mock_response = _make_ssrf_response(bad_doc)
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_forbidden_auth_method_returns_none(self, manager):
        """文档使用禁止的 auth 方法（client_secret_post）时应返回 None。"""
        bad_doc = _make_cimd_doc_json(token_endpoint_auth_method="client_secret_post")
        mock_response = _make_ssrf_response(bad_doc)
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await manager.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_repeated_failures_do_not_crash(self, manager):
        """连续多次抓取失败不应崩溃，每次都返回 None。"""
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = SSRFFetchError("503 Service Unavailable")
            for _ in range(5):
                result = await manager.get_client(CIMD_URL)
                assert result is None


# ---------------------------------------------------------------------------
# 1.5: loopback 端口灵活（RFC 8252 §7.3）
# ---------------------------------------------------------------------------


class TestCIMDLoopbackPortFlexibility:
    """验证 loopback redirect_uri 的端口灵活性。"""

    @pytest.fixture
    def manager(self):
        return CIMDClientManager(enable_cimd=True, default_scope="read write")

    def _make_doc(self, redirect_uris: list[str]) -> CIMDDocument:
        return CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/metadata"),
            redirect_uris=redirect_uris,
            token_endpoint_auth_method="none",
            grant_types=["authorization_code"],
        )

    def test_loopback_no_port_in_doc_matches_any_port(self, manager):
        """文档声明 http://127.0.0.1/callback（无端口），请求 :54321 → 匹配。"""
        doc = self._make_doc(["http://127.0.0.1/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "http://127.0.0.1:54321/callback"
            )
            is True
        )

    def test_loopback_no_port_in_doc_matches_different_port(self, manager):
        """文档声明 http://127.0.0.1/callback（无端口），请求 :1234 → 匹配。"""
        doc = self._make_doc(["http://127.0.0.1/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "http://127.0.0.1:1234/callback"
            )
            is True
        )

    def test_loopback_exact_match(self, manager):
        """文档声明 http://127.0.0.1:8080/callback，请求 :8080 → 匹配。"""
        doc = self._make_doc(["http://127.0.0.1:8080/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "http://127.0.0.1:8080/callback"
            )
            is True
        )

    def test_loopback_wrong_port_rejected(self, manager):
        """文档声明 http://127.0.0.1:8080/callback，请求 :9999 → 拒绝。"""
        doc = self._make_doc(["http://127.0.0.1:8080/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "http://127.0.0.1:9999/callback"
            )
            is False
        )

    def test_loopback_wildcard_port_pattern(self, manager):
        """文档声明 http://127.0.0.1:* /callback → 匹配任意端口。"""
        doc = self._make_doc(["http://127.0.0.1:*/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "http://127.0.0.1:54321/callback"
            )
            is True
        )

    def test_localhost_no_port_matches_any_port(self, manager):
        """文档声明 http://localhost/callback（无端口），请求 :54321 → 匹配。"""
        doc = self._make_doc(["http://localhost/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "http://localhost:54321/callback"
            )
            is True
        )

    def test_non_loopback_port_must_match(self, manager):
        """非 loopback 地址端口必须精确匹配。"""
        doc = self._make_doc(["https://app.example.com:8443/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "https://app.example.com:8443/callback"
            )
            is True
        )
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "https://app.example.com:9999/callback"
            )
            is False
        )

    def test_loopback_path_mismatch_rejected(self, manager):
        """loopback 但 path 不同 → 拒绝。"""
        doc = self._make_doc(["http://127.0.0.1/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(doc, "http://127.0.0.1:54321/other")
            is False
        )

    def test_loopback_scheme_mismatch_rejected(self, manager):
        """loopback 但 scheme 不同 → 拒绝。"""
        doc = self._make_doc(["http://127.0.0.1/callback"])
        assert (
            manager._fetcher.validate_redirect_uri(
                doc, "https://127.0.0.1:54321/callback"
            )
            is False
        )


# ---------------------------------------------------------------------------
# 集成：provider.cimd.get_client 完整路径
# ---------------------------------------------------------------------------


class TestCIMDProviderIntegration:
    """验证 provider.cimd.get_client 的完整路径（通过 provider 接入）。"""

    @pytest.fixture
    def provider(self, tmp_path):
        from app.mcp.keys import RSAKeyManager
        from app.mcp.provider import BangumiOAuthProvider

        rsa_manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        rsa_manager.generate_keys()
        return BangumiOAuthProvider(
            base_url="http://localhost:8000",
            rsa_manager=rsa_manager,
            issuer="http://localhost:8000",
            audience="bs",
            token_expiry_seconds=3600,
            auth_enabled=False,
            auth_username="admin",
        )

    @pytest.mark.asyncio
    async def test_provider_get_client_cimd_real_fetch(self, provider):
        """provider.get_client 应走 CIMD 分支并返回合成 client。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json())
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await provider.get_client(CIMD_URL)

        assert result is not None
        assert result.client_id == CIMD_URL
        assert result.client_name == "Claude Code"
        assert result.scope == "read write"

    @pytest.mark.asyncio
    async def test_provider_get_client_cimd_mismatch_returns_none(self, provider):
        """client_id 不匹配时 provider.get_client 应返回 None。"""
        evil_doc = _make_cimd_doc_json(client_id="https://evil.com/metadata")
        mock_response = _make_ssrf_response(evil_doc)
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await provider.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_provider_get_client_cimd_fetch_failure_returns_none(self, provider):
        """抓取失败时 provider.get_client 应返回 None。"""
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = SSRFFetchError("500 Server Error")
            result = await provider.get_client(CIMD_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_provider_get_client_cimd_scope_injection(self, provider):
        """文档无 scope 时 provider 应注入 CIMD 允许集（read write）。

        回归 Docker E2E P1：Claude Code 的 CIMD 文档没有 scope，authorize 请求
        带 scope=read。SDK 用合成 client 的 scope 作为允许集校验，若注入的是
        read 之外的请求会被拒（invalid_scope）。
        """
        mock_response = _make_ssrf_response(_make_cimd_doc_json(scope=None))
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            result = await provider.get_client(CIMD_URL)

        assert result is not None
        assert result.scope == "read write"

    @pytest.mark.asyncio
    async def test_provider_cimd_client_validate_scope_allows_read_write(
        self, provider
    ):
        """文档无 scope 时，合成 client.validate_scope 应放行 read write。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json(scope=None))
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            client = await provider.get_client(CIMD_URL)

        assert client is not None
        # 允许集覆盖 read write，SDK authorize handler 不再抛 invalid_scope
        assert client.validate_scope("read write") == ["read", "write"]
        # 超出允许集仍应拒绝
        with pytest.raises(InvalidScopeError):
            client.validate_scope("admin")
        # 未请求 scope → None（SDK 传给 provider 的 scopes=None）
        assert client.validate_scope(None) is None

    @pytest.mark.asyncio
    async def test_provider_authorize_with_full_scope_grants_requested(self, provider):
        """CIMD authorize 带 scope=read write 应通过校验并原样发放。

        边界说明：此处用合成 client + validate_scope + provider.authorize 的
        单元级组合模拟 SDK authorize handler 的调用序列；完整 HTTP 路由需
        真实 CIMD 抓取，成本高，故不在此覆盖。
        """
        mock_response = _make_ssrf_response(_make_cimd_doc_json(scope=None))
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            client = await provider.get_client(CIMD_URL)
            assert client is not None
            # 模拟 SDK handler：先校验 scope（修复前此处抛 InvalidScopeError）
            scopes = client.validate_scope("read write")
            params = AuthorizationParams(
                state="xyz",
                scopes=scopes,
                code_challenge="challenge",
                redirect_uri=AnyUrl("http://localhost/callback"),
                redirect_uri_provided_explicitly=True,
            )
            redirect_url = await provider.authorize(client, params)

        request_token = redirect_url.split("request_token=", 1)[1]
        pending = provider._pending_auths[request_token]
        assert pending["scopes"] == ["read", "write"]

    @pytest.mark.asyncio
    async def test_provider_authorize_without_scope_grants_read_only(self, provider):
        """CIMD authorize 未带 scope 时仍只发放 read（安全默认不变）。"""
        mock_response = _make_ssrf_response(_make_cimd_doc_json(scope=None))
        with patch(
            "fastmcp.server.auth.cimd.ssrf_safe_fetch_response",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = mock_response
            client = await provider.get_client(CIMD_URL)
            assert client is not None
            scopes = client.validate_scope(None)
            assert scopes is None
            params = AuthorizationParams(
                state=None,
                scopes=scopes,
                code_challenge="challenge",
                redirect_uri=AnyUrl("http://localhost/callback"),
                redirect_uri_provided_explicitly=True,
            )
            redirect_url = await provider.authorize(client, params)

        request_token = redirect_url.split("request_token=", 1)[1]
        pending = provider._pending_auths[request_token]
        assert pending["scopes"] == ["read"]
