"""
Trakt.tv OAuth2 认证服务

授权 URL 构建、令牌交换/刷新、CSRF state 管理统一委托给通用
``app.services.oauth`` 抽象层；本模块仅保留 Trakt 特有的令牌落地
（写入 trakt 配置表）与配置校验逻辑。
"""

import asyncio
import time
from datetime import datetime
from typing import Optional

from app.services.oauth import get_oauth_service, get_provider

from ...core.config import config_manager
from ...core.database import database_manager
from ...core.logging import logger
from ...models.trakt import (
    TraktAuthResponse,
    TraktCallbackRequest,
    TraktCallbackResponse,
    TraktConfig,
)


def get_trakt_app_credentials() -> tuple[str, str]:
    """返回 (client_id, client_secret)，取自 INI ``[trakt]``。"""
    cfg = config_manager.get_trakt_config() or {}
    return (
        (cfg.get("client_id", "") or "").strip(),
        (cfg.get("client_secret", "") or "").strip(),
    )


def get_trakt_redirect_uri() -> str:
    """返回 Trakt OAuth 回跳地址，取自 INI ``[trakt] redirect_uri``。"""
    cfg = config_manager.get_trakt_config() or {}
    return (cfg.get("redirect_uri", "") or "").strip()


class TraktAuthService:
    """Trakt OAuth2 认证服务"""

    def __init__(self) -> None:
        self.base_url = "https://api.trakt.tv"
        self.auth_url = "https://trakt.tv/oauth/authorize"
        self.token_url = "https://api.trakt.tv/oauth/token"
        self.oauth = get_oauth_service()
        self.trakt_config = {}
        # per-user 刷新锁：避免并发刷新重复调用 OAuth 接口
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _get_refresh_lock(self, user_id: str) -> asyncio.Lock:
        """获取指定用户的刷新锁（per-user，避免不同用户互相阻塞）。"""
        async with self._locks_guard:
            lock = self._refresh_locks.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                self._refresh_locks[user_id] = lock
            return lock

    def _get_config(self) -> dict:
        """获取最新的 Trakt 配置"""
        return config_manager.get_trakt_config()

    def _validate_config(self) -> bool:
        """验证 Trakt 配置是否有效"""
        trakt_config = self._get_config()
        if not trakt_config:
            logger.error("Trakt 配置未找到")
            return False

        client_id = (trakt_config.get("client_id", "") or "").strip()
        client_secret = (trakt_config.get("client_secret", "") or "").strip()
        redirect_uri = (trakt_config.get("redirect_uri", "") or "").strip()

        if not client_id:
            logger.error("Trakt client_id 未配置")
            return False

        if not client_secret:
            logger.error("Trakt client_secret 未配置")
            return False

        if not redirect_uri:
            logger.error("Trakt redirect_uri 未配置")
            return False

        return True

    async def init_oauth(self, user_id: str) -> Optional[TraktAuthResponse]:
        """初始化 OAuth 授权流程，生成授权 URL"""
        if not user_id or not user_id.strip():
            logger.error("用户ID不能为空")
            return None

        if not self._validate_config():
            return None

        # 生成并保存 state（统一落库，带 TTL，防 CSRF）
        state = self.oauth.create_state("trakt", user_id)
        provider = get_provider("trakt")
        auth_url = self.oauth.build_authorize_url(provider, state=state)

        return TraktAuthResponse(auth_url=auth_url, state=state)

    async def handle_callback(
        self, callback_request: TraktCallbackRequest, user_id: str
    ) -> TraktCallbackResponse:
        """处理 OAuth 回调，使用授权码获取访问令牌。

        注意：state 校验由调用方（API 回调入口）通过 ``extract_user_id_from_state``
        完成消费，此处不再二次消费 state，避免重复 DELETE 导致校验失败。
        """
        try:
            if not self._validate_config():
                return TraktCallbackResponse(success=False, message="Trakt 配置无效")

            # 使用授权码交换访问令牌
            token_data = await self._exchange_code_for_token(callback_request.code)

            if not token_data:
                return TraktCallbackResponse(success=False, message="获取访问令牌失败")

            # 保存令牌到数据库
            trakt_config = TraktConfig(
                user_id=user_id,
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                expires_at=self._calculate_expires_at(token_data.get("expires_in")),
                enabled=True,
                sync_interval=self._get_config().get(
                    "default_sync_interval", "0 */6 * * *"
                ),
                last_sync_time=int(
                    time.time()
                ),  # 设置为授权成功时间，用于增量同步的起始点
            )

            success = database_manager.save_trakt_config(trakt_config.to_dict())

            if success:
                logger.info(f"用户 {user_id} 的 Trakt 令牌保存成功")
                return TraktCallbackResponse(success=True, message="Trakt 授权成功")
            else:
                logger.error(f"用户 {user_id} 的 Trakt 令牌保存失败")
                return TraktCallbackResponse(success=False, message="保存令牌失败")

        except Exception as e:
            logger.error(f"处理 Trakt 回调时发生错误: {e}")
            return TraktCallbackResponse(
                success=False, message=f"处理回调时发生错误: {str(e)}"
            )

    async def refresh_token(self, user_id: str) -> bool:
        """刷新过期的访问令牌

        使用 per-user asyncio.Lock 确保同一用户同一时刻只有一个刷新操作，
        避免并发（手动同步 + 调度器）重复调用 OAuth refresh 接口或后刷新的
        覆盖先刷新的导致 token 失效。持锁后 double-check 是否仍需刷新。
        """
        try:
            lock = await self._get_refresh_lock(user_id)
            async with lock:
                # 持锁后重新读取配置，可能已被并发协程刷新
                config_dict = database_manager.get_trakt_config(user_id)
                if not config_dict:
                    logger.error(f"用户 {user_id} 的 Trakt 配置未找到")
                    return False

                config = TraktConfig.from_dict(config_dict)
                if not config:
                    logger.error(f"用户 {user_id} 的 Trakt 配置无效")
                    return False

                # double-check：并发场景下可能已被其他协程刷新
                if not config.refresh_if_needed():
                    logger.info(f"用户 {user_id} 的令牌尚未过期，无需刷新")
                    return True

                if not config.refresh_token:
                    logger.error(f"用户 {user_id} 没有刷新令牌，需要重新授权")
                    return False

                # 使用刷新令牌获取新的访问令牌
                refresh_data = await self._refresh_access_token(config.refresh_token)

                if not refresh_data:
                    logger.error(f"用户 {user_id} 的令牌刷新失败")
                    return False

                # 更新配置
                config.access_token = refresh_data["access_token"]
                config.refresh_token = refresh_data.get(
                    "refresh_token", config.refresh_token
                )
                config.expires_at = self._calculate_expires_at(
                    refresh_data.get("expires_in")
                )
                config.updated_at = int(datetime.now().timestamp())

                # 保存到数据库
                success = database_manager.save_trakt_config(config.to_dict())

                if success:
                    logger.info(f"用户 {user_id} 的 Trakt 令牌刷新成功")
                    return True
                else:
                    logger.error(f"用户 {user_id} 的 Trakt 令牌保存失败")
                    return False

        except Exception as e:
            logger.error(f"刷新 Trakt 令牌时发生错误: {e}")
            return False

    async def _exchange_code_for_token(self, code: str) -> Optional[dict]:
        """使用授权码交换访问令牌（委托通用 OAuth 服务）。"""
        try:
            if not self._validate_config():
                return None
            return self.oauth.exchange_code("trakt", code)
        except Exception as e:
            logger.error(f"交换 Trakt 令牌时发生错误: {e}")
            return None

    async def _refresh_access_token(self, refresh_token: str) -> Optional[dict]:
        """使用刷新令牌获取新的访问令牌（委托通用 OAuth 服务）。"""
        try:
            if not self._validate_config():
                return None
            return self.oauth.refresh_token("trakt", refresh_token)
        except Exception as e:
            logger.error(f"刷新 Trakt 令牌时发生错误: {e}")
            return None

    def _calculate_expires_at(self, expires_in: Optional[int]) -> Optional[int]:
        """计算令牌过期时间戳"""
        if not expires_in:
            return None

        # expires_in 是秒数，减去 60 秒作为缓冲
        buffer_seconds = 60
        return int(datetime.now().timestamp()) + expires_in - buffer_seconds

    # ── CSRF state（统一落库，由通用 OAuth 服务管理）────────────
    def extract_user_id_from_state(self, state: str) -> Optional[str]:
        """从 state 中提取用户ID（校验并消费）。"""
        result = self.oauth.consume_state("trakt", state)
        return result["account_key"] if result else None

    def _cleanup_expired_states(self, max_age: int = 300) -> int:
        """清理过期的 state，返回删除行数。"""
        return database_manager.cleanup_oauth_states_expired()

    def get_user_trakt_config(self, user_id: str) -> Optional[TraktConfig]:
        """获取用户的 Trakt 配置"""
        config_dict = database_manager.get_trakt_config(user_id)
        if not config_dict:
            return None

        return TraktConfig.from_dict(config_dict)

    def save_config(self, config: TraktConfig) -> bool:
        """保存或更新 Trakt 配置（API 层入口，避免跨层直访数据库）"""
        return database_manager.save_trakt_config(config.to_dict())

    def disconnect_trakt(self, user_id: str) -> bool:
        """断开 Trakt 连接，删除配置"""
        success = database_manager.delete_trakt_config(user_id)

        if success:
            logger.info(f"用户 {user_id} 的 Trakt 配置已删除")
        else:
            logger.warning(f"用户 {user_id} 的 Trakt 配置删除失败（可能不存在）")

        return success


# 全局 Trakt 认证服务实例
trakt_auth_service = TraktAuthService()
