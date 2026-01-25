"""
Trakt.tv API 路由
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from ..core.config import config_manager
from ..core.database import database_manager
from ..core.logging import logger
from ..models.trakt import (
    TraktApiConfigUpdateRequest,
    TraktAuthRequest,
    TraktAuthResponse,
    TraktCallbackRequest,
    TraktConfigResponse,
    TraktConfigUpdateRequest,
    TraktManualSyncRequest,
    TraktManualSyncResponse,
    TraktSyncStatusResponse,
)
from ..services.trakt.auth import trakt_auth_service
from ..services.trakt.scheduler import trakt_scheduler
from ..services.trakt.sync_service import trakt_sync_service

router = APIRouter(prefix="/api/trakt", tags=["trakt"])


@router.post("/auth/init", response_model=TraktAuthResponse)
async def init_trakt_auth(request: TraktAuthRequest, req: Request) -> TraktAuthResponse:
    """初始化 Trakt OAuth 授权"""
    try:
        # 这里应该从会话或令牌中获取实际用户ID
        # 暂时使用请求中的 user_id
        user_id = request.user_id

        auth_response = await trakt_auth_service.init_oauth(user_id)

        if not auth_response:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Trakt 配置无效或初始化失败",
            )

        return auth_response

    except Exception as e:
        logger.error(f"初始化 Trakt 授权失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"初始化授权失败: {str(e)}",
        )


@router.get("/auth/callback")
async def trakt_auth_callback(
    code: str,
    state: Optional[str] = None,
) -> RedirectResponse:
    """Trakt OAuth 回调处理"""
    try:
        # 这里需要从会话或 state 中获取用户ID
        # 简化处理：使用默认用户ID
        user_id = "default_user"  # TODO: 从会话或 state 中获取实际用户ID

        callback_request = TraktCallbackRequest(code=code, state=state or "")
        callback_response = await trakt_auth_service.handle_callback(
            callback_request, user_id
        )

        if callback_response.success:
            # 重定向到成功页面（不需要认证）
            return RedirectResponse(url="/trakt/auth/success")
        else:
            # 重定向到失败页面
            return RedirectResponse(
                url=f"/trakt/auth?status=error&message={callback_response.message}"
            )

    except Exception as e:
        logger.error(f"处理 Trakt 回调失败: {e}")
        return RedirectResponse(url=f"/trakt/auth?status=error&message={str(e)}")


@router.get("/config", response_model=TraktConfigResponse)
async def get_trakt_config() -> TraktConfigResponse:
    """获取当前用户的 Trakt 配置"""
    try:
        # TODO: 从会话或令牌中获取实际用户ID
        user_id = "default_user"

        config = trakt_auth_service.get_user_trakt_config(user_id)

        # 从配置文件获取 API 配置
        trakt_api_config = config_manager.get_trakt_config()

        if not config:
            return TraktConfigResponse(
                user_id=user_id,
                enabled=False,
                sync_interval="0 */6 * * *",
                last_sync_time=None,
                is_connected=False,
                token_expires_at=None,
                client_id=trakt_api_config.get("client_id", ""),
                client_secret=trakt_api_config.get("client_secret", ""),
                redirect_uri=trakt_api_config.get(
                    "redirect_uri", "http://localhost:8000/api/trakt/auth/callback"
                ),
            )

        # 检查令牌是否有效
        is_connected = bool(config.access_token) and not config.is_token_expired()

        return TraktConfigResponse(
            user_id=config.user_id,
            enabled=config.enabled,
            sync_interval=config.sync_interval,
            last_sync_time=config.last_sync_time,
            is_connected=is_connected,
            token_expires_at=config.expires_at,
            client_id=trakt_api_config.get("client_id", ""),
            client_secret=trakt_api_config.get("client_secret", ""),
            redirect_uri=trakt_api_config.get(
                "redirect_uri", "http://localhost:8000/api/trakt/auth/callback"
            ),
        )

    except Exception as e:
        logger.error(f"获取 Trakt 配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取配置失败: {str(e)}",
        )


@router.put("/config", response_model=TraktConfigResponse)
async def update_trakt_config(
    update_request: TraktConfigUpdateRequest,
) -> TraktConfigResponse:
    """更新 Trakt 配置"""
    try:
        # TODO: 从会话或令牌中获取实际用户ID
        user_id = "default_user"

        config = trakt_auth_service.get_user_trakt_config(user_id)

        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trakt 配置未找到，请先完成授权",
            )

        # 更新配置
        if update_request.enabled is not None:
            config.enabled = update_request.enabled

        if update_request.sync_interval is not None:
            config.sync_interval = update_request.sync_interval

        # 保存到数据库

        success = database_manager.save_trakt_config(config.to_dict())

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="保存配置失败",
            )

        # 返回更新后的配置
        is_connected = bool(config.access_token) and not config.is_token_expired()

        return TraktConfigResponse(
            user_id=config.user_id,
            enabled=config.enabled,
            sync_interval=config.sync_interval,
            last_sync_time=config.last_sync_time,
            is_connected=is_connected,
            token_expires_at=config.expires_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新 Trakt 配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新配置失败: {str(e)}",
        )


@router.put("/config/api", response_model=dict)
async def update_trakt_api_config(
    update_request: TraktApiConfigUpdateRequest,
) -> dict:
    """更新 Trakt API 配置"""
    try:
        # 获取当前配置
        trakt_config = config_manager.get_trakt_config()

        # 更新配置
        if update_request.client_id is not None:
            trakt_config["client_id"] = update_request.client_id
            config_manager.set("trakt", "client_id", update_request.client_id)

        if update_request.client_secret is not None:
            trakt_config["client_secret"] = update_request.client_secret
            config_manager.set("trakt", "client_secret", update_request.client_secret)

        if update_request.redirect_uri is not None:
            trakt_config["redirect_uri"] = update_request.redirect_uri
            config_manager.set("trakt", "redirect_uri", update_request.redirect_uri)

        # 保存配置
        config_manager.save_config()

        return {"success": True, "message": "API 配置保存成功"}

    except Exception as e:
        logger.error(f"更新 Trakt API 配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新 API 配置失败: {str(e)}",
        )


@router.get("/sync/status", response_model=TraktSyncStatusResponse)
async def get_trakt_sync_status() -> TraktSyncStatusResponse:
    """获取 Trakt 同步状态"""
    try:
        # TODO: 从会话或令牌中获取实际用户ID
        user_id = "default_user"

        config = trakt_auth_service.get_user_trakt_config(user_id)

        if not config:
            return TraktSyncStatusResponse(
                is_running=False,
                last_sync_time=None,
                next_sync_time=None,
                success_count=0,
                error_count=0,
                total_count=0,
            )

        # 从调度器获取作业状态
        job_status = trakt_scheduler.get_user_job_status(user_id)

        # 计算下次执行时间
        next_sync_time = None
        if job_status and job_status.get("next_run_time"):
            next_sync_time = int(job_status["next_run_time"])

        # 检查是否有正在运行的任务
        is_running = False  # TODO: 需要实现任务运行状态跟踪

        # 从数据库获取同步统计信息
        # 查询该用户的 Trakt 同步记录
        # TODO: 应该做分页查询,直到获取全量的记录进行统计
        sync_stats = database_manager.get_sync_records(
            limit=1000,  # 获取足够多的记录以统计
            user_name=user_id,  # 注意：user_name 字段可能需要映射
            source_prefix="trakt",
        )

        # 计算成功和失败数量
        success_count = 0
        error_count = 0

        if sync_stats and "records" in sync_stats:
            for record in sync_stats["records"]:
                if record.get("status") == "success":
                    success_count += 1
                elif record.get("status") == "error":
                    error_count += 1

        total_count = success_count + error_count

        return TraktSyncStatusResponse(
            is_running=is_running,
            last_sync_time=config.last_sync_time,
            next_sync_time=next_sync_time,
            success_count=success_count,
            error_count=error_count,
            total_count=total_count,
        )

    except Exception as e:
        logger.error(f"获取 Trakt 同步状态失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取同步状态失败: {str(e)}",
        )


@router.post("/sync/manual", response_model=TraktManualSyncResponse)
async def manual_trakt_sync(
    sync_request: TraktManualSyncRequest,
) -> TraktManualSyncResponse:
    """手动触发 Trakt 同步"""
    try:
        user_id = sync_request.user_id
        full_sync = sync_request.full_sync

        logger.info(f"手动触发 Trakt 同步: user_id={user_id}, full_sync={full_sync}")

        # 调用同步服务启动异步任务
        task_id = await trakt_sync_service.start_user_sync_task(
            user_id=user_id, full_sync=full_sync
        )

        return TraktManualSyncResponse(
            success=True,
            message="同步任务已提交",
            job_id=task_id,
        )

    except Exception as e:
        logger.error(f"手动触发 Trakt 同步失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"触发同步失败: {str(e)}",
        )


@router.delete("/disconnect")
async def disconnect_trakt() -> dict:
    """断开 Trakt 连接"""
    try:
        # TODO: 从会话或令牌中获取实际用户ID
        user_id = "default_user"

        success = trakt_auth_service.disconnect_trakt(user_id)

        if success:
            return {"success": True, "message": "Trakt 连接已断开"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="断开连接失败",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"断开 Trakt 连接失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"断开连接失败: {str(e)}",
        )
