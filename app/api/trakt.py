"""
Trakt.tv API 路由
"""

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from ..api import deps
from ..core.config import config_manager
from ..core.logging import logger
from ..core.public_url import redirect_public
from ..models.trakt import (
    TOKEN_STATUS_ACTIVE,
    TOKEN_STATUS_EXPIRED,
    TOKEN_STATUS_NOT_CONFIGURED,
    TraktApiConfigUpdateRequest,
    TraktAuthRequest,
    TraktAuthResponse,
    TraktCallbackRequest,
    TraktConfigResponse,
    TraktConfigUpdateRequest,
    TraktEmailLoginCompleteRequest,
    TraktEmailLoginCompleteResponse,
    TraktEmailLoginStartRequest,
    TraktEmailLoginStartResponse,
    TraktManualSyncRequest,
    TraktManualSyncResponse,
    TraktSyncStatusResponse,
    normalize_auth_type,
)
from ..services.sync_service import sync_service
from ..services.trakt.auth import trakt_auth_service
from ..services.trakt.email_login import complete_email_login, start_email_login
from ..services.trakt.scheduler import trakt_scheduler
from ..services.trakt.sync_service import trakt_sync_service
from ..services.trakt.token_refresher import validate_and_save_bearer

router = APIRouter(prefix="/api/trakt", tags=["trakt"])


@router.post("/auth/init", response_model=TraktAuthResponse)
async def init_trakt_auth(
    request: TraktAuthRequest,
    current_user: dict = Depends(deps.get_current_user_flexible),
) -> TraktAuthResponse:
    """初始化 Trakt OAuth 授权"""
    try:
        # 这里应该从会话或令牌中获取实际用户ID
        # 暂时使用请求中的 user_id
        user_id = current_user.get("username", "default_user")

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
        if state is None:
            return redirect_public(
                "/trakt/auth?status=error&message=" + quote("缺少 state 参数", safe="")
            )

        user_id = trakt_auth_service.extract_user_id_from_state(state)
        if not user_id:
            # state 无效/过期/已消费：直接拒绝，避免用占位用户写库
            return redirect_public(
                "/trakt/auth?status=error&message="
                + quote("state 无效或已过期，请重新发起授权", safe="")
            )

        callback_request = TraktCallbackRequest(code=code, state=state or "")
        callback_response = await trakt_auth_service.handle_callback(
            callback_request, user_id
        )

        if callback_response.success:
            # 授权成功后自动将 user_id（应用登录用户名，作为 Trakt 同步隔离标识）
            # 追加到激活账号的 media_server_usernames，避免因漏填导致 Trakt 同步
            # 被用户名过滤拦截（DB 为唯一真相源）。这是媒体驱动正常行为，需明显提示。
            from ..core.accounts import (
                get_active_bangumi_account,
                save_bangumi_account,
            )

            acc = get_active_bangumi_account()
            auto_added_user = ""
            target_account = ""
            if acc:
                existing_names = list(acc.get("media_server_usernames") or [])
                if user_id not in existing_names:
                    existing_names.append(user_id)
                    acc["media_server_usernames"] = existing_names
                    save_bangumi_account(acc)
                    auto_added_user = user_id
                    target_account = acc.get("section_name", "")
                    logger.info(
                        f"Trakt 授权成功：已自动将用户名 '{user_id}' 追加到 "
                        f"激活 Bangumi 账号 '{target_account}' 的 media_server_usernames，"
                        f"确保该用户的 Trakt 同步不被过滤拦截。"
                    )
            # 成功页通过 query 参数展示自动追加提示（仅新增时带参）
            success_url = "/trakt/auth/success"
            if auto_added_user:
                success_url += (
                    "?auto_added="
                    + quote(auto_added_user, safe="")
                    + "&account="
                    + quote(target_account, safe="")
                )
            return redirect_public(success_url)

        return redirect_public(
            "/trakt/auth?status=error&message="
            + quote(str(callback_response.message), safe="")
        )

    except Exception as e:
        logger.error(f"处理 Trakt 回调失败: {e}")
        return redirect_public(
            "/trakt/auth?status=error&message=" + quote(str(e), safe="")
        )


