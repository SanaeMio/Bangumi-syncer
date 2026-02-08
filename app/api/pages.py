"""
页面路由模块
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .deps import get_current_user_from_cookie

# 设置模板引擎
templates = Jinja2Templates(directory="templates")

# 创建页面路由
router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """根路径重定向到仪表板"""
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """仪表板页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user}
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页面"""
    # 如果已经登录，直接跳转到主页
    user = get_current_user_from_cookie(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """配置管理页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse("config.html", {"request": request, "user": user})


@router.get("/records", response_class=HTMLResponse)
async def records_page(request: Request):
    """同步记录页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "records.html", {"request": request, "user": user}
    )


@router.get("/mappings", response_class=HTMLResponse)
async def mappings_page(request: Request):
    """映射管理页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "mappings.html", {"request": request, "user": user}
    )


@router.get("/debug", response_class=HTMLResponse)
async def debug_page(request: Request):
    """调试工具页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse("debug.html", {"request": request, "user": user})


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """日志页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse("logs.html", {"request": request, "user": user})


@router.get("/trakt/config", response_class=HTMLResponse)
async def trakt_config_page(request: Request):
    """Trakt 配置页面"""
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "trakt/config.html", {"request": request, "user": user}
    )


@router.get("/trakt/auth/success", response_class=HTMLResponse)
async def trakt_auth_success_page(request: Request):
    """Trakt 授权成功页面（不需要认证）"""
    return templates.TemplateResponse("trakt/auth_success.html", {"request": request})


@router.get("/trakt/auth", response_class=HTMLResponse)
async def trakt_auth_error_page(request: Request):
    """Trakt 授权错误页面（不需要认证）"""
    status = request.query_params.get("status", "")
    message = request.query_params.get("message", "")

    return templates.TemplateResponse(
        "trakt/auth_error.html",
        {"request": request, "status": status, "message": message},
    )
