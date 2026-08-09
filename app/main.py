"""
主应用文件
"""

import asyncio
import os
import re
from contextlib import asynccontextmanager
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .api.airing_calendar import router as airing_calendar_router
from .api.app_release import router as app_release_router
from .api.auth import router as auth_router
from .api.bangumi_accounts import router as bangumi_accounts_router
from .api.bangumi_archive import router as bangumi_archive_router
from .api.bangumi_oauth import router as bangumi_oauth_router
from .api.bangumi_replay import router as bangumi_replay_router
from .api.bgm_poster import router as bgm_poster_router
from .api.config import router as config_router
from .api.feiniu import router as feiniu_router
from .api.fongmi import router as fongmi_router
from .api.health import router as health_router
from .api.inbox import router as inbox_router
from .api.llm import router as llm_router
from .api.logs import router as logs_router
from .api.mappings import router as mappings_router
from .api.notification import router as notification_router
from .api.pages import router as pages_router
from .api.proxy import router as proxy_router
from .api.summary_jobs import router as summary_jobs_router
from .api.sync import root_router, router as sync_router
from .api.trakt import router as trakt_router
from .api.upgrade import router as upgrade_router
from .core.app_version import get_version, get_version_info, get_version_name
from .core.background_tasks import (
    cancel_all as cancel_background_tasks,
    register_background_task,
    wait_all as wait_background_tasks,
)
from .core.config import config_manager
from .core.database import database_manager
from .core.logging import log_request_id, logger
from .core.public_url import get_public_base_path
from .core.scheduler_registry import scheduler_registry
from .core.startup_info import startup_info
from .services.feiniu.sync_service import ensure_feiniu_startup_watermark
from .services.mapping_service import mapping_service
from .services.scheduler_bootstrap import register_all as register_schedulers
from .services.sync_service import sync_service