@router.get("/config", response_model=TraktConfigResponse)
async def get_trakt_config(
    current_user: dict = Depends(deps.get_current_user_flexible),
) -> TraktConfigResponse:
    """获取当前用户的 Trakt 配置"""
    try:
        user_id = current_user.get("username", "default_user")

        config = trakt_auth_service.get_user_trakt_config(user_id)

        # 从配置文件获取 API 配置
        trakt_api_config = config_manager.get_trakt_config()

        if not config:
            return TraktConfigResponse(
                user_id=user_id,
                enabled=False,
                sync_interval="0 */6 * * *",
                sync_filter_enabled=True,
                last_sync_time=None,
                is_connected=False,
                token_expires_at=None,
                client_id=trakt_api_config.get("client_id", ""),
                client_secret_configured=bool(trakt_api_config.get("client_secret")),
                redirect_uri=trakt_api_config.get(
                    "redirect_uri", "http://localhost:8000/api/trakt/auth/callback"
                ),
                auth_type="oauth",
                token_configured=False,
                token_status=TOKEN_STATUS_NOT_CONFIGURED,
            )

        # 连接状态与凭证状态：两种模式均基于 access_token 与 expires_at 计算
        token_configured = bool(config.access_token)
        if token_configured and not config.is_token_expired():
            token_status = TOKEN_STATUS_ACTIVE
        elif token_configured:
            token_status = TOKEN_STATUS_EXPIRED
        else:
            token_status = TOKEN_STATUS_NOT_CONFIGURED
        is_connected = token_status == TOKEN_STATUS_ACTIVE

        return TraktConfigResponse(
            user_id=config.user_id,
            enabled=config.enabled,
            sync_interval=config.sync_interval,
            sync_filter_enabled=config.sync_filter_enabled,
            last_sync_time=config.last_sync_time,
            is_connected=is_connected,
            token_expires_at=config.expires_at,
            client_id=trakt_api_config.get("client_id", ""),
            client_secret_configured=bool(trakt_api_config.get("client_secret")),
            redirect_uri=trakt_api_config.get(
                "redirect_uri", "http://localhost:8000/api/trakt/auth/callback"
            ),
            auth_type=config.auth_type,
            token_configured=token_configured,
            token_status=token_status,
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
    current_user: dict = Depends(deps.get_current_user_flexible),
) -> TraktConfigResponse:
    """更新 Trakt 配置"""
    try:
        user_id = current_user.get("username", "default_user")

        config = trakt_auth_service.get_user_trakt_config(user_id)

        has_access = bool(
            update_request.access_token and update_request.access_token.strip()
        )
        has_refresh = bool(
            update_request.refresh_token and update_request.refresh_token.strip()
        )
        auth_type = update_request.auth_type
        target_mode = normalize_auth_type(auth_type) if auth_type is not None else None

        # ---- 凭证模式切换前置校验 ----
        # 必须在调用 validate_and_save_bearer 之前执行，避免「已把 Bearer 凭证
        # 落库却返回 400」的副作用。
        # 1) 提供 Bearer 凭证却要求其他模式：矛盾，直接拒绝
        if (
            (has_refresh or has_access)
            and target_mode is not None
            and target_mode != "bearer"
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "已提供 Bearer 凭证会保存为 Bearer 模式；如需使用 API 应用模式，"
                    "请点击「授权 Trakt」完成授权（无需填写 token）"
                ),
            )
        # 2) 切换模式但未提供对应凭证：拒绝（oauth/bearer 数据域不同，不能只改标记）
        if (
            target_mode is not None
            and config is not None
            and target_mode != config.auth_type
        ):
            if target_mode == "oauth":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "切换至 API 应用模式需重新授权，请点击「授权 Trakt」完成授权"
                    ),
                )
            # target_mode == bearer
            if not has_refresh:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "切换至 Bearer 模式需提供 refresh_token"
                        "（或使用「通过邮箱登录」）"
                    ),
                )

        # ---- Bearer 凭证 ----
        # 提供 refresh_token 则立即验证并刷新（旋转式），有效才落库。
        # 实际只使用并验证 refresh_token（刷新成功即证明凭证对有效，且会换新
        # access/refresh）；access_token 为可选展示字段，不被使用。
        if has_refresh:
            result = await validate_and_save_bearer(
                user_id, update_request.refresh_token.strip()
            )
            if not result["success"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=result["message"],
                )
            # 验证已落库（auth_type=bearer + 新 token），重新读取最新配置
            config = trakt_auth_service.get_user_trakt_config(user_id)
        elif has_access:
            # 只给了 access_token：无法校验/续期，明确提示需要 refresh_token
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bearer 凭证校验需要 refresh_token（access_token 可选）",
            )

        if not config:
            # 无既有配置且未提供 Bearer 凭证：视为未授权，保持 404
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trakt 配置未找到，请先完成授权或填写 Bearer 凭证",
            )

        # ---- 更新非凭证字段 ----
        enable = update_request.enabled
        sync_interval = update_request.sync_interval
        sync_filter_enabled = update_request.sync_filter_enabled
        # 只写 enabled/sync_interval/sync_filter_enabled/auth_type 这些列，
        # 不触碰 access_token/refresh_token/expires_at：避免用本请求早先读到的
        # 旧 token 覆盖并发刷新（心跳/定时同步/手动同步）旋转后的新 token。
        updates: dict = {}
        if enable is not None:
            config.enabled = enable
            updates["enabled"] = enable

        if sync_interval is not None:
            config.sync_interval = sync_interval
            updates["sync_interval"] = sync_interval

        if sync_filter_enabled is not None:
            config.sync_filter_enabled = sync_filter_enabled
            updates["sync_filter_enabled"] = sync_filter_enabled

        if auth_type is not None:
            config.auth_type = target_mode
            updates["auth_type"] = target_mode

        if updates:
            success = trakt_auth_service.update_config_fields(user_id, updates)
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="保存配置失败",
                )

        if enable is not None and sync_interval is not None:
            # sync_interval 可能被更新了, 需要先移除旧的作业再添加新的作业
            trakt_scheduler.remove_user_job(user_id)
            if enable:
                trakt_scheduler.add_user_job(user_id, sync_interval)

        # 返回更新后的配置（凭证状态与 GET 一致，token 不回显）
        token_configured = bool(config.access_token)
        if token_configured and not config.is_token_expired():
            token_status = TOKEN_STATUS_ACTIVE
        elif token_configured:
            token_status = TOKEN_STATUS_EXPIRED
        else:
            token_status = TOKEN_STATUS_NOT_CONFIGURED
        is_connected = token_status == TOKEN_STATUS_ACTIVE
        # 从配置文件获取 API 配置
        trakt_api_config = config_manager.get_trakt_config()

        return TraktConfigResponse(
            user_id=config.user_id,
            enabled=config.enabled,
            sync_interval=config.sync_interval,
            sync_filter_enabled=config.sync_filter_enabled,
            last_sync_time=config.last_sync_time,
            is_connected=is_connected,
            token_expires_at=config.expires_at,
            client_id=trakt_api_config.get("client_id", ""),
            client_secret_configured=bool(trakt_api_config.get("client_secret")),
            redirect_uri=trakt_api_config.get(
                "redirect_uri", "http://localhost:8000/api/trakt/auth/callback"
            ),
            auth_type=config.auth_type,
            token_configured=token_configured,
            token_status=token_status,
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
    _current_user: dict = Depends(deps.get_current_user_flexible),
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
            if str(update_request.client_secret).strip() == "":
                pass
            else:
                trakt_config["client_secret"] = update_request.client_secret
                config_manager.set(
                    "trakt", "client_secret", update_request.client_secret
                )

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


@router.post("/email-login/start", response_model=TraktEmailLoginStartResponse)
async def trakt_email_login_start(
    request: TraktEmailLoginStartRequest,
    current_user: dict = Depends(deps.get_current_user_flexible),
) -> TraktEmailLoginStartResponse:
    """邮箱登录：发送验证码到指定邮箱"""
    user_id = current_user.get("username", "default_user")
    result = await start_email_login(user_id, request.email)
    if result.get("rate_limited"):
        # 冷却限流：429 + retry_after，前端据此启动倒计时而非卡死按钮
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": result["message"],
                "retry_after": result.get("retry_after"),
            },
        )
    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"],
        )
    return TraktEmailLoginStartResponse(
        success=True,
        message=result["message"],
        retry_after=result.get("retry_after"),
    )


