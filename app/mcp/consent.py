"""MCP consent 授权页的 HTTP 处理器（GET/POST ``/consent``）。

从 ``app.mcp.provider`` 拆分而来：模块级函数负责请求解析、会话校验与
CSRF 校验，具体的待授权状态操作仍委托给 ``BangumiOAuthProvider`` 实例。
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.core.public_url import redirect_public
from app.core.security import security_manager

if TYPE_CHECKING:
    from .provider import BangumiOAuthProvider


def _consent_login_redirect(request_token: str) -> RedirectResponse:
    """未登录访问 /consent 时重定向到登录页，登录成功后回跳 consent。

    ``next`` 为**不含 base_path** 的站内路径（与 ``app.api.pages._login_redirect``
    语义一致），且整体 urlencode，前端 ``static/js/auth.js`` 读取后会校验其为
    站内路径再回跳（开放重定向护栏）。
    """
    consent_path = f"/consent?{urlencode({'request_token': request_token})}"
    return redirect_public(f"/login?{urlencode({'next': consent_path})}")


async def handle_consent(request: Request, provider: BangumiOAuthProvider) -> Response:
    """处理 consent 页面的 GET/POST，可作为 custom_route 处理器使用。"""
    # 从 query（GET）或 form（POST）获取 request_token；POST 的 form 在此提取一次
    # 并在下方 CSRF 校验处复用，避免重复 await request.form()。
    form = None
    # 显式联合类型：GET 来自 query（str），POST 来自 form（str | UploadFile），
    # 下方统一按 isinstance(str) 收窄。
    raw: str | UploadFile | None
    if request.method == "GET":
        raw = request.query_params.get("request_token")
    else:
        form = await request.form()
        raw = form.get("request_token")

    # 统一收窄为 str：query/form 取值可能是 UploadFile，排除后按缺失处理。
    request_token = raw if isinstance(raw, str) else None

    if not request_token:
        return HTMLResponse("<h1>Error: missing request_token</h1>", status_code=400)

    # 从 cookie 中提取 session token
    session_token = None
    cookie = request.headers.get("cookie")
    if cookie:
        # 解析 cookie 以查找 session token
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("session_token="):
                session_token = part.split("=", 1)[1]
                break

    if request.method == "GET":
        # 当 auth.enabled=True 时先校验会话
        if provider.auth_enabled:
            if session_token:
                session = security_manager.validate_session(session_token)
                if not session:
                    return _consent_login_redirect(str(request_token))
            else:
                return _consent_login_redirect(str(request_token))

        context = await provider.get_consent_context(
            request_token, session_token=session_token
        )
        if context is None:
            return HTMLResponse(
                "<h1>Error: invalid or expired request</h1>", status_code=400
            )
        return HTMLResponse(provider._render_consent_form(context))

    # POST：校验 CSRF token（form 已在函数开头提取并复用）
    assert form is not None
    action = form.get("action", "deny")
    rt = str(request_token)
    raw_csrf = form.get("csrf_token")
    # 同样收窄为 str：非字符串（如 UploadFile）视为未提交 CSRF。
    submitted_csrf = raw_csrf if isinstance(raw_csrf, str) else None

    # 在任何删除操作之前查找待处理 auth
    pending_info = provider._pending_auths.get(rt)
    if pending_info is None:
        return HTMLResponse(
            "<h1>Error: invalid or expired request</h1>", status_code=400
        )

    # 校验 CSRF token（一次性使用，绑定到该待处理 auth）
    expected_csrf = pending_info.get("csrf_token", "")
    if not submitted_csrf or not hmac.compare_digest(
        str(submitted_csrf), str(expected_csrf)
    ):
        return HTMLResponse("<h1>Error: invalid CSRF token</h1>", status_code=403)

    redirect_uri = pending_info["redirect_uri"]
    state = pending_info.get("state")

    if action == "allow":
        # 当 auth_enabled=True 时，在 POST 允许时重新校验会话
        username = provider.auth_username
        if provider.auth_enabled:
            if session_token:
                session = security_manager.validate_session(session_token)
                if not session:
                    return _consent_login_redirect(rt)
                username = session.get("username", provider.auth_username)
            else:
                return _consent_login_redirect(rt)
        auth_code = await provider.handle_consent_allow(
            rt, username=username, csrf_token=submitted_csrf
        )
        params: dict[str, str] = {"code": auth_code}
        if state:
            params["state"] = state
        return RedirectResponse(
            url=f"{redirect_uri}?{urlencode(params)}", status_code=302
        )
    else:
        await provider.handle_consent_deny(rt)
        params = {
            "error": "access_denied",
            "error_description": "User denied authorization",
        }
        if state:
            params["state"] = state
        return RedirectResponse(
            url=f"{redirect_uri}?{urlencode(params)}", status_code=302
        )
