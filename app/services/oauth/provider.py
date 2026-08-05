"""OAuth 提供方配置与注册表（通用抽象）。

让 Bangumi、Trakt 等不同 OAuth 2.0 授权方共用同一套
授权 URL 构建 / 令牌交换 / 刷新 / CSRF state 管理逻辑，
仅需在此登记各自的端点与凭证解析方式即可接入新来源。
"""

from dataclasses import dataclass, field
from typing import Callable

OAuthCredentials = tuple[str, str]


@dataclass
class OAuthProvider:
    """单个 OAuth 提供方的静态配置。

    ``get_credentials`` / ``get_redirect_uri`` 以可调用形式提供，便于延迟导入，
    避免与具体媒体源模块形成循环依赖。
    """

    name: str
    authorize_url: str
    token_url: str
    redirect_path: str
    scopes: list[str] = field(default_factory=list)
    extra_auth_params: dict = field(default_factory=dict)
    get_credentials: Callable[[], OAuthCredentials] = lambda: ("", "")
    get_redirect_uri: Callable[[], str] = lambda: ""


class OAuthProviderRegistry:
    """提供方注册表。"""

    def __init__(self) -> None:
        self._providers: dict[str, OAuthProvider] = {}

    def register(self, provider: OAuthProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> OAuthProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise KeyError(f"未注册的 OAuth 提供方：{name}")
        return provider

    def names(self) -> list[str]:
        return list(self._providers.keys())