# 创建FastAPI应用（root_path 便于反代子路径下 OpenAPI 等）
_app_kw: dict = {
    "title": get_version_name(),
    "description": get_version_info()["description"],
    "version": get_version(),
}
_rp = get_public_base_path()
if _rp:
    _app_kw["root_path"] = _rp


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动与关闭"""
    # ===== 启动 =====
    startup_info.print_info("🚀 应用启动中...")
    startup_info.print_separator()

    # 将旧 INI 账号段一次性迁移到数据库（幂等），账号以 DB 为唯一真相源
    try:
        from app.core.accounts import migrate_ini_accounts_to_db

        migrated = migrate_ini_accounts_to_db()
        if migrated:
            startup_info.print_success(f"迁移了 {migrated} 个 bangumi 账号到数据库")
    except Exception as e:
        startup_info.print_error(f"迁移 bangumi 账号到数据库失败: {e}")

    try:
        from app.core.accounts import list_bangumi_accounts

        accounts = list_bangumi_accounts()
        mappings = mapping_service.get_all_mappings()
        startup_info.print_success(f"加载了 {len(accounts)} 个 bangumi 账号（数据库）")
        startup_info.print_success(f"加载了 {len(mappings)} 个自定义映射")
    except Exception as e:
        startup_info.print_error(f"启动时加载配置信息失败: {e}")

    startup_info.print_separator()

    try:
        ensure_feiniu_startup_watermark()
    except Exception as e:
        logger.debug(f"飞牛启动水位检查: {e}")

    # 清理超过保留天数的同步记录，控制数据库体积
    try:
        retention_days = int(
            config_manager.get_config("dev", "sync_records_retention_days", 0)
        )
        database_manager.cleanup_old_records(retention_days)
        # 复用同一保留期清理待同步队列的 synced/abandoned 历史记录
        # （pending 是待处理任务，永不清理）
        database_manager.cleanup_pending_sync_queue(retention_days)
    except Exception as e:
        logger.warning(f"启动时清理旧同步记录失败（不影响主流程）: {e}")

    try:
        register_schedulers()
        scheduler_config = config_manager.get_scheduler_config()
        startup_delay = scheduler_config.get("startup_delay", 30)
        logger.info(f"调度器将在 {startup_delay} 秒后启动...")

        async def delayed_scheduler_start() -> None:
            await asyncio.sleep(startup_delay)
            await scheduler_registry.start_all()

        register_background_task(delayed_scheduler_start())
    except Exception as e:
        logger.error(f"启动调度器失败: {e}")

    startup_info.print_startup_complete()

    yield

    # ===== 关闭 =====
    logger.info("Bangumi-Syncer 正在关闭...")

    # 取消所有后台 fire-and-forget 任务，并等待最多 5 秒让它们清理资源
    cancel_background_tasks()
    await wait_background_tasks(timeout=5.0)

    try:
        await scheduler_registry.stop_all()
    except Exception as e:
        logger.error(f"停止调度器失败: {e}")

    try:
        sync_service.shutdown()
        logger.info("同步服务线程池已关闭")
    except Exception as e:
        logger.error(f"关闭同步服务线程池失败: {e}")

    try:
        database_manager.close()
        logger.info("数据库连接已关闭")
    except Exception as e:
        logger.error(f"关闭数据库连接失败: {e}")


app = FastAPI(**_app_kw, lifespan=lifespan)


# X-Request-ID 透传/生成规则：仅接受可见 ASCII 标点类安全字符，
# 拒绝 ]/[ 等会破坏日志行头结构的字符；缺失或不合法时自动生成。
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._~:+-]{1,64}$")


async def request_context_middleware(request: Request, call_next):
    """为请求注入 request_id（X-Request-ID 头或生成的短 ID），日志行关联 [req:...]。

    先于 csp_middleware 注册，保证最外层包裹：请求期间（含线程池任务，
    经 copy_context 传播）内所有日志行都携带同一个 request_id。
    """
    raw = request.headers.get("X-Request-ID", "").strip()
    if _REQUEST_ID_RE.fullmatch(raw):
        rid = raw
    else:
        rid = uuid4().hex[:12]
    token = log_request_id.set(rid)
    try:
        response = await call_next(request)
    finally:
        log_request_id.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


app.middleware("http")(request_context_middleware)


# 创建静态文件和模板目录
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册路由
app.include_router(sync_router)
app.include_router(root_router)  # 无前缀的同步接口（包含媒体服务器接口）
app.include_router(auth_router)
app.include_router(config_router)
app.include_router(bgm_poster_router)
app.include_router(mappings_router)
app.include_router(logs_router)
app.include_router(pages_router)
app.include_router(health_router)
app.include_router(app_release_router)
app.include_router(proxy_router)
app.include_router(notification_router)
app.include_router(llm_router)
app.include_router(summary_jobs_router)
app.include_router(inbox_router)
app.include_router(trakt_router)
app.include_router(feiniu_router)
app.include_router(fongmi_router)
app.include_router(upgrade_router)
app.include_router(bangumi_accounts_router)
app.include_router(bangumi_archive_router)
app.include_router(bangumi_oauth_router)
app.include_router(bangumi_replay_router)
app.include_router(airing_calendar_router)


# ─────────────────────────────────────────────────────────────────────────
# CSP 响应头（纵深防御，限制外域资源加载 + 禁用内联事件外的脚本注入）
# ─────────────────────────────────────────────────────────────────────────
# 现状：base.html 含内联防闪烁脚本，需保留 'unsafe-inline'
# 外域资源：仅 <a href> 跳转（bgm.tv / github.com），无外域 script/img 加载
# 图片：通过 /api/bgm/subjects/posters 后端代理 + data: 占位符
_CSP_HEADER = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


@app.middleware("http")
async def csp_middleware(request: Request, call_next):
    """为所有 HTML 响应附加 Content-Security-Policy 头"""
    response = await call_next(request)
    # 仅对 HTML 页面附加，避免静态资源 / API JSON 误伤
    ctype = response.headers.get("content-type", "")
    if "text/html" in ctype:
        response.headers["Content-Security-Policy"] = _CSP_HEADER
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
    return response


if __name__ == "__main__":
    # 配置Uvicorn日志
    uvicorn_logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "loggers": {
            "uvicorn": {
                "level": "DEBUG" if logger.debug_mode else "INFO",
            },
            "uvicorn.access": {
                "level": "WARNING",
            },
        },
    }

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=uvicorn_logging_config,
        access_log=False,  # 禁用Uvicorn的访问日志
    )
