"""通用 OAuth 2.0 服务：授权 URL 构建、令牌交换/刷新、CSRF state 管理。

所有 OAuth 提供方共用此实现；state 统一落库到 ``oauth_states`` 表，
取代此前 Bangumi 的临时 JSON 文件与 Trakt 的进程内字典两套冗余实现。
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import httpx

from app.core.database import database_manager
from app.services.oauth.provider import OAuthProvider

# state 有效期（秒），防止重放
OAUTH_STATE_TTL = 600


class OAuthService:
    def __init__(self, registry) -> None:
        self.registry = registry

    def get_provider(self, name: str) -> OAuthProvider:
        return self.registry.get(name)

    # ── CSRF state（统一落库）──────────────────────────────────
    def create_state(
        self,
        provider_name: str,
        account_key: str,
        redirect_uri: str = "",
    ) -> str:
        """生成并保存一个授权 state，返回其值。

        ``redirect_uri`` 用于存发起授权时使用的回调地址，回调换 token 时
        还原以保证 authorize 与 token 交换用同一 redirect_uri（OAuth 2.0 要求）。
        """
        state = secrets.token_urlsafe(16)
        expires_at = int(time.time()) + OAUTH_STATE_TTL
        database_manager.save_oauth_state(
            state,
            account_key,
            expires_at,
            provider=provider_name,
            redirect_uri=redirect_uri,
        )
        return state

    def consume_state(self, provider_name: str, state: str) -> dict | None:
        """校验并消费 state，返回 ``{account_key, redirect_uri}``；无效/过期/不匹配返回 None。

        使用 ``delete_oauth_state`` 的 rowcount 作为消费凭据，避免
        SELECT-then-DELETE 在并发下双重消费 state 导致 CSRF 防护失效
        （两个请求都通过 SELECT，第二个 DELETE 命中 0 行但旧实现未检查）。
        """
        rec = database_manager.get_oauth_state(state)
        if not rec:
            return None
        if rec.get("provider") not in ("", provider_name):
            return None
        # 原子消费：仅当 state 仍存在（rowcount>0）时才算消费成功；
        # 并发场景下后到的 DELETE 命中 0 行，返回 None 拒绝回调。
        if not database_manager.delete_oauth_state(state):
            return None
        # DB 列名为 section_name（save_oauth_state 的第二参数），
        # 对 Trakt 场景存的是 user_id，对 Bangumi 场景存的是 section_name
        return {
            "account_key": rec.get("section_name"),
            "redirect_uri": rec.get("redirect_uri") or "",
        }

    # ── 授权 URL ────────────────────────────────────────────────
    def build_authorize_url(
        self,
        provider: OAuthProvider,
        *,
        state: str,
        scopes: list[str] | None = None,
        extra_params: dict | None = None,
        redirect_uri_override: str | None = None,
    ) -> str:
        client_id, _ = provider.get_credentials()
        params: dict[str, Any] = dict(provider.extra_auth_params)
        params.setdefault("response_type", "code")
        params["client_id"] = client_id
        params["redirect_uri"] = (
            redirect_uri_override
            if redirect_uri_override
            else provider.get_redirect_uri()
        )
        params["state"] = state
        scope = scopes if scopes is not None else provider.scopes
        if scope:
            params["scope"] = " ".join(scope)
        if extra_params:
            params.update(extra_params)
        from urllib.parse import urlencode

        return f"{provider.authorize_url}?{urlencode(params)}"

    # ── 令牌交换 / 刷新 ─────────────────────────────────────────
    def exchange_code(
        self,
        provider_name: str,
        code: str,
        *,
        redirect_uri_override: str | None = None,
    ) -> dict[str, Any]:
        """用授权码向提供方令牌端点换取令牌（state 由调用方先行校验）。"""
        provider = self.get_provider(provider_name)
        client_id, client_secret = provider.get_credentials()
        payload = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": (
                redirect_uri_override
                if redirect_uri_override
                else provider.get_redirect_uri()
            ),
        }
        resp = self._post_token(provider.token_url, payload)
        resp.raise_for_status()
        return resp.json()

    def refresh_token(self, provider_name: str, refresh_token: str) -> dict[str, Any]:
        """用 refresh_token 刷新访问令牌。"""
        provider = self.get_provider(provider_name)
        client_id, client_secret = provider.get_credentials()
        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "redirect_uri": provider.get_redirect_uri(),
        }
        resp = self._post_token(provider.token_url, payload)
        resp.raise_for_status()
        return resp.json()

    def _post_token(self, token_url: str, payload: dict[str, Any]) -> httpx.Response:
        """向令牌端点发 POST（bangumi 域走 ECH，其他提供方走普通 TLS）。"""
        from app.core.config import config_manager
        from app.utils.http_client import create_sync_client

        mode = str(config_manager.get("dev", "ech_mode", fallback="off") or "").strip()
        ech = mode if "bgm.tv" in token_url else False
        with create_sync_client(ech=ech, timeout=30.0) as client:
            return client.post(token_url, data=payload)
