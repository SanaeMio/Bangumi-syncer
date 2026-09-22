"""
FastMCP server 工厂

创建 FastMCP 实例并注册工具，生成 http_app，供 app/main.py 嵌入。
"""

import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import config_manager
from app.core.security import security_manager

from .consent import handle_consent
from .keys import RSAKeyManager
from .provider import REFRESH_TOKEN_TTL, BangumiOAuthProvider
from .tools import get_current_config, get_logs, update_config

# RSA 密钥默认存放目录（相对 cwd 的项目数据目录）：Docker 镜像 WORKDIR=/app
# 且已预建 /app/data，故容器内即 /app/data；该目录已加入 .gitignore。
_DATA_DIR = "data"

_DEFAULT_PRIVATE_KEY_FILENAME = "mcp_private.pem"
_DEFAULT_PUBLIC_KEY_FILENAME = "mcp_public.pem"

# base_url 占位：生产环境应从配置读取公共 URL
_DEFAULT_BASE_URL = "http://localhost:8000"


def _resolve_key_paths() -> tuple[str, str]:
    """解析 RSA 密钥对路径：环境变量优先，否则默认落项目数据目录 data/。

    默认落 ``data/``（相对 cwd；Docker 部署 WORKDIR=/app 即 ``/app/data``），
    避免容器重建或系统清理临时目录后重新生成密钥对，导致此前已签发的
    Access Token 全部验签失败、客户端被迫重新授权。
    ``MCP_RSA_PRIVATE_KEY`` / ``MCP_RSA_PUBLIC_KEY`` 仍可覆盖完整路径。

    Returns:
        (private_key_path, public_key_path)
    """
    private_key_path = os.environ.get(
        "MCP_RSA_PRIVATE_KEY",
        os.path.join(_DATA_DIR, _DEFAULT_PRIVATE_KEY_FILENAME),
    )
    public_key_path = os.environ.get(
        "MCP_RSA_PUBLIC_KEY",
        os.path.join(_DATA_DIR, _DEFAULT_PUBLIC_KEY_FILENAME),
    )
    return private_key_path, public_key_path


def _resolve_base_url(base_url: str | None = None) -> str:
    """解析 base_url：优先参数 > 环境变量 MCP_BASE_URL > 配置 > 默认值。"""
    if base_url:
        return base_url
    env_url = os.environ.get("MCP_BASE_URL")
    if env_url:
        return env_url
    # 从 dev 配置读取公共 URL（如有）
    configured = config_manager.get("dev", "mcp_base_url", fallback="")
    if configured:
        return configured
    return _DEFAULT_BASE_URL


def _create_provider(base_url: str | None = None) -> BangumiOAuthProvider:
    """创建携带 RSA 密钥的 BangumiOAuthProvider。

    auth_enabled / auth_username 从 BS 安全配置读取；
    base_url / 优先参数 > MCP_BASE_URL 环境变量 > 配置 > 默认值。
    """
    resolved_base_url = _resolve_base_url(base_url)
    auth_config = security_manager.get_auth_config()

    private_key_path, public_key_path = _resolve_key_paths()
    rsa_manager = RSAKeyManager(
        private_key_path=private_key_path,
        public_key_path=public_key_path,
    )
    rsa_manager.load_or_generate()

    refresh_ttl = int(os.environ.get("MCP_REFRESH_TOKEN_TTL", str(REFRESH_TOKEN_TTL)))

    return BangumiOAuthProvider(
        base_url=resolved_base_url,
        rsa_manager=rsa_manager,
        issuer=resolved_base_url,
        audience="bangumi-syncer",
        token_expiry_seconds=3600,
        refresh_token_ttl=refresh_ttl,
        auth_enabled=bool(auth_config["enabled"]),
        auth_username=str(auth_config["username"]),
    )


def _register_tools(mcp: FastMCP) -> None:
    """在 FastMCP 实例上注册 MCP 工具。

    使用 mcp.add_tool() 注册来自 app.mcp.tools 的异步工具函数。
    每个工具返回 {"status": "success", "data": ...} 结构，失败时抛出
    ToolError。
    """
    mcp.add_tool(get_logs)
    mcp.add_tool(get_current_config)
    mcp.add_tool(update_config)


def create_mcp_server(
    base_url: str | None = None,
    *,
    provider: BangumiOAuthProvider | None = None,
) -> FastMCP:
    """创建 FastMCP 实例，使用 BangumiOAuthProvider 并注册工具。

    **这是全仓库唯一装配 MCP app 的入口**：`/consent` 路由必须在
    `http_app()` 调用之前通过 `custom_route()` 注册，一旦装配逻辑散落到
    多个工厂，这条隐蔽约束极易被漏掉（历史上生产路径曾因此漏注册
    `/consent`）。所有调用方（含测试工厂）都应经由此函数装配。

    Args:
        base_url: 服务公共 URL，用于 OAuth metadata 中的 issuer / endpoint。
                  None 时从配置/环境变量自动解析。仅在 provider 为 None 时生效。
        provider: 可选注入的自定义 provider。提供时完全跳过
                  `_resolve_base_url` / `_create_provider` 的配置解析，
                  便于测试复用同一装配逻辑。

    Returns:
        FastMCP 实例（已配置 auth、注册 /consent 路由、注册 3 个工具）。
    """
    if provider is None:
        provider = _create_provider(base_url=base_url)
    mcp = FastMCP(name="bangumi-syncer", auth=provider)

    # 注册 /consent 路由（必须在 http_app() 调用前完成）
    @mcp.custom_route("/consent", methods=["GET", "POST"])
    async def consent_route(request: Request) -> Response:
        return await handle_consent(request, provider)

    _register_tools(mcp)
    return mcp


def create_mcp_app(
    base_url: str | None = None,
    *,
    provider: BangumiOAuthProvider | None = None,
):
    """创建 FastMCP http_app（Starlette app），用于路由提取与 lifespan 合并。

    内部委托给 `create_mcp_server`——后者是唯一装配入口，负责在
    `http_app()` 之前注册 `/consent`（这是本函数不直接构造 FastMCP 的原因）。

    Args:
        base_url: 服务公共 URL。None 时从配置/环境变量自动解析。
        provider: 可选注入的自定义 provider，透传给 `create_mcp_server`。

    Returns:
        StarletteWithLifespan 实例，包含：
        - /.well-known/oauth-authorization-server
        - /.well-known/oauth-protected-resource/mcp
        - /authorize
        - /token
        - /consent
        - /mcp（工具端点，含 3 个注册工具）
    """
    mcp = create_mcp_server(base_url=base_url, provider=provider)
    return mcp.http_app(path="/mcp")


# 模块级单例：供 app/main.py 直接导入
mcp_app = create_mcp_app()
