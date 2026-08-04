"""Bangumi OAuth 2.0 相关接口。

- ``GET  /api/oauth/bangumi/start``    生成授权 URL（需登录，接受动态 redirect_uri）
- ``GET  /api/oauth/bangumi/callback`` Bangumi 回调（用 code+state 换 token，免登录，靠 state 防 CSRF）
- ``GET  /api/oauth/bangumi/close``    回调换 token 后关闭弹窗的小页面（含 postMessage 通知父窗口）
- ``POST /api/oauth/bangumi/disconnect`` 断开 OAuth 关联（需登录）
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core.public_url import redirect_public
from ..services.bangumi.auth import bangumi_auth_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/oauth/bangumi", tags=["bangumi-oauth"])


async def _require_user(request: Request):
    user = await get_current_user_flexible(request)
    if not user:
        return None
    return user


@router.get("/start")
async def oauth_start(request: Request, redirect_uri: str = ""):
    """生成授权 URL。

    ``redirect_uri`` 由前端动态传入（取 ``window.location.origin +
    /api/oauth/bangumi/callback``），
    浏览器能访问发起授权的地址，自然也能接收 302 重定向回来，无需公网固定
    回调。redirect_uri 会绑定到 state 落库，回调换 token 时还原。

    安全：redirect_uri 必须以 ``/api/oauth/bangumi/callback`` 结尾，防止
    攻击者诱导已登录用户将授权码导向外部站点。
    """
    if not await _require_user(request):
        return JSONResponse(status_code=401, content={"detail": "未认证"})
    # 白名单校验：仅允许指向本服务的回调路径，防止 redirect_uri 被篡改导出授权码
    if redirect_uri and not redirect_uri.endswith("/api/oauth/bangumi/callback"):
        return JSONResponse(
            status_code=400,
            content={"detail": "回调地址必须以 /api/oauth/bangumi/callback 结尾"},
        )
    try:
        auth_url, state = bangumi_auth_service.get_auth_url(
            redirect_uri=redirect_uri or None
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})
    return {
        "auth_url": auth_url,
        "state": state,
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
    message = "授权成功，正在关闭窗口…" if ok else "授权失败，请关闭窗口后重试"
    # 通过 postMessage 通知父窗口（与 jellyfin-plugin-bangumi 一致），
    # 父窗口监听消息后刷新 OAuth 状态和账号列表
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>Bangumi 授权</title></head><body style='font-family:sans-serif;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
        f"<p>{message}</p>"
        "<script>"
        "if (window.opener) {"
        "try { window.opener.postMessage('BANGUMI-OAUTH-COMPLETE', '*'); } catch (e) {}"
        "}"
        "window.close();"
        "</script>"
        "</body></html>"
    )


@router.post("/disconnect")
async def oauth_disconnect(request: Request, section: str = ""):
    """断开指定账号的 OAuth 关联。

    ``section`` 为空时回退到当前激活账号（兼容旧行为）。断开后账号回退为
    手动模式，保留已填写的访问令牌；账号记录本身不删除。
    """
    if not await _require_user(request):
        return JSONResponse(status_code=401, content={"detail": "未认证"})
    ok = bangumi_auth_service.disconnect(section=section or None)
    if not ok:
        return JSONResponse(
            status_code=404, content={"detail": "未找到指定的 Bangumi 账号"}
        )
    return {"status": "success"}
