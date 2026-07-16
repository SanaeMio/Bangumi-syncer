"""
主应用文件
"""

import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.app_release import router as app_release_router
from .api.auth import router as auth_router
from .api.bgm_poster import router as bgm_poster_router
from .api.config import router as config_router
from .api.feiniu import router as feiniu_router
from .api.fongmi import router as fongmi_router
from .api.health import router as health_router
from .api.inbox import router as inbox_router
from .api.logs import router as logs_router
from .api.mappings import router as mappings_router
from .api.notification import router as notification_router
from .api.pages import router as pages_router
from .api.proxy import router as proxy_router
from .api.sync import root_router, router as sync_router
from .api.trakt import router as trakt_router
from .api.upgrade import router as upgrade_router
from .core.app_version import get_version, get_version_info, get_version_name
from .core.config import config_manager
from .core.database import database_manager
from .core.logging import logger
from .core.public_url import get_public_base_path
from .core.startup_info import startup_info
from .services.feiniu.scheduler import feiniu_scheduler
from .services.feiniu.sync_service import ensure_feiniu_startup_watermark
from .services.fongmi.scheduler import fongmi_scheduler
from .services.mapping_service import mapping_service
from .services.sync_service import sync_service
from .services.trakt.scheduler import trakt_scheduler

_background_tasks: set[asyncio.Task] = set()

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

    try:
        bangumi_configs = config_manager.get_bangumi_configs()
        user_mappings = config_manager.get_user_mappings()
        mappings = mapping_service.get_all_mappings()
        startup_info.print_success(f"加载了 {len(bangumi_configs)} 个bangumi账号配置")
        startup_info.print_success(f"加载了 {len(user_mappings)} 个用户映射配置")
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
    except Exception as e:
        logger.warning(f"启动时清理旧同步记录失败（不影响主流程）: {e}")

    try:
        scheduler_config = config_manager.get_scheduler_config()
        startup_delay = scheduler_config.get("startup_delay", 30)
        logger.info(f"Trakt 调度器将在 {startup_delay} 秒后启动...")

        async def delayed_scheduler_start() -> None:
            await asyncio.sleep(startup_delay)
            for name, coro in [
                ("Trakt", trakt_scheduler.start),
                ("飞牛", feiniu_scheduler.start),
                ("fongmi", fongmi_scheduler.start),
            ]:
                try:
                    ok = await coro()
                    logger.info(f"{name} 调度器启动{'成功' if ok else '失败'}")
                except Exception as e:
                    logger.error(f"{name} 调度器启动异常: {e}")

        task = asyncio.create_task(delayed_scheduler_start())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.error(f"启动调度器失败: {e}")

    startup_info.print_startup_complete()

    yield

    # ===== 关闭 =====
    logger.info("Bangumi-Syncer 正在关闭...")

    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()

    for name, coro in [
        ("Trakt", trakt_scheduler.stop),
        ("飞牛", feiniu_scheduler.stop),
        ("fongmi", fongmi_scheduler.stop),
    ]:
        try:
            await coro()
            logger.info(f"{name} 调度器已停止")
        except Exception as e:
            logger.error(f"停止{name}调度器失败: {e}")

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
app.include_router(inbox_router)
app.include_router(trakt_router)
app.include_router(feiniu_router)
app.include_router(fongmi_router)
app.include_router(upgrade_router)


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
