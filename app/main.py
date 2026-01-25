"""
主应用文件
"""

import os

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from version import get_version, get_version_info, get_version_name

from .api.auth import router as auth_router
from .api.config import router as config_router
from .api.health import router as health_router
from .api.logs import router as logs_router
from .api.mappings import router as mappings_router
from .api.notification import router as notification_router
from .api.pages import router as pages_router
from .api.proxy import router as proxy_router
from .api.sync import root_router, router as sync_router
from .api.trakt import router as trakt_router
from .core.config import config_manager
from .core.logging import logger
from .core.startup_info import startup_info
from .services.mapping_service import mapping_service
from .services.trakt.scheduler import trakt_scheduler

# 创建FastAPI应用
app = FastAPI(
    title=get_version_name(),
    description=get_version_info()["description"],
    version=get_version(),
)


# 创建静态文件和模板目录
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 设置模板引擎
templates = Jinja2Templates(directory="templates")

# 注册路由
app.include_router(sync_router)
app.include_router(root_router)  # 无前缀的同步接口（包含媒体服务器接口）
app.include_router(auth_router)
app.include_router(config_router)
app.include_router(mappings_router)
app.include_router(logs_router)
app.include_router(pages_router)
app.include_router(health_router)
app.include_router(proxy_router)
app.include_router(notification_router)
app.include_router(trakt_router)


# 启动事件
@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    startup_info.print_info("🚀 应用启动中...")
    startup_info.print_separator()

    # 输出配置信息
    try:
        # 加载所有配置
        bangumi_configs = config_manager.get_bangumi_configs()
        user_mappings = config_manager.get_user_mappings()
        mappings = mapping_service.get_all_mappings()

        # 显示汇总信息
        startup_info.print_success(f"加载了 {len(bangumi_configs)} 个bangumi账号配置")
        startup_info.print_success(f"加载了 {len(user_mappings)} 个用户映射配置")
        startup_info.print_success(f"加载了 {len(mappings)} 个自定义映射")

    except Exception as e:
        startup_info.print_error(f"启动时加载配置信息失败: {e}")

    startup_info.print_separator()

    # 启动 Trakt 调度器（延迟启动）
    try:
        scheduler_config = config_manager.get_scheduler_config()
        startup_delay = scheduler_config.get("startup_delay", 30)

        logger.info(f"Trakt 调度器将在 {startup_delay} 秒后启动...")

        # 使用异步任务延迟启动调度器
        import asyncio

        async def delayed_scheduler_start():
            await asyncio.sleep(startup_delay)
            success = trakt_scheduler.start()
            if success:
                logger.info("Trakt 调度器启动成功")
            else:
                logger.error("Trakt 调度器启动失败")

        asyncio.create_task(delayed_scheduler_start())

    except Exception as e:
        logger.error(f"启动 Trakt 调度器失败: {e}")

    startup_info.print_startup_complete()


# 关闭事件
@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    logger.info("Bangumi-Syncer 正在关闭...")

    # 停止 Trakt 调度器
    try:
        trakt_scheduler.stop()
        logger.info("Trakt 调度器已停止")
    except Exception as e:
        logger.error(f"停止 Trakt 调度器失败: {e}")


if __name__ == "__main__":
    # TODO 开发配置,合入前删除
    import debugpy

    # 允许远程连接并监听 5678 端口
    # 0.0.0.0 表示监听所有网络接口，127.0.0.1 仅限本地
    debugpy.listen(("0.0.0.0", 5678))
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
