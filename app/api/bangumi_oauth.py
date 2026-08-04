"""Bangumi OAuth 2.0 相关接口。

- ``GET  /api/oauth/bangumi/start``    生成授权 URL（需登录）
- ``GET  /api/oauth/bangumi/callback`` Bangumi 回调（用 code+state 换 token，免登录，靠 state 防 CSRF）
- ``GET  /api/oauth/bangumi/close``    回调换 token 后关闭弹窗的小页面
- ``GET  /api/oauth/bangumi/status``   当前连接状态（需登录）
- ``GET  /api/oauth/bangumi/redirect`` 当前使用的 redirect_uri（需登录）
- ``POST /api/oauth/bangumi/disconnect`` 断开 OAuth 关联（需登录）
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core.public_url import redirect_public
from ..services.bangumi.auth import bangumi_auth_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/oauth/bangumi", tags=["bangumi-oauth"])


def _require_user(request: Request):
    user = get_current_user_flexible(request)
    if not user:
        return None
    return user


@router.get("/start")
async def oauth_start(request: Request):
    if not _require_user(request):
        return JSONResponse(status_code=401, content={"detail": "未认证"})
    try:
        auth_url, state = bangumi_auth_service.get_auth_url()
    except ValueError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})
    return {
        "auth_url": auth_url,
        "state": state,
        "redirect_uri": bangumi_auth_service.get_redirect_uri(),
    }


@router.get("/callback")
async def oauth_callback(request: Request, code: str = "", state: str = ""):
    if not code or not state:
        return redirect_public("/api/oauth/bangumi/close?result=error")
    try:
        bangumi_auth_service.exchange_code_for_token(code, state)
    except Exception:
        return redirect_public("/api/oauth/bangumi/close?result=error")
    return redirect_public("/api/oauth/bangumi/close?result=success")


@router.get("/close")
async def oauth_close(result: str = "success"):
    ok = result == "success"
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>Bangumi 授权</title></head><body style='font-family:sans-serif;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
        f"<p>{'授权成功，正在关闭窗口…' if ok else '授权失败，请关闭窗口后重试'}</p>"
        "<script>window.close();</script>"
        "</body></html>"
    )


@router.get("/status")
async def oauth_status(request: Request):
    if not _require_user(request):
        return JSONResponse(status_code=401, content={"detail": "未认证"})
    return bangumi_auth_service.get_connection_status()


@router.get("/redirect")
async def oauth_redirect_uri(request: Request):
    if not _require_user(request):
        return JSONResponse(status_code=401, content={"detail": "未认证"})
    return {"redirect_uri": bangumi_auth_service.get_redirect_uri()}


@router.post("/disconnect")
async def oauth_disconnect(request: Request):
    if not _require_user(request):
        return JSONResponse(status_code=401, content={"detail": "未认证"})
    bangumi_auth_service.disconnect()
    return {"status": "success"}
