"""测试专用 MCP 装配 helper。

测试不再自行复制一份「FastMCP + /consent + http_app」的装配逻辑，而是
复用生产唯一装配入口 ``create_mcp_app``；这样 ``/consent`` 必须在
``http_app()`` 之前注册这类隐蔽约束只存在于生产代码一处，测试不会与
生产路径产生漂移。

本模块仅提供测试装配，不参与 pytest 收集（文件名不匹配 ``test_*.py``
/ ``*_test.py``）。
"""

from __future__ import annotations

from typing import Any

from app.mcp.keys import RSAKeyManager
from app.mcp.provider import BangumiOAuthProvider
from app.mcp.server import create_mcp_app


def build_test_mcp_app(
    private_key_path: str,
    public_key_path: str,
    issuer: str,
    audience: str,
    token_expiry_seconds: int = 3600,
    *,
    auth_enabled: bool = False,
    auth_username: str = "admin",
    base_url: str | None = None,
    required_scopes: list[str] | None = None,
) -> Any:
    """构造一个启用 auth 的 Starlette app，供测试使用。

    复用生产唯一装配入口 ``create_mcp_app(provider=...)``，避免测试复制
    ``/consent`` 注册时机逻辑。provider 的 ``client_registration_options``
    与 ``revocation_options`` 不显式传入，走 ``BangumiOAuthProvider`` 默认值
    （``valid_scopes=["read", "write"]``、revocation 启用），与生产一致。

    .. note::
       本 helper **刻意不显式传** ``ClientRegistrationOptions``：它复用生产默认
       ``valid_scopes=["read", "write"]``，即 DCR 请求的 scope 必须落在该允许集内。
       这与被替换的旧内联工厂 ``_create_test_server_with_tools`` 所用的
       ``ClientRegistrationOptions(enabled=True)``（``valid_scopes=None``，即
       **不校验**允许集）语义边界不同；两者并非等价，本 helper 有意对齐生产行为，
       因此不做显式传入。

    Args:
        private_key_path: RSA 私钥路径。
        public_key_path: RSA 公钥路径。
        issuer: OAuth issuer（写入 metadata 与 JWT ``iss``）。
        audience: JWT ``aud``，测试用 ``jwt.decode(..., audience=...)`` 校验。
        token_expiry_seconds: access token 有效期。
        auth_enabled: 是否启用真人授权（consent）环节。
        auth_username: 授权时允许的用户名。
        base_url: 服务公共 URL，None 时回退到 ``issuer``。
        required_scopes: 透传给 ``BangumiOAuthProvider`` 的传输层准入底线；
            None 时走生产默认 ``["read"]``，显式传 ``[]`` 可关闭 scope 门槛。

    Returns:
        带 OAuth 端点与 consent 页面的 Starlette app；``app.state`` 上附带
        ``public_key_pem`` 与 ``provider`` 供测试断言使用。
    """
    rsa_manager = RSAKeyManager(
        private_key_path=private_key_path,
        public_key_path=public_key_path,
    )
    rsa_manager.load_or_generate()

    # 不显式传 client_registration_options：复用生产默认 valid_scopes=["read", "write"]，
    # 与旧测试工厂的 valid_scopes=None（不校验允许集）不同，有意对齐生产行为。
    provider = BangumiOAuthProvider(
        base_url=base_url or issuer,
        rsa_manager=rsa_manager,
        issuer=issuer,
        audience=audience,
        token_expiry_seconds=token_expiry_seconds,
        auth_enabled=auth_enabled,
        auth_username=auth_username,
        required_scopes=required_scopes,
    )

    app = create_mcp_app(provider=provider)

    # 供测试断言：公钥验签与直接操作 provider 状态。
    app.state.public_key_pem = rsa_manager.get_public_key_pem()
    app.state.provider = provider

    return app