@router.post("/email-login/complete", response_model=TraktEmailLoginCompleteResponse)
async def trakt_email_login_complete(
    request: TraktEmailLoginCompleteRequest,
    current_user: dict = Depends(deps.get_current_user_flexible),
) -> TraktEmailLoginCompleteResponse:
    """邮箱登录：提交验证码，完成后自动获取并存储 Bearer 凭证"""
    user_id = current_user.get("username", "default_user")
    result = await complete_email_login(user_id, request.otp)
    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"],
        )
    return TraktEmailLoginCompleteResponse(
        success=True,
        message=result["message"],
        expires_at=result.get("expires_at"),
    )


@router.get("/sync/status", response_model=TraktSyncStatusResponse)
async def get_trakt_sync_status(
    current_user: dict = Depends(deps.get_current_user_flexible),
) -> TraktSyncStatusResponse:
    """获取 Trakt 同步状态"""
    try:
        user_id = current_user.get("username", "default_user")

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
        sync_stats = sync_service.get_sync_records(
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
    current_user: dict = Depends(deps.get_current_user_flexible),
) -> TraktManualSyncResponse:
    """手动触发 Trakt 同步"""
    try:
        user_id = current_user.get("username", "default_user")
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
async def disconnect_trakt(
    current_user: dict = Depends(deps.get_current_user_flexible),
) -> dict:
    """断开 Trakt 连接"""
    try:
        user_id = current_user.get("username", "default_user")
        success = trakt_auth_service.disconnect_trakt(user_id)

        if success:
            trakt_scheduler.remove_user_job(user_id)
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
