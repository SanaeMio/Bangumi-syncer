"""
FastMCP OAuthProvider（app.mcp.provider）的测试。

覆盖 T3（完整 OAuthProvider）、T4（CIMD 集成）、T5（DCR 回退）：
- RSA 密钥管理（生成、加载、签名、验签）
- DCR（Dynamic Client Registration）：默认 scope 与数量上限
- authorize 端点：auth.enabled 分支
- consent 页面（allow/deny）与 CSRF 保护
- token 端点（authorization_code、refresh_token）
- JWT claims 校验（RS256、sub/scope/iss/aud/exp）
- CIMD：URL client_id 识别、scope 注入、元数据注入
- 完整端到端 OAuth 流程
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import AnyHttpUrl
from starlette.testclient import TestClient

from app.core.public_url import get_public_base_path

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _generate_keypair():
    """生成全新的 RSA 密钥对（测试用）。"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


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
# RSA 密钥管理器测试
# ---------------------------------------------------------------------------


class TestRSAKeyManager:
    """RSA 密钥的生成、持久化，以及 JWT 签名/验签。"""

    def test_generate_creates_valid_keypair(self, tmp_path):
        """generate_keys 应生成合法的 RSA 密钥对。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"
        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.generate_keys()

        assert private_path.exists()
        assert public_path.exists()

        # 验证密钥可用于签名/验签
        private_pem = private_path.read_bytes()
        public_pem = public_path.read_bytes()

        token = jwt.encode({"sub": "test"}, private_pem, algorithm="RS256")
        decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
        assert decoded["sub"] == "test"

    def test_load_or_generate_creates_on_first_run(self, tmp_path):
        """load_or_generate 在密钥不存在时应创建密钥。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"
        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.load_or_generate()

        assert private_path.exists()
        assert public_path.exists()

    def test_load_or_generate_loads_existing(self, tmp_path):
        """load_or_generate 不应覆盖已存在的密钥。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"

        # 预先生成密钥
        private_pem, public_pem = _generate_keypair()
        private_path.write_bytes(private_pem)
        public_path.write_bytes(public_pem)

        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.load_or_generate()

        # 密钥应保持不变
        assert private_path.read_bytes() == private_pem
        assert public_path.read_bytes() == public_pem

    def test_load_or_generate_recovers_missing_public_key(self, tmp_path):
        """私钥存在 + 公钥缺失：重新派生公钥，保留私钥。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"

        # 磁盘上只存在私钥。
        private_pem, _ = _generate_keypair()
        private_path.write_bytes(private_pem)
        assert not public_path.exists()

        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.load_or_generate()

        # 私钥必须逐字节保持不变（不得静默重新生成）。
        assert private_path.read_bytes() == private_pem
        # 私钥文件保持仅属主权限。
        assert (private_path.stat().st_mode & 0o777) == 0o600

        # 公钥由已存在的私钥重新派生。
        assert public_path.exists()
        derived_public = (
            serialization.load_pem_private_key(private_pem, password=None)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        assert public_path.read_bytes() == derived_public

        # 恢复出的密钥对可用。
        token = manager.sign_jwt({"sub": "recover"})
        decoded = jwt.decode(token, manager.get_public_key_pem(), algorithms=["RS256"])
        assert decoded["sub"] == "recover"

    def test_load_or_generate_generates_pair_when_private_missing(self, tmp_path):
        """私钥缺失：生成一对全新的匹配密钥。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"

        # 陈旧的公钥仍存在但私钥已丢失 -> 必须重新生成。
        _, stale_public_pem = _generate_keypair()
        public_path.write_bytes(stale_public_pem)

        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.load_or_generate()

        assert private_path.exists()
        assert public_path.exists()
        assert private_path.read_bytes() != b""
        assert (private_path.stat().st_mode & 0o777) == 0o600
        # 公钥现在必须匹配新生成的私钥，而不是陈旧的那把。
        assert public_path.read_bytes() != stale_public_pem

        token = manager.sign_jwt({"sub": "fresh"})
        decoded = jwt.decode(token, manager.get_public_key_pem(), algorithms=["RS256"])
        assert decoded["sub"] == "fresh"

    def test_public_key_pem_returns_public_key_bytes(self, tmp_path):
        """get_public_key_pem 应返回 PEM 格式的公钥。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"
        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.generate_keys()

        pub_pem = manager.get_public_key_pem()
        assert b"BEGIN PUBLIC KEY" in pub_pem

    def test_sign_jwt_creates_valid_jwt(self, tmp_path):
        """sign_jwt 应生成以 RS256 签名的 JWT。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"
        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.generate_keys()

        claims = {"sub": "user1", "scope": "read write"}
        token = manager.sign_jwt(claims)

        # 用公钥验签
        public_pem = manager.get_public_key_pem()
        decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
        assert decoded["sub"] == "user1"
        assert decoded["scope"] == "read write"

    def test_verify_jwt_rejects_expired(self, tmp_path):
        """verify_jwt 对已过期的 JWT 应返回 None。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"
        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.generate_keys()

        claims = {"sub": "user1", "exp": int(time.time()) - 100}
        token = manager.sign_jwt(claims)
        assert manager.verify_jwt(token) is None

    def test_verify_jwt_rejects_bad_signature(self, tmp_path):
        """verify_jwt 对签名错误的 JWT 应返回 None。"""
        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"
        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )
        manager.generate_keys()

        other_private, _ = _generate_keypair()
        claims = {"sub": "user1", "exp": int(time.time()) + 3600}
        token = jwt.encode(claims, other_private, algorithm="RS256")
        assert manager.verify_jwt(token) is None

    def test_verify_jwt_过期token_返回None并记录warning(self, tmp_path, caplog):
        """verify_jwt 对过期 token 应返回 None，并记录 WARNING 级 ExpiredSignatureError。"""
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()

        token = manager.sign_jwt({"sub": "user1", "exp": int(time.time()) - 100})

        with caplog.at_level(logging.WARNING, logger="app.mcp.keys"):
            result = manager.verify_jwt(token)

        assert result is None
        # 锁定日志级别 + 异常类型名，不依赖中文文案
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warning_records, "过期 token 必须记录 WARNING 级别日志"
        assert any("ExpiredSignatureError" in r.getMessage() for r in warning_records)
        # 中文关键词作为附加语义确认
        assert "过期" in caplog.text
        # 严禁把 token 原文写进日志
        assert token not in caplog.text

    def test_verify_jwt_签名错误token_返回None并记录验证失败warning(
        self, tmp_path, caplog
    ):
        """verify_jwt 对签名错误 token 应返回 None，并记录 WARNING 级 InvalidSignatureError。

        日志只允许打印异常类型名，不得泄露 token 原文。
        """
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()

        other_private, _ = _generate_keypair()
        token = jwt.encode(
            {"sub": "user1", "exp": int(time.time()) + 3600},
            other_private,
            algorithm="RS256",
        )

        with caplog.at_level(logging.WARNING, logger="app.mcp.keys"):
            result = manager.verify_jwt(token)

        assert result is None
        # 锁定日志级别 + 异常类型名，不依赖中文文案
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warning_records, "签名错误 token 必须记录 WARNING 级别日志"
        assert any("InvalidSignatureError" in r.getMessage() for r in warning_records)
        # 中文关键词作为附加语义确认
        assert "验证失败" in caplog.text
        assert token not in caplog.text

    def test_verify_jwt_audience不匹配_返回None并记录warning(self, tmp_path, caplog):
        """verify_jwt 在 audience 不匹配时应返回 None，并记录 InvalidAudienceError 的 warning。"""
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()

        token = manager.sign_jwt(
            {
                "sub": "user1",
                "exp": int(time.time()) + 3600,
                "aud": "wrong-aud",
            }
        )

        with caplog.at_level(logging.WARNING, logger="app.mcp.keys"):
            result = manager.verify_jwt(token, audience="bangumi-syncer")

        assert result is None
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warning_records, "audience 不匹配必须记录 WARNING 级别日志"
        assert any("InvalidAudienceError" in r.getMessage() for r in warning_records)
        assert "验证失败" in caplog.text
        # 严禁把 token 原文写进日志
        assert token not in caplog.text

    def test_verify_jwt_audience匹配_返回claims(self, tmp_path):
        """verify_jwt 在 audience 匹配时应返回 claims 且 sub 正确。"""
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()

        token = manager.sign_jwt(
            {
                "sub": "user1",
                "exp": int(time.time()) + 3600,
                "aud": "bangumi-syncer",
            }
        )

        result = manager.verify_jwt(token, audience="bangumi-syncer")

        assert result is not None
        assert result["sub"] == "user1"
        assert result["aud"] == "bangumi-syncer"

    def test_verify_jwt_audience为None_跳过校验(self, tmp_path):
        """verify_jwt 在 audience=None 时应跳过受众校验，携带任意 aud 也返回 claims。"""
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()

        token = manager.sign_jwt(
            {
                "sub": "user1",
                "exp": int(time.time()) + 3600,
                "aud": "some-other-audience",
            }
        )

        result = manager.verify_jwt(token, audience=None)

        assert result is not None
        assert result["sub"] == "user1"

    def test_load_non_rsa_private_key_raises_value_error(self, tmp_path):
        """加载非 RSA 私钥（Ed25519）时应抛出清晰的 ValueError，而非静默赋值。"""
        from cryptography.hazmat.primitives.asymmetric import ed25519

        from app.mcp.keys import RSAKeyManager

        private_path = tmp_path / "private.pem"
        public_path = tmp_path / "public.pem"
        ed_key = ed25519.Ed25519PrivateKey.generate()
        private_path.write_bytes(
            ed_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        manager = RSAKeyManager(
            private_key_path=str(private_path),
            public_key_path=str(public_path),
        )

        with pytest.raises(ValueError, match="不是 RSA 私钥"):
            manager.load_or_generate()


# ---------------------------------------------------------------------------
# OAuth Provider 单元测试（T3）
# ---------------------------------------------------------------------------


class TestOAuthProviderUnit:
    """BangumiOAuthProvider 的单元测试（不启动完整 server）。"""

    @pytest.fixture
    def rsa_manager(self, tmp_path):
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()
        return manager

    @pytest.fixture
    def provider(self, rsa_manager):
        from app.mcp.provider import BangumiOAuthProvider

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
    async def test_get_client_returns_none_for_unknown(self, provider):
        """get_client 对未知 client_id 应返回 None。"""
        result = await provider.get_client("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_register_and_get_client(self, provider):
        """register_client 应存储 client；get_client 应能取回它。"""
        from mcp.shared.auth import OAuthClientInformationFull

        client_info = OAuthClientInformationFull(
            client_id="test-dcr-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="client_secret_post",
        )
        await provider.register_client(client_info)

        # client 应带有密钥（用于 client_secret_post）
        assert client_info.client_secret

        # 取回它
        retrieved = await provider.get_client("test-dcr-client")
        assert retrieved is not None
        assert retrieved.client_id == "test-dcr-client"

    @pytest.mark.asyncio
    async def test_register_client_assigns_default_scope(self, provider):
        """register_client 在未指定 scope 时应赋默认 scope。"""
        from mcp.shared.auth import OAuthClientInformationFull

        client_info = OAuthClientInformationFull(
            client_id="test-scope-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        await provider.register_client(client_info)
        assert client_info.scope == "read"

    @pytest.mark.asyncio
    async def test_register_client_generates_client_id_if_missing(self, provider):
        """register_client 在未提供 client_id 时应生成 client_id。"""
        from mcp.shared.auth import OAuthClientInformationFull

        # 完全省略 client_id（Pydantic 要求必填，故创建后再删除）
        client_info = OAuthClientInformationFull(
            client_id="temp-placeholder",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        # 清空 client_id 来模拟其缺失
        client_info.client_id = ""
        await provider.register_client(client_info)
        assert client_info.client_id
        assert len(client_info.client_id) > 0
        assert client_info.client_id != ""

    @pytest.mark.asyncio
    async def test_register_client_generates_secret_for_confidential(self, provider):
        """register_client 应为非公开 client 生成 client_secret。"""
        from mcp.shared.auth import OAuthClientInformationFull

        client_info = OAuthClientInformationFull(
            client_id="test-confidential",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="client_secret_post",
        )
        await provider.register_client(client_info)
        assert client_info.client_secret
        assert len(client_info.client_secret) > 0

    @pytest.mark.asyncio
    async def test_register_client_enforces_max_limit(self, provider):
        """register_client 在达到 client 数量上限时应抛错。"""
        from mcp.shared.auth import OAuthClientInformationFull

        # 临时把上限设为一个很小的数
        original_max = provider.MAX_CLIENTS
        provider.MAX_CLIENTS = 1
        try:
            # 注册第一个 client
            client_info = OAuthClientInformationFull(
                client_id="first-client",
                redirect_uris=[AnyHttpUrl("http://localhost/callback")],
                grant_types=["authorization_code"],
                token_endpoint_auth_method="none",
            )
            await provider.register_client(client_info)

            # 第二个应失败
            from mcp.server.auth.provider import RegistrationError

            with pytest.raises(RegistrationError, match="limit"):
                client_info2 = OAuthClientInformationFull(
                    client_id="second-client",
                    redirect_uris=[AnyHttpUrl("http://localhost/callback")],
                    grant_types=["authorization_code"],
                    token_endpoint_auth_method="none",
                )
                await provider.register_client(client_info2)
        finally:
            provider.MAX_CLIENTS = original_max

    @pytest.mark.asyncio
    async def test_authorize_returns_consent_url(self, provider):
        """authorize 应返回指向 consent 页面的 URL。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        # 先注册 client
        await provider.register_client(client)

        params = AuthorizationParams(
            state="test-state",
            scopes=["read", "write"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )
        url = await provider.authorize(client, params)

        # 应重定向到 consent 页面
        assert "/consent" in url
        assert "request_token=" in url

    @pytest.mark.asyncio
    async def test_authorize_stores_csrf_token(self, provider):
        """authorize 应把 CSRF token 存入 pending auth。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        # 先注册 client
        await provider.register_client(client)

        params = AuthorizationParams(
            state="test-state",
            scopes=["read", "write"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )
        await provider.authorize(client, params)

        # 检查 pending auth 中带有 csrf_token
        assert len(provider._pending_auths) == 1
        pending = next(iter(provider._pending_auths.values()))
        assert "csrf_token" in pending
        assert len(pending["csrf_token"]) > 0

    @pytest.mark.asyncio
    async def test_exchange_authorization_code_issues_jwt(self, provider):
        """exchange_authorization_code 应返回 JWT + refresh token。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        # 先注册 client
        await provider.register_client(client)

        params = AuthorizationParams(
            state=None,
            scopes=["read", "write"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )
        # authorize 以获取 request_token
        await provider.authorize(client, params)

        # 取出 pending 请求的 auth code（模拟 consent allow）
        request_token = None
        for rt, pending in provider._pending_auths.items():
            if pending["client_id"] == "test-client":
                request_token = rt
                break

        assert request_token is not None

        # 模拟 consent allow（带 CSRF token）
        csrf_token = provider._pending_auths[request_token]["csrf_token"]
        auth_code = await provider.handle_consent_allow(
            request_token, username="testuser", csrf_token=csrf_token
        )

        # 用 code 换取 token
        code_obj = await provider.load_authorization_code(client, auth_code)
        assert code_obj is not None

        token = await provider.exchange_authorization_code(client, code_obj)
        assert token.access_token
        assert token.token_type == "Bearer"
        assert token.refresh_token

        # 校验 JWT claims
        public_pem = provider.rsa_manager.get_public_key_pem()
        decoded = jwt.decode(
            token.access_token, public_pem, algorithms=["RS256"], audience="bs"
        )
        assert decoded["sub"] == "testuser"
        assert decoded["scope"] == "read write"
        assert decoded["iss"] == "http://localhost:8000"
        assert decoded["aud"] == "bs"
        assert decoded["exp"] > time.time()

    @pytest.mark.asyncio
    async def test_load_access_token_verifies_jwt(self, provider):
        """load_access_token 应校验 JWT 并返回 AccessToken。"""
        from mcp.server.auth.provider import AccessToken

        # 签发一个有效的 JWT
        claims = {
            "sub": "user1",
            "scope": "read write",
            "iss": "http://localhost:8000",
            "aud": "bs",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        }
        token_str = provider.rsa_manager.sign_jwt(claims)

        result = await provider.load_access_token(token_str)
        assert result is not None
        assert isinstance(result, AccessToken)
        assert result.subject == "user1"

    @pytest.mark.asyncio
    async def test_load_access_token_rejects_expired_jwt(self, provider):
        """load_access_token 对已过期的 JWT 应返回 None。"""
        claims = {
            "sub": "user1",
            "scope": "read write",
            "iss": "http://localhost:8000",
            "aud": "bs",
            "exp": int(time.time()) - 100,  # 已过期
            "iat": int(time.time()) - 3700,
        }
        token_str = provider.rsa_manager.sign_jwt(claims)

        result = await provider.load_access_token(token_str)
        assert result is None

    @pytest.mark.asyncio
    async def test_load_access_token_rejects_bad_signature(self, provider):
        """load_access_token 对签名错误的 JWT 应返回 None。"""
        # 用另一把密钥签名
        other_private, _ = _generate_keypair()
        claims = {
            "sub": "user1",
            "scope": "read write",
            "iss": "http://localhost:8000",
            "aud": "bs",
            "exp": int(time.time()) + 3600,
        }
        token_str = jwt.encode(claims, other_private, algorithm="RS256")

        result = await provider.load_access_token(token_str)
        assert result is None

    @pytest.mark.asyncio
    async def test_consent_deny_clears_pending(self, provider):
        """handle_consent_deny 应清除 pending auth 请求。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        # 先注册 client
        await provider.register_client(client)

        params = AuthorizationParams(
            state=None,
            scopes=["read", "write"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )
        await provider.authorize(client, params)

        # 找到 request_token
        request_token = next(iter(provider._pending_auths))
        assert request_token in provider._pending_auths

        # 拒绝
        await provider.handle_consent_deny(request_token)
        assert request_token not in provider._pending_auths

    @pytest.mark.asyncio
    async def test_refresh_token_rotation(self, provider):
        """exchange_refresh_token 应轮换 refresh token。"""
        from mcp.server.auth.provider import (
            OAuthClientInformationFull,
            RefreshToken,
        )

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code", "refresh_token"],
            token_endpoint_auth_method="none",
        )

        # 创建一个 refresh token
        old_refresh_str = secrets.token_urlsafe(32)
        old_refresh = RefreshToken(
            token=old_refresh_str,
            client_id="test-client",
            scopes=["read", "write"],
            subject="testuser",
        )
        provider._refresh_tokens[old_refresh_str] = old_refresh

        # 交换
        token = await provider.exchange_refresh_token(
            client, old_refresh, ["read", "write"]
        )
        assert token.access_token
        assert token.refresh_token
        assert token.refresh_token != old_refresh_str

        # 旧 token 应被移除
        assert old_refresh_str not in provider._refresh_tokens

    @pytest.mark.asyncio
    async def test_revoke_refresh_token(self, provider):
        """revoke_token 应从存储中移除 refresh token。"""
        from mcp.server.auth.provider import RefreshToken

        refresh = RefreshToken(
            token="test-refresh",
            client_id="test-client",
            scopes=["read"],
        )
        provider._refresh_tokens["test-refresh"] = refresh

        await provider.revoke_token(refresh)
        assert "test-refresh" not in provider._refresh_tokens

    def test_默认required_scopes为read(self, rsa_manager):
        """未显式传入 required_scopes 时，传输层准入底线应默认为 ["read"]。"""
        from app.mcp.provider import BangumiOAuthProvider

        provider = BangumiOAuthProvider(
            base_url="http://localhost:8000",
            rsa_manager=rsa_manager,
            issuer="http://localhost:8000",
            audience="bs",
        )
        assert provider.required_scopes == ["read"]

    def test_显式required_scopes透传给基类(self, rsa_manager):
        """显式传入 required_scopes 时应原样透传给基类。"""
        from app.mcp.provider import BangumiOAuthProvider

        provider = BangumiOAuthProvider(
            base_url="http://localhost:8000",
            rsa_manager=rsa_manager,
            issuer="http://localhost:8000",
            audience="bs",
            required_scopes=["read", "write"],
        )
        assert provider.required_scopes == ["read", "write"]

    def test_空required_scopes不做默认值兜底(self, rsa_manager):
        """显式传入空列表时应保持为空，便于显式关闭 scope 门槛。"""
        from app.mcp.provider import BangumiOAuthProvider

        provider = BangumiOAuthProvider(
            base_url="http://localhost:8000",
            rsa_manager=rsa_manager,
            issuer="http://localhost:8000",
            audience="bs",
            required_scopes=[],
        )
        assert provider.required_scopes == []


# ---------------------------------------------------------------------------
# 内存态 TTL / 惰性清理测试
# ---------------------------------------------------------------------------


class TestMemoryStateTTL:
    """进程内存态的 TTL 与惰性清理（pending / auth code / refresh / 吊销记录）。"""

    @pytest.fixture
    def rsa_manager(self, tmp_path):
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()
        return manager

    @pytest.fixture
    def provider(self, rsa_manager):
        from app.mcp.provider import BangumiOAuthProvider

        return BangumiOAuthProvider(
            base_url="http://localhost:8000",
            rsa_manager=rsa_manager,
            issuer="http://localhost:8000",
            audience="bs",
            token_expiry_seconds=3600,
            auth_enabled=False,
            auth_username="admin",
        )

    def _make_code(self, code: str, expires_at: float):
        from mcp.server.auth.provider import AuthorizationCode
        from pydantic import AnyUrl

        return AuthorizationCode(
            code=code,
            scopes=["read"],
            expires_at=expires_at,
            client_id="test-client",
            code_challenge="challenge123",
            redirect_uri=AnyUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
            subject="testuser",
        )

    def _make_refresh(self, token: str, expires_at: int | None):
        from mcp.server.auth.provider import RefreshToken

        return RefreshToken(
            token=token,
            client_id="test-client",
            scopes=["read"],
            expires_at=expires_at,
            subject="testuser",
        )

    @pytest.mark.asyncio
    async def test_refresh_token_签发时带有效期(self, provider):
        """exchange_authorization_code 签发的 refresh token 带 30 天有效期。"""
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        code_obj = self._make_code("code-1", time.time() + 300)

        token = await provider.exchange_authorization_code(client, code_obj)

        assert provider.refresh_token_ttl == 30 * 24 * 3600
        stored = provider._refresh_tokens[token.refresh_token]
        assert stored.expires_at == pytest.approx(
            time.time() + provider.refresh_token_ttl, abs=5
        )

    @pytest.mark.asyncio
    async def test_refresh_token_轮换后重新计时(self, provider):
        """exchange_refresh_token 轮换后新 token 重新计时，旧 token 被删除。"""
        from mcp.server.auth.provider import RefreshToken
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code", "refresh_token"],
            token_endpoint_auth_method="none",
        )
        old_str = secrets.token_urlsafe(32)
        # 旧 token 临近过期（60 秒后），轮换后新 token 应重新按 30 天计时
        old = RefreshToken(
            token=old_str,
            client_id="test-client",
            scopes=["read"],
            expires_at=int(time.time()) + 60,
            subject="testuser",
        )
        provider._refresh_tokens[old_str] = old

        token = await provider.exchange_refresh_token(client, old, ["read"])

        assert old_str not in provider._refresh_tokens
        new_stored = provider._refresh_tokens[token.refresh_token]
        assert new_stored.expires_at == pytest.approx(
            time.time() + provider.refresh_token_ttl, abs=5
        )

    @pytest.mark.asyncio
    async def test_cleanup_移除过期auth_code(self, provider):
        """_cleanup_expired_state 应清除已过期的授权码，保留有效授权码。"""
        provider._auth_codes["expired-code"] = self._make_code(
            "expired-code", time.time() - 10
        )
        provider._auth_codes["valid-code"] = self._make_code(
            "valid-code", time.time() + 300
        )

        provider._cleanup_expired_state()

        assert "expired-code" not in provider._auth_codes
        assert "valid-code" in provider._auth_codes

    @pytest.mark.asyncio
    async def test_cleanup_移除过期refresh_token(self, provider):
        """_cleanup_expired_state 应清除已过期 refresh token，保留有效与无过期的。"""
        provider._refresh_tokens["expired-rt"] = self._make_refresh(
            "expired-rt", int(time.time()) - 10
        )
        provider._refresh_tokens["valid-rt"] = self._make_refresh(
            "valid-rt", int(time.time()) + 3600
        )
        # expires_at=None 表示不过期，必须保留
        provider._refresh_tokens["no-exp"] = self._make_refresh("no-exp", None)

        provider._cleanup_expired_state()

        assert "expired-rt" not in provider._refresh_tokens
        assert "valid-rt" in provider._refresh_tokens
        assert "no-exp" in provider._refresh_tokens

    def test_cleanup_移除过期吊销记录_未过期的保留(self, provider):
        """吊销记录按 access token 的 exp 惰性清理，未过期的保留。"""
        provider._revoked_tokens["past-jti"] = time.time() - 10
        provider._revoked_tokens["future-jti"] = time.time() + 3600

        provider._cleanup_expired_state()

        assert "past-jti" not in provider._revoked_tokens
        assert "future-jti" in provider._revoked_tokens

    @pytest.mark.asyncio
    async def test_revoke_access_token_记录到期时间用于清理(self, provider):
        """revoke_token 记录 jti 时同时记录其 exp，供惰性清理。"""
        from mcp.server.auth.provider import AccessToken

        access = AccessToken(
            token="access-token",
            client_id="test-client",
            scopes=["read"],
            expires_at=int(time.time()) + 3600,
            claims={"jti": "jti-recording"},
        )

        await provider.revoke_token(access)

        assert provider._revoked_tokens["jti-recording"] == pytest.approx(
            access.expires_at
        )

    def test_pending_过期清理回归(self, provider):
        """pending 过期清理行为保持不变（10 分钟 TTL）。"""
        provider._pending_auths["expired-pending"] = {"created_at": time.time() - 700}
        provider._pending_auths["valid-pending"] = {"created_at": time.time()}

        provider._cleanup_expired_state()

        assert "expired-pending" not in provider._pending_auths
        assert "valid-pending" in provider._pending_auths


# ---------------------------------------------------------------------------
# consent + CSRF 测试（T3）
# ---------------------------------------------------------------------------


class TestConsentFlow:
    """consent 页面与 CSRF 保护的测试。"""

    @pytest.fixture
    def rsa_manager(self, tmp_path):
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()
        return manager

    @pytest.fixture
    def provider(self, rsa_manager):
        from app.mcp.provider import BangumiOAuthProvider

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
    async def test_consent_allow_without_csrf_rejected(self, provider):
        """handle_consent_allow 应拒绝不带 CSRF token 的请求。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        # 先注册 client
        await provider.register_client(client)

        params = AuthorizationParams(
            state=None,
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )
        await provider.authorize(client, params)
        request_token = next(iter(provider._pending_auths))

        # 尝试不带 CSRF token 进行 allow
        with pytest.raises(ValueError, match="CSRF"):
            await provider.handle_consent_allow(
                request_token, csrf_token="", username="admin"
            )

    @pytest.mark.asyncio
    async def test_consent_allow_with_wrong_csrf_rejected(self, provider):
        """handle_consent_allow 应拒绝带错误 CSRF token 的请求。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        # 先注册 client
        await provider.register_client(client)

        params = AuthorizationParams(
            state=None,
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )
        await provider.authorize(client, params)
        request_token = next(iter(provider._pending_auths))

        # 尝试带错误的 CSRF token 进行 allow
        with pytest.raises(ValueError, match="CSRF"):
            await provider.handle_consent_allow(
                request_token, csrf_token="wrong-token", username="admin"
            )

    @pytest.mark.asyncio
    async def test_consent_allow_with_correct_csrf_succeeds(self, provider):
        """handle_consent_allow 在 CSRF token 正确时应成功。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull

        client = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        # 先注册 client
        await provider.register_client(client)

        params = AuthorizationParams(
            state=None,
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )
        await provider.authorize(client, params)
        request_token = next(iter(provider._pending_auths))
        csrf_token = provider._pending_auths[request_token]["csrf_token"]

        # 应成功
        auth_code = await provider.handle_consent_allow(
            request_token, csrf_token=csrf_token, username="admin"
        )
        assert auth_code
        assert len(auth_code) > 0

    @pytest.mark.asyncio
    async def test_consent_form_rendered_with_csrf_and_escaping(self, provider):
        """_render_consent_form 应包含 CSRF token 并转义 HTML。"""
        context = {
            "request_token": "tok123",
            "client_id": "<script>alert(1)</script>",
            "scopes": ["read", "write"],
            "username": "admin",
            "csrf_token": "csrf456",
        }
        html = provider._render_consent_form(context)
        # CSRF token 应存在
        assert "csrf456" in html
        # client_id 应被转义
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# CIMD 测试（T4）
# ---------------------------------------------------------------------------


class TestCIMDIntegration:
    """CIMD（Client ID Metadata Document）集成的测试。"""

    @pytest.fixture
    def rsa_manager(self, tmp_path):
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()
        return manager

    @pytest.fixture
    def provider(self, rsa_manager):
        from app.mcp.provider import BangumiOAuthProvider

        return BangumiOAuthProvider(
            base_url="http://localhost:8000",
            rsa_manager=rsa_manager,
            issuer="http://localhost:8000",
            audience="bs",
            token_expiry_seconds=3600,
            auth_enabled=False,
            auth_username="admin",
        )

    def test_is_cimd_detects_url_client_id(self, provider):
        """is_cimd_client_id 应把 HTTPS URL 识别为 CIMD。"""
        assert provider.cimd.is_cimd_client_id(
            "https://claude.ai/oauth/claude-code-client-metadata"
        )

    def test_is_cimd_rejects_plain_client_id(self, provider):
        """is_cimd_client_id 应拒绝普通的字符串 client_id。"""
        assert not provider.cimd.is_cimd_client_id("my-random-client")

    def test_is_cimd_rejects_http_url(self, provider):
        """is_cimd_client_id 应拒绝 HTTP（非 SSL）URL。"""
        assert not provider.cimd.is_cimd_client_id("http://example.com/metadata")

    @pytest.mark.asyncio
    async def test_get_client_cimd_branch_with_mock(self, provider):
        """get_client 对 URL 形式的 client_id 应委托给 CIMD。"""
        from fastmcp.server.auth.cimd import CIMDDocument
        from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient

        # 创建一个 mock 的 CIMD client
        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://claude.ai/oauth/claude-code-client-metadata"),
            client_name="Claude Code",
            redirect_uris=["http://localhost/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
        )
        mock_client = ProxyDCRClient(
            client_id="https://claude.ai/oauth/claude-code-client-metadata",
            client_secret=None,
            redirect_uris=None,
            grant_types=cimd_doc.grant_types,
            scope="read write",
            token_endpoint_auth_method=cimd_doc.token_endpoint_auth_method,
            allowed_redirect_uri_patterns=None,
            client_name=cimd_doc.client_name,
            cimd_document=cimd_doc,
            cimd_fetched_at=time.time(),
        )

        # mock CIMD 的 get_client 方法（必须是 async）
        async def mock_get_client(url):
            return mock_client

        provider.cimd.get_client = mock_get_client

        result = await provider.get_client(
            "https://claude.ai/oauth/claude-code-client-metadata"
        )
        assert result is not None
        assert result.client_name == "Claude Code"

    @pytest.mark.asyncio
    async def test_get_client_dcr_branch(self, provider):
        """get_client 对非 URL 的 client_id 应返回 DCR client。"""
        from mcp.shared.auth import OAuthClientInformationFull

        client_info = OAuthClientInformationFull(
            client_id="dcr-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        await provider.register_client(client_info)

        result = await provider.get_client("dcr-client")
        assert result is not None
        assert result.client_id == "dcr-client"

    @pytest.mark.asyncio
    async def test_get_client_unknown_returns_none(self, provider):
        """get_client 对未知 client_id 应返回 None。"""
        result = await provider.get_client("totally-unknown-client")
        assert result is None

    @pytest.mark.asyncio
    async def test_cimd_scope_injection(self, provider):
        """不带 scope 的 CIMD client 会被注入允许的 scope 集合（校验）。"""
        from fastmcp.server.auth.cimd import CIMDDocument
        from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient

        # 不带 scope 的 CIMD doc
        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://example.com/oauth/client-metadata"),
            client_name="Test App",
            redirect_uris=["http://localhost/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code"],
            scope=None,  # 元数据中不带 scope
        )

        # 模拟 CIMDClientManager.get_client 借助 default_scope 的处理
        mock_client = ProxyDCRClient(
            client_id="https://example.com/oauth/client-metadata",
            client_secret=None,
            redirect_uris=None,
            grant_types=cimd_doc.grant_types,
            scope=cimd_doc.scope or provider.cimd.default_scope,
            token_endpoint_auth_method=cimd_doc.token_endpoint_auth_method,
            allowed_redirect_uri_patterns=None,
            client_name=cimd_doc.client_name,
            cimd_document=cimd_doc,
            cimd_fetched_at=time.time(),
        )

        # 注入的是校验允许集（read write），请求 read write 不会被 SDK 拒绝；
        # 实际发放的默认 scope 仍是 read（见 authorize 覆盖）。
        assert mock_client.scope == "read write"
        assert mock_client.validate_scope("read write") == ["read", "write"]

    def test_cimd_default_scope_configuration(self, provider):
        """CIMDClientManager 的 default_scope 应为允许的 scope 集合。"""
        assert provider.cimd.default_scope == "read write"


# ---------------------------------------------------------------------------
# 元数据注入测试（T4）
# ---------------------------------------------------------------------------


class TestMetadataInjection:
    """client_id_metadata_document_supported 注入的测试。"""

    @pytest.fixture
    def rsa_manager(self, tmp_path):
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()
        return manager

    @pytest.fixture
    def provider(self, rsa_manager):
        from app.mcp.provider import BangumiOAuthProvider

        return BangumiOAuthProvider(
            base_url="http://localhost:8000",
            rsa_manager=rsa_manager,
            issuer="http://localhost:8000",
            audience="bs",
            token_expiry_seconds=3600,
            auth_enabled=False,
            auth_username="admin",
        )

    def test_metadata_includes_cimd_support(self, provider):
        """get_routes 应注入 client_id_metadata_document_supported=True。"""
        routes = provider.get_routes()
        # 找到 metadata 路由
        from starlette.routing import Route

        metadata_route = None
        for route in routes:
            if (
                isinstance(route, Route)
                and route.path == "/.well-known/oauth-authorization-server"
            ):
                metadata_route = route
                break

        assert metadata_route is not None, "Metadata route should exist"

        # 我们无法直接检查路由中的元数据，
        # 但可以验证该路由存在且配置正确
        assert "GET" in (metadata_route.methods or [])


# ---------------------------------------------------------------------------
# 全流程集成测试（使用 TestClient）
# ---------------------------------------------------------------------------


class TestOAuthFullFlow:
    """使用 TestClient 的端到端 OAuth 流程测试。"""

    @pytest.fixture
    def tmp_keys(self, tmp_path):
        """创建临时密钥文件。"""
        return {
            "private": str(tmp_path / "private.pem"),
            "public": str(tmp_path / "public.pem"),
        }

    @pytest.fixture
    def server_app_auth_disabled(self, tmp_keys):
        """创建 auth.enabled=False 的测试 server。"""
        from tests.mcp_helpers import build_test_mcp_app

        app = build_test_mcp_app(
            private_key_path=tmp_keys["private"],
            public_key_path=tmp_keys["public"],
            issuer="http://localhost:8000",
            audience="bs",
            auth_enabled=False,
            auth_username="admin",
        )
        return app

    @pytest.fixture
    def server_app_auth_enabled(self, tmp_keys):
        """创建 auth.enabled=True 的测试 server。"""
        from tests.mcp_helpers import build_test_mcp_app

        app = build_test_mcp_app(
            private_key_path=tmp_keys["private"],
            public_key_path=tmp_keys["public"],
            issuer="http://localhost:8000",
            audience="bs",
            auth_enabled=True,
            auth_username="admin",
        )
        return app

    def _make_test_client(self, app):
        return TestClient(app, raise_server_exceptions=False)

    def _complete_authorization_code_flow(self, client):
        """走完 DCR → authorize → consent → token，返回 (client_id, token_json)。"""
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "none",
                "scope": "read",
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
                "scope": "read",
            },
            follow_redirects=False,
        )
        consent_url = auth_response.headers["location"]
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        consent_get = client.get(consent_url)
        assert consent_get.status_code == 200
        csrf_token = _extract_csrf_token(consent_get.text)

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
        assert token_response.status_code == 200
        return client_id, token_response.json()

    def _start_consent_flow(self, client):
        """走 DCR + authorize，返回 (client_id, consent_url, request_token)。"""
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
            },
            follow_redirects=False,
        )
        assert auth_response.status_code == 302
        consent_url = auth_response.headers["location"]
        request_token = parse_qs(urlparse(consent_url).query)["request_token"][0]
        return client_id, consent_url, request_token

    def _assert_login_redirect_points_to_consent(self, response, request_token):
        """断言 302 登录页重定向，且 next 精确指向本站 consent 路径。"""
        assert response.status_code == 302, (
            f"无有效 session 时应 302 到登录页，实际: {response.status_code}, "
            f"body: {response.text[:200]}"
        )
        parsed = urlparse(response.headers["location"])
        assert parsed.path == "/login", f"应跳转 /login，实际: {parsed.path}"
        next_value = parse_qs(parsed.query)["next"][0]
        # next 为不含 base_path 的站内路径（JS 端 appUrl() 负责补 base）
        assert next_value == f"/consent?request_token={request_token}", (
            f"next 应精确回跳 consent，实际: {next_value!r}"
        )
        base = get_public_base_path()
        if base:
            assert not next_value.startswith(base), (
                f"next 不应包含 base_path 前缀 {base!r}，实际: {next_value!r}"
            )
        # 开放重定向护栏：站内相对路径，既非绝对 URL 也非协议相对 URL
        assert next_value.startswith("/")
        assert not next_value.startswith("//")

    def test_metadata_endpoint_returns_authorization_server_metadata(
        self, server_app_auth_disabled
    ):
        """GET /.well-known/oauth-authorization-server 应返回元数据。"""
        client = self._make_test_client(server_app_auth_disabled)
        response = client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        data = response.json()
        # AnyHttpUrl 会规范化为带尾部斜杠
        assert data["issuer"] in ("http://localhost:8000", "http://localhost:8000/")
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data

    def test_metadata_includes_cimd_support_flag(self, server_app_auth_disabled):
        """元数据应声明 client_id_metadata_document_supported。"""
        client = self._make_test_client(server_app_auth_disabled)
        response = client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        data = response.json()
        assert data.get("client_id_metadata_document_supported") is True

    def test_dcr_register_client(self, server_app_auth_disabled):
        """POST /register 应注册一个新 client。"""
        client = self._make_test_client(server_app_auth_disabled)
        response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "client_id" in data
        assert "client_secret" in data
        assert data["client_id_issued_at"] is not None

    def test_full_flow_auth_disabled(self, server_app_auth_disabled):
        """auth.enabled=False 的完整 OAuth 流程：authorize → consent → token。"""
        client = self._make_test_client(server_app_auth_disabled)

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

        # 步骤 2：authorize
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

        # 步骤 3：consent 页面（GET）
        consent_get = client.get(consent_url)
        assert consent_get.status_code == 200

        # 步骤 4：consent allow（POST）
        # 从 consent URL 解析 request_token
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]
        # 从 consent 表单中提取 CSRF token
        csrf_token = _extract_csrf_token(consent_get.text)

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

        # 提取 code
        parsed_redirect = urlparse(redirect_url)
        code = parse_qs(parsed_redirect.query)["code"][0]

        # 步骤 5：token 交换
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
        assert token_data["refresh_token"]

        # 校验 JWT claims
        access_token = token_data["access_token"]
        # 加载公钥用于验签
        public_pem = server_app_auth_disabled.state.public_key_pem
        decoded = jwt.decode(
            access_token, public_pem, algorithms=["RS256"], audience="bs"
        )
        assert decoded["sub"] == "admin"  # auth.enabled=False 时使用 auth.username
        assert decoded["scope"] == "read write"
        assert decoded["iss"] == "http://localhost:8000"
        assert decoded["aud"] == "bs"
        assert decoded["exp"] > time.time()

    def test_consent_deny_returns_error(self, server_app_auth_disabled):
        """用户点击 deny 应带 error 重定向。"""
        client = self._make_test_client(server_app_auth_disabled)

        # DCR
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = reg_response.json()["client_id"]

        # authorize
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
            },
            follow_redirects=False,
        )
        consent_url = auth_response.headers["location"]
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        # 获取 consent 表单以提取 CSRF token
        consent_get = client.get(consent_url)
        assert consent_get.status_code == 200
        csrf_token = _extract_csrf_token(consent_get.text)

        # 拒绝
        consent_post = client.post(
            "/consent",
            data={
                "action": "deny",
                "request_token": request_token,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert consent_post.status_code == 302
        redirect_url = consent_post.headers["location"]
        assert "error=" in redirect_url
        assert "access_denied" in redirect_url

    def test_consent_post_without_csrf_rejected(self, server_app_auth_disabled):
        """不带 CSRF token 的 POST /consent 应返回 403。"""
        client = self._make_test_client(server_app_auth_disabled)

        # DCR
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = reg_response.json()["client_id"]

        # authorize
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
            },
            follow_redirects=False,
        )
        consent_url = auth_response.headers["location"]
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        # 不带 CSRF token 的 POST
        consent_post = client.post(
            "/consent",
            data={
                "action": "allow",
                "request_token": request_token,
                # 不带 csrf_token
            },
            follow_redirects=False,
        )
        assert consent_post.status_code == 403

    def test_consent_post_with_wrong_csrf_rejected(self, server_app_auth_disabled):
        """带错误 CSRF token 的 POST /consent 应返回 403。"""
        client = self._make_test_client(server_app_auth_disabled)

        # DCR
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = reg_response.json()["client_id"]

        # authorize
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
            },
            follow_redirects=False,
        )
        consent_url = auth_response.headers["location"]
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        # 带错误 CSRF token 的 POST
        consent_post = client.post(
            "/consent",
            data={
                "action": "allow",
                "request_token": request_token,
                "csrf_token": "wrong-csrf-token",
            },
            follow_redirects=False,
        )
        assert consent_post.status_code == 403

    def test_token_with_invalid_code_returns_error(self, server_app_auth_disabled):
        """token 端点使用无效 code 时应返回 error。

        注意：按 MCP 规范，FastMCP 的 TokenHandler 会把 invalid_grant 的 400 转为 401
        （原文："Invalid or expired tokens MUST receive a HTTP 401 response"，即"无效或过期的 token 必须收到 HTTP 401 响应"）。
        """
        client = self._make_test_client(server_app_auth_disabled)

        # DCR
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = reg_response.json()["client_id"]

        # 用无效 code 尝试换取 token
        token_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "invalid-code",
                "redirect_uri": "http://localhost/callback",
                "client_id": client_id,
                "code_verifier": "verifier",
            },
        )
        # 按 MCP 规范，FastMCP 将 invalid_grant 的 400 转为 401
        assert token_response.status_code == 401
        assert token_response.json()["error"] == "invalid_grant"

    def test_refresh_token_flow(self, server_app_auth_disabled):
        """refresh token 应换取新的 access token。"""
        client = self._make_test_client(server_app_auth_disabled)

        # DCR
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = reg_response.json()["client_id"]

        # authorize
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
            },
            follow_redirects=False,
        )
        consent_url = auth_response.headers["location"]
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        # 获取 consent 表单以提取 CSRF token
        consent_get = client.get(consent_url)
        assert consent_get.status_code == 200
        csrf_token = _extract_csrf_token(consent_get.text)

        # consent 允许
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

        # token 交换
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
        refresh_token = token_response.json()["refresh_token"]

        # 刷新
        refresh_response = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        assert refresh_response.status_code == 200
        new_token_data = refresh_response.json()
        assert new_token_data["access_token"]
        assert new_token_data["access_token"] != token_response.json()["access_token"]

    def test_过期refresh_token_刷新被拒(self, server_app_auth_disabled):
        """过期 refresh token 换新 token 应被拒绝。

        provider 的 load_refresh_token 只负责加载；过期判定由 MCP SDK token
        handler 承担（mcp/server/auth/handlers/token.py:215），返回 invalid_grant。
        """
        client = self._make_test_client(server_app_auth_disabled)
        client_id, token_data = self._complete_authorization_code_flow(client)

        provider = server_app_auth_disabled.state.provider
        stored = provider._refresh_tokens[token_data["refresh_token"]]
        # 签发的 refresh token 必须自带有效期
        assert stored.expires_at is not None
        assert stored.expires_at == pytest.approx(
            time.time() + provider.refresh_token_ttl, abs=5
        )
        # 再将其手动置为过期，模拟自然到期
        stored.expires_at = int(time.time()) - 10

        refresh_response = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_data["refresh_token"],
                "client_id": client_id,
            },
        )
        assert refresh_response.status_code == 401
        assert refresh_response.json()["error"] == "invalid_grant"

    def test_revoke_token_endpoint(self, server_app_auth_disabled):
        """POST /revoke 应吊销 token。"""
        client = self._make_test_client(server_app_auth_disabled)

        # DCR
        reg_response = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = reg_response.json()["client_id"]

        # authorize
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
            },
            follow_redirects=False,
        )
        consent_url = auth_response.headers["location"]
        parsed = urlparse(consent_url)
        request_token = parse_qs(parsed.query)["request_token"][0]

        consent_get = client.get(consent_url)
        csrf_token = _extract_csrf_token(consent_get.text)

        consent_post = client.post(
            "/consent",
            data={
                "action": "allow",
                "request_token": request_token,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        redirect_url = consent_post.headers["location"]
        code = parse_qs(urlparse(redirect_url).query)["code"][0]

        # token 交换
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
        refresh_token = token_response.json()["refresh_token"]

        # 吊销（SDK 的 RevocationRequest 模型要求 client_secret）
        revoke_response = client.post(
            "/revoke",
            data={
                "token": refresh_token,
                "client_id": client_id,
                "client_secret": "",
            },
        )
        assert revoke_response.status_code == 200

    @pytest.mark.asyncio
    async def test_full_flow_auth_enabled_with_mock_security(
        self, server_app_auth_enabled, monkeypatch
    ):
        """auth.enabled=True 且 mock security_manager 的完整 OAuth 流程。"""
        from app.mcp import provider as provider_module

        # mock security_manager.validate_session 使其返回一个 session
        mock_session = {"username": "testuser", "created_at": time.time()}
        monkeypatch.setattr(
            provider_module.security_manager,
            "validate_session",
            lambda token: mock_session,
        )

        with TestClient(
            server_app_auth_enabled, raise_server_exceptions=False
        ) as client:
            # DCR（显式注册 read write scope，因默认 scope 已改为 read）
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

            # authorize
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
                },
                follow_redirects=False,
            )
            assert auth_response.status_code == 302
            consent_url = auth_response.headers["location"]

            # consent GET（通过 security_manager 校验 BS session）
            # 发送 session_token cookie 以便处理器校验
            consent_get = client.get(
                consent_url,
                cookies={"session_token": "valid-session-token"},
            )
            assert consent_get.status_code == 200

            # consent 允许
            parsed = urlparse(consent_url)
            request_token = parse_qs(parsed.query)["request_token"][0]
            csrf_token = _extract_csrf_token(consent_get.text)
            consent_post = client.post(
                "/consent",
                data={
                    "action": "allow",
                    "request_token": request_token,
                    "csrf_token": csrf_token,
                },
                cookies={"session_token": "valid-session-token"},
                follow_redirects=False,
            )
            assert consent_post.status_code == 302
            redirect_url = consent_post.headers["location"]
            code = parse_qs(urlparse(redirect_url).query)["code"][0]

            # token 交换
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

            # 校验 JWT sub = testuser（来自 security_manager session）
            public_pem = server_app_auth_enabled.state.public_key_pem
            decoded = jwt.decode(
                token_data["access_token"],
                public_pem,
                algorithms=["RS256"],
                audience="bs",
            )
            assert decoded["sub"] == "testuser"
            assert decoded["scope"] == "read write"

    @pytest.mark.asyncio
    async def test_consent_get_no_session_redirects_to_login(
        self, server_app_auth_enabled, monkeypatch
    ):
        """auth.enabled=True 且无 session cookie 时，consent GET 应 302 登录页。"""
        from app.mcp import provider as provider_module

        # mock security_manager.validate_session 使其返回 None（无 session）
        monkeypatch.setattr(
            provider_module.security_manager,
            "validate_session",
            lambda token: None,
        )

        with TestClient(
            server_app_auth_enabled, raise_server_exceptions=False
        ) as client:
            _, consent_url, request_token = self._start_consent_flow(client)

            # 无 cookie → 应 302 到登录页并回跳 consent，而不是 401 纯文本
            consent_get = client.get(consent_url, follow_redirects=False)
            self._assert_login_redirect_points_to_consent(consent_get, request_token)

    @pytest.mark.asyncio
    async def test_consent_get_expired_session_redirects_to_login(
        self, server_app_auth_enabled, monkeypatch
    ):
        """auth.enabled=True 且 session 失效/过期时，consent GET 应 302 登录页。"""
        from app.mcp import provider as provider_module

        monkeypatch.setattr(
            provider_module.security_manager,
            "validate_session",
            lambda token: None,
        )

        with TestClient(
            server_app_auth_enabled, raise_server_exceptions=False
        ) as client:
            _, consent_url, request_token = self._start_consent_flow(client)

            consent_get = client.get(
                consent_url,
                cookies={"session_token": "expired-token"},
                follow_redirects=False,
            )
            self._assert_login_redirect_points_to_consent(consent_get, request_token)

    @pytest.mark.asyncio
    async def test_consent_post_allow_no_session_redirects_without_issuing_code(
        self, server_app_auth_enabled, monkeypatch
    ):
        """无 session 的 POST allow 应 302 登录页，且不签发 code、不删除 pending。"""
        from app.mcp import provider as provider_module

        mock_session = {"username": "testuser", "created_at": time.time()}
        monkeypatch.setattr(
            provider_module.security_manager,
            "validate_session",
            lambda token: mock_session,
        )

        with TestClient(
            server_app_auth_enabled, raise_server_exceptions=False
        ) as client:
            _, consent_url, request_token = self._start_consent_flow(client)

            # 先用有效 session 取到合法的 CSRF token（CSRF 校验先于 session 校验）
            consent_get = client.get(
                consent_url, cookies={"session_token": "valid-session-token"}
            )
            assert consent_get.status_code == 200
            csrf_token = _extract_csrf_token(consent_get.text)

            provider = server_app_auth_enabled.state.provider
            assert request_token in provider._pending_auths

            # 不带 session cookie 提交 allow
            consent_post = client.post(
                "/consent",
                data={
                    "action": "allow",
                    "request_token": request_token,
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )
            self._assert_login_redirect_points_to_consent(consent_post, request_token)

            # 不得签发 authorization code，也不得删除 pending auth
            assert provider._auth_codes == {}
            assert request_token in provider._pending_auths

    @pytest.mark.asyncio
    async def test_consent_login_redirect_back_with_session_renders_form(
        self, server_app_auth_enabled, monkeypatch
    ):
        """302 登录后携带有效 session 回跳 consent，应 200 并渲染 allow/deny。"""
        from app.mcp import provider as provider_module

        mock_session = {"username": "testuser", "created_at": time.time()}
        monkeypatch.setattr(
            provider_module.security_manager,
            "validate_session",
            lambda token: mock_session,
        )

        with TestClient(
            server_app_auth_enabled, raise_server_exceptions=False
        ) as client:
            _, consent_url, request_token = self._start_consent_flow(client)

            consent_get = client.get(consent_url, follow_redirects=False)
            self._assert_login_redirect_points_to_consent(consent_get, request_token)

            next_value = parse_qs(urlparse(consent_get.headers["location"]).query)[
                "next"
            ][0]

            # 模拟登录成功后 JS 回跳到 next（此时已带 session cookie）
            back_get = client.get(
                next_value, cookies={"session_token": "valid-session-token"}
            )
            assert back_get.status_code == 200, (
                f"回跳后应 200 渲染表单，实际: {back_get.status_code}, "
                f"body: {back_get.text[:200]}"
            )
            assert 'value="allow"' in back_get.text
            assert 'value="deny"' in back_get.text

    @pytest.mark.asyncio
    async def test_consent_post_invalid_csrf_still_403_before_session_check(
        self, server_app_auth_enabled, monkeypatch
    ):
        """CSRF 校验必须先于 session 校验：无 session 且 CSRF 错误应 403（非 302）。"""
        from app.mcp import provider as provider_module

        monkeypatch.setattr(
            provider_module.security_manager,
            "validate_session",
            lambda token: None,
        )

        with TestClient(
            server_app_auth_enabled, raise_server_exceptions=False
        ) as client:
            _, _, request_token = self._start_consent_flow(client)

            provider = server_app_auth_enabled.state.provider
            assert request_token in provider._pending_auths

            consent_post = client.post(
                "/consent",
                data={
                    "action": "allow",
                    "request_token": request_token,
                    "csrf_token": "wrong-token",
                },
                follow_redirects=False,
            )
            assert consent_post.status_code == 403, (
                f"CSRF 错误应先于 session 校验返回 403，实际: "
                f"{consent_post.status_code}"
            )
            assert provider._auth_codes == {}
            assert request_token in provider._pending_auths

    def test_consent_login_redirect_next_is_safe_relative_path(self):
        """登录重定向 helper 构造的 next 始终是站内相对路径（开放重定向护栏）。"""
        from app.mcp.consent import _consent_login_redirect

        for raw_token in (
            "plain-token",
            "token-with-&-and-=-and-?",
            "/evil/../token",
            "//evil.com",
        ):
            response = _consent_login_redirect(raw_token)
            assert response.status_code == 302
            parsed = urlparse(response.headers["location"])
            assert parsed.path == "/login"
            next_value = parse_qs(parsed.query)["next"][0]
            assert next_value.startswith("/"), (
                f"next 必须以 / 开头，实际: {next_value!r}"
            )
            assert not next_value.startswith("//"), (
                f"next 不得为协议相对 URL，实际: {next_value!r}"
            )
            # 原始 token 被整体 urlencode，不会被解释为额外 query 参数
            assert parse_qs(urlparse(next_value).query)["request_token"] == [raw_token]


# ---------------------------------------------------------------------------
# DCR 回退测试（T5）
# ---------------------------------------------------------------------------


class TestDCRFallback:
    """DCR 回退行为的测试。"""

    @pytest.fixture
    def rsa_manager(self, tmp_path):
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()
        return manager

    @pytest.fixture
    def provider(self, rsa_manager):
        from app.mcp.provider import BangumiOAuthProvider

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
    async def test_unknown_client_id_rejected_in_authorize(self, provider):
        """authorize 对未知 client_id 应以 unauthorized_client 拒绝。"""
        from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
        from mcp.shared.auth import OAuthClientInformationFull

        # 创建 client 对象但不注册它
        client = OAuthClientInformationFull(
            client_id="unregistered-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        params = AuthorizationParams(
            state=None,
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://localhost/callback"),
            redirect_uri_provided_explicitly=True,
        )

        with pytest.raises(AuthorizeError) as exc_info:
            await provider.authorize(client, params)
        assert exc_info.value.error == "unauthorized_client"
        assert "Unknown client" in (exc_info.value.error_description or "")

    @pytest.mark.asyncio
    async def test_authorize_validates_redirect_uri(self, provider):
        """authorize 对不匹配的 redirect_uri 应以 invalid_request 拒绝。"""
        from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
        from mcp.shared.auth import OAuthClientInformationFull

        client_info = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("http://localhost/callback")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        await provider.register_client(client_info)

        # 用不同的 redirect_uri 尝试 authorize
        params = AuthorizationParams(
            state=None,
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("http://evil.com/callback"),
            redirect_uri_provided_explicitly=True,
        )

        with pytest.raises(AuthorizeError) as exc_info:
            await provider.authorize(client_info, params)
        assert exc_info.value.error == "invalid_request"
        assert "redirect_uri" in (exc_info.value.error_description or "")

    def test_matches_redirect_uri_loopback_port_flexible(self):
        """Loopback redirect_uri 应忽略端口匹配（RFC 8252）。"""
        from app.mcp.provider import BangumiOAuthProvider

        assert (
            BangumiOAuthProvider._matches_redirect_uri(
                "http://localhost:54321/callback",
                ["http://localhost/callback"],
            )
            is True
        )

    def test_matches_redirect_uri_non_loopback_port_must_match(self):
        """非 loopback redirect_uri 应要求端口精确匹配。"""
        from app.mcp.provider import BangumiOAuthProvider

        assert (
            BangumiOAuthProvider._matches_redirect_uri(
                "http://example.com:8080/callback",
                ["http://example.com/callback"],
            )
            is False
        )

    @pytest.mark.asyncio
    async def test_register_client_validates_redirect_uris(self, provider):
        """register_client 应校验提供了 redirect_uris。"""
        from mcp.shared.auth import OAuthClientInformationFull

        # 不带 redirect_uris 的 client 也应可注册
        # （有些 client 初始可能没有它们）
        client_info = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        await provider.register_client(client_info)
        # 应成功（空列表是合法的）
        retrieved = await provider.get_client("test-client")
        assert retrieved is not None


# ---------------------------------------------------------------------------
# 安全修复测试（圆桌评审）
# ---------------------------------------------------------------------------


class TestSecurityFixes:
    """圆桌评审中 P0/P1/P2 安全修复的测试。"""

    @pytest.fixture
    def rsa_manager(self, tmp_path):
        from app.mcp.keys import RSAKeyManager

        manager = RSAKeyManager(
            private_key_path=str(tmp_path / "private.pem"),
            public_key_path=str(tmp_path / "public.pem"),
        )
        manager.generate_keys()
        return manager

    @pytest.fixture
    def provider(self, rsa_manager):
        from app.mcp.provider import BangumiOAuthProvider

        return BangumiOAuthProvider(
            base_url="http://localhost:8000",
            rsa_manager=rsa_manager,
            issuer="http://localhost:8000",
            audience="bs",
            token_expiry_seconds=3600,
            auth_enabled=False,
            auth_username="admin",
        )

    # --- P0-1: CIMD redirect_uri 校验 ---

    @pytest.mark.asyncio
    async def test_cimd_authorize_rejects_malicious_redirect_uri(self, provider):
        """带恶意 redirect_uri 的 CIMD client 应被拒绝（P0-1）。"""
        from fastmcp.server.auth.cimd import CIMDDocument
        from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
        from mcp.server.auth.provider import AuthorizationParams
        from pydantic import AnyHttpUrl

        # 创建一个带已知 redirect_uri 的 CIMD client
        cimd_doc = CIMDDocument(
            client_id=AnyHttpUrl("https://claude.ai/oauth/claude-code-client-metadata"),
            client_name="Claude Code",
            redirect_uris=["http://localhost/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
        )
        mock_client = ProxyDCRClient(
            client_id="https://claude.ai/oauth/claude-code-client-metadata",
            client_secret=None,
            redirect_uris=None,
            grant_types=cimd_doc.grant_types,
            scope="read write",
            token_endpoint_auth_method=cimd_doc.token_endpoint_auth_method,
            allowed_redirect_uri_patterns=None,
            client_name=cimd_doc.client_name,
            cimd_document=cimd_doc,
            cimd_fetched_at=time.time(),
        )

        # mock CIMD 的 get_client 使其返回我们的 mock
        async def mock_get_client(url):
            return mock_client

        provider.cimd.get_client = mock_get_client

        # 用攻击者控制的 redirect_uri 尝试 authorize
        params = AuthorizationParams(
            state="test-state",
            scopes=["read", "write"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("https://attacker.com/steal"),
            redirect_uri_provided_explicitly=True,
        )

        from mcp.server.auth.provider import AuthorizeError

        with pytest.raises(AuthorizeError) as exc_info:
            await provider.authorize(mock_client, params)
        assert exc_info.value.error == "invalid_request"
        assert "redirect_uri" in (exc_info.value.error_description or "")

    # --- P0-2: DCR 前缀绕过 ---

    @pytest.mark.asyncio
    async def test_dcr_authorize_rejects_prefix_bypass_subdomain(self, provider):
        """通过子域的 DCR client redirect_uri 前缀绕过应被拒绝（P0-2）。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl

        client_info = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("https://app.example.com/cb")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        await provider.register_client(client_info)

        # 攻击：cb.attacker.com 能匹配 startswith("...example.com/cb")，但主机不同
        params = AuthorizationParams(
            state=None,
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("https://app.example.com/cb.attacker.com/x"),
            redirect_uri_provided_explicitly=True,
        )

        from mcp.server.auth.provider import AuthorizeError

        with pytest.raises(AuthorizeError) as exc_info:
            await provider.authorize(client_info, params)
        assert exc_info.value.error == "invalid_request"
        assert "redirect_uri" in (exc_info.value.error_description or "")

    @pytest.mark.asyncio
    async def test_dcr_authorize_rejects_prefix_bypass_dot_segments(self, provider):
        """通过 dot-segments 的 DCR client redirect_uri 绕过应被拒绝（P0-2）。"""
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl

        client_info = OAuthClientInformationFull(
            client_id="test-client",
            redirect_uris=[AnyHttpUrl("https://app.example.com/cb")],
            grant_types=["authorization_code"],
            token_endpoint_auth_method="none",
        )
        await provider.register_client(client_info)

        # 攻击：通过 dot-segments 进行路径穿越
        params = AuthorizationParams(
            state=None,
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri=AnyHttpUrl("https://app.example.com/cb/../evil"),
            redirect_uri_provided_explicitly=True,
        )

        from mcp.server.auth.provider import AuthorizeError

        with pytest.raises(AuthorizeError) as exc_info:
            await provider.authorize(client_info, params)
        assert exc_info.value.error == "invalid_request"
        assert "redirect_uri" in (exc_info.value.error_description or "")

    # --- P1-1: issuer_url ---

    def test_metadata_issuer_matches_jwt_iss(self, provider):
        """元数据 issuer 应匹配 JWT 的 iss claim（P1-1）。"""
        from starlette.testclient import TestClient

        # 创建完整 server 以测试元数据
        from tests.mcp_helpers import build_test_mcp_app

        app = build_test_mcp_app(
            private_key_path=provider.rsa_manager.private_key_path,
            public_key_path=provider.rsa_manager.public_key_path,
            issuer="http://localhost:8000",
            audience="bs",
            auth_enabled=False,
            auth_username="admin",
        )

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        metadata = response.json()
        # 元数据中的 issuer 应匹配配置的 issuer
        assert metadata["issuer"] in ("http://localhost:8000", "http://localhost:8000/")

    # --- P1-2: 吊销 access token ---

    @pytest.mark.asyncio
    async def test_revoke_access_token_invalidates_it(self, provider):
        """吊销 access token 后应使 load_access_token 返回 None（P1-2）。"""
        from mcp.server.auth.provider import AccessToken

        # 创建一个有效的 access token
        claims = {
            "sub": "user1",
            "scope": "read write",
            "iss": "http://localhost:8000",
            "aud": "bs",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        }
        token_str = provider.rsa_manager.sign_jwt(claims)

        # 先加载它以确认有效
        access_token = await provider.load_access_token(token_str)
        assert access_token is not None
        assert isinstance(access_token, AccessToken)

        # 吊销它
        await provider.revoke_token(access_token)

        # 吊销后 load_access_token 应返回 None
        result = await provider.load_access_token(token_str)
        assert result is None

    # --- P2: valid_scopes ---

    def test_client_registration_options_has_valid_scopes(self, provider):
        """ClientRegistrationOptions 应声明 valid_scopes（P2）。"""
        assert provider.client_registration_options is not None
        assert provider.client_registration_options.valid_scopes == ["read", "write"]
