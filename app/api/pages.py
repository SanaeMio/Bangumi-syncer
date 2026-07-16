"""
页面路由模块
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..core.app_version import get_display_version
from ..core.config import config_manager
from ..core.public_url import get_public_base_path, redirect_public
from ..core.web_templates import get_templates
from ..utils.bgm_image_url import build_poster_cache_namespace
from .deps import get_current_user_from_cookie


def _login_redirect(request: Request):
    """重定向到登录页，携带当前站内路径作为 next 参数"""
    path = request.url.path
    base = get_public_base_path()
    if base and path.startswith(base):
        path = path[len(base) :]
    if not path or path == "/login":
        path = "/dashboard"
    return redirect_public(f"/login?next={path}")


templates = get_templates()

# 创建页面路由
router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    """根路径重定向到仪表板"""
    return redirect_public("/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """仪表板页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return _login_redirect(request)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "app_display_version": get_display_version(),
            "poster_cache_ns": build_poster_cache_namespace(
                config_manager.get("dev", "bgm_api_proxy", fallback=""),
                config_manager.get("dev", "bgm_image_proxy", fallback=""),
            ),
        },
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """登录页面"""
    # 如果已经登录，直接跳转到主页
    user = get_current_user_from_cookie(request)
    if user:
        return redirect_public("/dashboard")

    return templates.TemplateResponse(request, "login.html")


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    """配置管理页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return _login_redirect(request)

    return templates.TemplateResponse(request, "config.html", {"user": user})


@router.get("/records", response_class=HTMLResponse)
async def records_page(request: Request):
    """同步记录页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return _login_redirect(request)

    return templates.TemplateResponse(request, "records.html", {"user": user})


@router.get("/mappings", response_class=HTMLResponse)
async def mappings_page(request: Request):
    """映射管理页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return _login_redirect(request)

    return templates.TemplateResponse(request, "mappings.html", {"user": user})


@router.get("/match-records", response_class=HTMLResponse)
async def match_records_page(request: Request):
    """匹配记录页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return _login_redirect(request)

    return templates.TemplateResponse(request, "match_records.html", {"user": user})


@router.get("/debug", response_class=HTMLResponse)
async def debug_page(request: Request):
    """调试工具页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return _login_redirect(request)

    return templates.TemplateResponse(request, "debug.html", {"user": user})


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """日志页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return _login_redirect(request)

    return templates.TemplateResponse(request, "logs.html", {"user": user})


@router.get("/trakt/config", response_class=HTMLResponse)
async def trakt_config_page(request: Request) -> HTMLResponse:
    """Trakt 配置页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return _login_redirect(request)

    return templates.TemplateResponse(request, "trakt/config.html", {"user": user})


@router.get("/trakt/auth/success", response_class=HTMLResponse)
async def trakt_auth_success_page(request: Request) -> HTMLResponse:
    """Trakt 授权成功页面（不需要认证）"""
    return templates.TemplateResponse(request, "trakt/auth_success.html")


@router.get("/trakt/auth", response_class=HTMLResponse)
async def trakt_auth_error_page(request: Request) -> HTMLResponse:
    """Trakt 授权错误页面（不需要认证）"""
    status = request.query_params.get("status", "")
    message = request.query_params.get("message", "")

    return templates.TemplateResponse(
        request,
        "trakt/auth_error.html",
        {"status": status, "message": message},
    )
