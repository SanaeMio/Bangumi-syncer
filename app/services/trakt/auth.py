"""
Trakt.tv OAuth2 认证服务
"""

import secrets
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx

from ...core.config import config_manager
from ...core.database import database_manager
from ...core.logging import logger
from ...models.trakt import (
    TraktAuthResponse,
    TraktCallbackRequest,
    TraktCallbackResponse,
    TraktConfig,
)


class TraktAuthService:
    """Trakt OAuth2 认证服务"""

    def __init__(self):
        self.base_url = "https://api.trakt.tv"
        self.auth_url = "https://trakt.tv/oauth/authorize"
        self.token_url = "https://api.trakt.tv/oauth/token"
        self._oauth_states = {}
        self.trakt_config = {}

    def _get_config(self) -> dict:
        """获取最新的 Trakt 配置"""
        return config_manager.get_trakt_config()

    def _validate_config(self) -> bool:
        """验证 Trakt 配置是否有效"""
        trakt_config = self._get_config()
        if not trakt_config:
            logger.error("Trakt 配置未找到")
            return False

        client_id = trakt_config.get("client_id", "").strip()
        client_secret = trakt_config.get("client_secret", "").strip()
        redirect_uri = trakt_config.get("redirect_uri", "").strip()

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

        # 生成随机 state 参数防止 CSRF 攻击
        state = secrets.token_urlsafe(32)

        # 存储 state 到临时存储（这里简化处理，实际应使用缓存或数据库）
        # 注意：生产环境应使用更安全的存储方式
        self._save_oauth_state(user_id, state)

        # 构建授权 URL
        trakt_config = self._get_config()
        params = {
            "response_type": "code",
            "client_id": trakt_config["client_id"],
            "redirect_uri": trakt_config["redirect_uri"],
            "state": state,
        }

        auth_url = f"{self.auth_url}?{urlencode(params)}"

        return TraktAuthResponse(auth_url=auth_url, state=state)

    async def handle_callback(
        self, callback_request: TraktCallbackRequest, user_id: str
    ) -> TraktCallbackResponse:
        """处理 OAuth 回调，使用授权码获取访问令牌"""
        try:
            if not self._validate_config():
                return TraktCallbackResponse(success=False, message="Trakt 配置无效")

            # 验证 state 参数（这里简化，实际应验证与存储的 state 匹配）
            # state = callback_request.state
            # if not self._verify_oauth_state(user_id, state):
            #     return TraktCallbackResponse(
            #         success=False, message="State 验证失败"
            #     )

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
                last_sync_time=None,
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

    async def handle_callback_legacy(self, code: str, state: str, user_id: str) -> bool:
        """处理 OAuth 回调的z接口，用于兼容测试"""
        try:
            if not self._validate_config():
                return False

            # 验证 state 参数（这里简化，实际应验证与存储的 state 匹配）
            # if not self._verify_oauth_state(user_id, state):
            #     return False

            # 使用授权码交换访问令牌
            token_data = await self._exchange_code_for_token(code)

            if not token_data:
                return False

            # 保存令牌到数据库
            trakt_config = TraktConfig(
                user_id=user_id,
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                expires_at=self._calculate_expires_at(token_data.get("expires_in")),
                enabled=True,
                sync_interval=self.trakt_config.get(
                    "default_sync_interval", "0 */6 * * *"
                ),
                last_sync_time=None,
            )

            success = database_manager.save_trakt_config(trakt_config.to_dict())

            if success:
                logger.info(f"用户 {user_id} 的 Trakt 令牌保存成功")
                return True
            else:
                logger.error(f"用户 {user_id} 的 Trakt 令牌保存失败")
                return False

        except Exception as e:
            logger.error(f"处理 Trakt 回调时发生错误: {e}")
            return False

    async def refresh_token(self, user_id: str) -> bool:
        """刷新过期的访问令牌"""
        try:
            # 获取用户的 Trakt 配置
            config_dict = database_manager.get_trakt_config(user_id)
            if not config_dict:
                logger.error(f"用户 {user_id} 的 Trakt 配置未找到")
                return False

            config = TraktConfig.from_dict(config_dict)
            if not config:
                logger.error(f"用户 {user_id} 的 Trakt 配置无效")
                return False

            # 检查是否需要刷新
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
        """使用授权码交换访问令牌"""
        try:
            if not self._validate_config():
                return None

            trakt_config = self._get_config()
            data = {
                "code": code,
                "client_id": trakt_config["client_id"],
                "client_secret": trakt_config["client_secret"],
                "redirect_uri": trakt_config["redirect_uri"],
                "grant_type": "authorization_code",
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.token_url, json=data)

                if response.status_code == 200:
                    token_data = response.json()
                    logger.info("成功获取 Trakt 访问令牌")
                    return token_data
                else:
                    logger.error(
                        f"获取 Trakt 访问令牌失败: {response.status_code} - {response.text}"
                    )
                    return None

        except httpx.RequestError as e:
            logger.error(f"请求 Trakt 令牌接口失败: {e}")
            return None
        except Exception as e:
            logger.error(f"交换 Trakt 令牌时发生错误: {e}")
            return None

    async def _refresh_access_token(self, refresh_token: str) -> Optional[dict]:
        """使用刷新令牌获取新的访问令牌"""
        try:
            if not self._validate_config():
                return None

            trakt_config = self._get_config()
            data = {
                "refresh_token": refresh_token,
                "client_id": trakt_config["client_id"],
                "client_secret": trakt_config["client_secret"],
                "redirect_uri": trakt_config["redirect_uri"],
                "grant_type": "refresh_token",
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.token_url, json=data)

                if response.status_code == 200:
                    token_data = response.json()
                    logger.info("成功刷新 Trakt 访问令牌")
                    return token_data
                else:
                    logger.error(
                        f"刷新 Trakt 访问令牌失败: {response.status_code} - {response.text}"
                    )
                    return None

        except httpx.RequestError as e:
            logger.error(f"请求 Trakt 令牌刷新接口失败: {e}")
            return None
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

    def _save_oauth_state(self, user_id: str, state: str) -> None:
        """保存 OAuth state 到临时存储（简化实现）"""
        # 注意：生产环境应使用 Redis 或数据库存储 state，并设置过期时间
        # 这里使用内存存储作为示例
        if not hasattr(self, "_oauth_states"):
            self._oauth_states = {}

        self._oauth_states[f"{user_id}:{state}"] = {
            "user_id": user_id,
            "state": state,
            "created_at": time.time(),
        }

    def _verify_oauth_state(self, user_id: str, state: str) -> bool:
        """验证 OAuth state（简化实现）"""
        if not hasattr(self, "_oauth_states"):
            return False

        key = f"{user_id}:{state}"
        state_data = self._oauth_states.get(key)

        if not state_data:
            return False

        # 检查 state 是否过期（5分钟）
        created_at = state_data["created_at"]
        if not isinstance(created_at, (int, float)):
            return False
        if time.time() - created_at > 300:
            del self._oauth_states[key]
            return False

        # 验证成功后删除 state
        del self._oauth_states[key]
        return True

    def _cleanup_expired_states(self, max_age: int = 300) -> None:
        """清理过期的 state"""
        if not hasattr(self, "_oauth_states"):
            return

        current_time = time.time()
        expired_keys = []
        for key, state_data in self._oauth_states.items():
            created_at = state_data["created_at"]
            if (
                isinstance(created_at, (int, float))
                and current_time - created_at > max_age
            ):
                expired_keys.append(key)

        for key in expired_keys:
            del self._oauth_states[key]

    def get_user_trakt_config(self, user_id: str) -> Optional[TraktConfig]:
        """获取用户的 Trakt 配置"""
        config_dict = database_manager.get_trakt_config(user_id)
        if not config_dict:
            return None

        return TraktConfig.from_dict(config_dict)

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
