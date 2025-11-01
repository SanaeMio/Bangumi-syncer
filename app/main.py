"""
主应用文件
"""
import os
import sys
import time
import platform
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

from .core.config import config_manager
from .core.logging import logger
from .core.security import security_manager
from .core.database import database_manager
from .core.startup_info import startup_info
from version import get_version, get_version_name, get_version_info
from .services.mapping_service import mapping_service

from .api.sync import router as sync_router, root_router
from .api.auth import router as auth_router
from .api.config import router as config_router
from .api.mappings import router as mappings_router
from .api.logs import router as logs_router
from .api.pages import router as pages_router
from .api.health import router as health_router
from .api.proxy import router as proxy_router
from .api.notification import router as notification_router


# 创建FastAPI应用
app = FastAPI(
    title=get_version_name(), 
    description=get_version_info()["description"],
    version=get_version()
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
    startup_info.print_startup_complete()


# 关闭事件
@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    logger.info("Bangumi-Syncer 正在关闭...")


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
        access_log=False  # 禁用Uvicorn的访问日志
    ) 

