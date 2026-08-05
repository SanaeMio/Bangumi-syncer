"""内置 OAuth 提供方登记：Bangumi 与 Trakt。

凭证与回跳地址通过延迟导入的解析函数获取，避免与各自 auth 模块形成循环依赖。
新增媒体源接入时，只需在此追加一个 ``OAuthProvider`` 即可复用通用流程。
"""

from app.services.oauth.provider import OAuthProvider


def _bangumi_credentials():
    from app.services.bangumi.auth import get_app_credentials

    return get_app_credentials()


def _bangumi_redirect():
    from app.services.bangumi.auth import get_redirect_uri

    return get_redirect_uri()


BANGUMI_PROVIDER = OAuthProvider(
    name="bangumi",
    authorize_url="https://bgm.tv/oauth/authorize",
    token_url="https://bgm.tv/oauth/access_token",
    redirect_path="/api/oauth/bangumi/callback",
    scopes=[],
    extra_auth_params={"response_type": "code"},
    get_credentials=_bangumi_credentials,
    get_redirect_uri=_bangumi_redirect,
)


def _trakt_credentials():
    from app.services.trakt.auth import get_trakt_app_credentials

    return get_trakt_app_credentials()


def _trakt_redirect():
    from app.services.trakt.auth import get_trakt_redirect_uri

    return get_trakt_redirect_uri()


TRAKT_PROVIDER = OAuthProvider(
    name="trakt",
    authorize_url="https://trakt.tv/oauth/authorize",
    token_url="https://trakt.tv/oauth/access_token",
    redirect_path="/api/oauth/trakt/callback",
    scopes=[],
    extra_auth_params={"response_type": "code"},
    get_credentials=_trakt_credentials,
    get_redirect_uri=_trakt_redirect,
)
