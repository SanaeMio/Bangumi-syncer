"""通用 OAuth 抽象层：提供方注册表 + 共享服务实例。

其它媒体源接入 OAuth 时，只需在 ``providers.py`` 登记一个 ``OAuthProvider``，
即可复用 ``get_oauth_service()`` 的授权/换码/刷新/state 逻辑。
"""

from app.services.oauth.provider import OAuthProvider, OAuthProviderRegistry
from app.services.oauth.providers import BANGUMI_PROVIDER, TRAKT_PROVIDER
from app.services.oauth.service import OAuthService

_oauth_registry = OAuthProviderRegistry()
_oauth_registry.register(BANGUMI_PROVIDER)
_oauth_registry.register(TRAKT_PROVIDER)

_oauth_service = OAuthService(_oauth_registry)


def get_oauth_service() -> OAuthService:
    return _oauth_service


def get_oauth_registry() -> OAuthProviderRegistry:
    return _oauth_registry


def get_provider(name: str) -> OAuthProvider:
    return _oauth_registry.get(name)


__all__ = [
    "OAuthProvider",
    "OAuthProviderRegistry",
    "OAuthService",
    "get_oauth_service",
    "get_oauth_registry",
    "get_provider",
    "BANGUMI_PROVIDER",
    "TRAKT_PROVIDER",
]
