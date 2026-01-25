"""
Trakt OAuth2 授权流程测试
"""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.trakt import TraktCallbackRequest
from app.services.trakt.auth import TraktAuthService, config_manager


class TestTraktAuthService:
    """TraktAuthService 测试类"""

    @pytest.mark.asyncio
    async def test_init_oauth_url_generation(self, mock_config_manager):
        """测试 OAuth 授权 URL 正确生成"""
        # 准备
        user_id = "test_user"
        url_safe = "test_state_123"
        service = TraktAuthService()

        with patch("secrets.token_urlsafe", return_value=url_safe):
            # 执行
            auth_url = await service.init_oauth(user_id)

            # 验证
            assert auth_url is not None
            assert auth_url.auth_url.startswith(service.auth_url)
            assert "response_type=code" in auth_url.auth_url
            assert "client_id=test_client_id" in auth_url.auth_url
            assert "redirect_uri=" in auth_url.auth_url
            assert "state=test_state_123" in auth_url.auth_url

            # 验证 state 存储
            state_key = f"{user_id}:{url_safe}"
            assert state_key in service._oauth_states
            assert service._oauth_states[state_key]["user_id"] == user_id
            assert service._oauth_states[state_key]["created_at"] > 0

    @pytest.mark.asyncio
    async def test_init_oauth_invalid_user(self):
        """测试无效用户初始化 OAuth"""
        service = TraktAuthService()

        # 测试空用户ID
        result = await service.init_oauth("")
        assert result is None

        # Note: Testing with None would cause a type error due to the function signature
        # The function expects a string parameter, so passing None would violate the type hint

    @pytest.mark.asyncio
    async def test_handle_callback_success(self, mock_database_manager):
        """测试成功处理 OAuth 回调"""
        with patch.object(
            config_manager,
            "get_trakt_config",
            return_value={
                "client_id": "test_client_id",
                "client_secret": "test_client_secret",
                "redirect_uri": "https://example.com/callback",
            },
        ):
            # 准备
            service = TraktAuthService()
            user_id = "test_user"
            state = "test_state_123"
            code = "test_auth_code_456"

            # 存储 state
            service._oauth_states[state] = {
                "user_id": user_id,
                "created_at": int(time.time()),
            }

            # 模拟 Trakt API 响应
            mock_response = {
                "access_token": "new_access_token_789",
                "refresh_token": "new_refresh_token_101",
                "expires_in": 7200,
                "scope": "public",
                "token_type": "bearer",
            }

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_response_obj = Mock()
                mock_response_obj.status_code = 200
                mock_response_obj.json = Mock(return_value=mock_response)
                mock_client.post = AsyncMock(return_value=mock_response_obj)
                mock_client_class.return_value.__aenter__.return_value = mock_client

                # 执行 - 使用新的 API 签名
                callback_request = TraktCallbackRequest(code=code, state=state)
                result = await service.handle_callback(callback_request, user_id)

                # 验证
                assert result.success is True

                # 验证数据库保存
                saved_config = mock_database_manager.get_trakt_config(user_id)
                assert saved_config is not None
                assert saved_config["access_token"] == "new_access_token_789"
                assert saved_config["refresh_token"] == "new_refresh_token_101"
                assert saved_config["user_id"] == user_id
                assert saved_config["expires_at"] > int(time.time())

    @pytest.mark.asyncio
    async def test_handle_callback_invalid_state(self):
        """测试 state 参数验证失败"""
        service = TraktAuthService()

        # 测试无效 state
        with pytest.raises(ValueError, match="无效的 state 参数"):
            callback_request = TraktCallbackRequest(
                code="test_code", state="invalid_state"
            )
            await service.handle_callback(callback_request, "test_user")

        # 测试过期 state
        old_timestamp = int(time.time()) - 3600  # 1小时前
        service._oauth_states["expired_state"] = {
            "user_id": "test_user",
            "created_at": old_timestamp,
        }

        with pytest.raises(ValueError, match="state 已过期"):
            callback_request = TraktCallbackRequest(
                code="test_code", state="expired_state"
            )
            await service.handle_callback(callback_request, "test_user")

    @pytest.mark.asyncio
    async def test_handle_callback_api_error(self):
        """测试 Trakt API 返回错误"""
        service = TraktAuthService()
        state = "test_state_123"
        code = "test_code"

        # 存储 state
        service._oauth_states[state] = {
            "user_id": "test_user",
            "created_at": int(time.time()),
        }

        # 模拟 Trakt API 返回错误
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_obj = Mock()
            mock_response_obj.status_code = 400
            mock_response_obj.text = "Bad Request"
            mock_client.post = AsyncMock(return_value=mock_response_obj)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # 执行和验证
            callback_request = TraktCallbackRequest(code=code, state=state)
            result = await service.handle_callback(callback_request, "test_user")
            assert result.success is False

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, mock_database_manager, mock_time):
        """测试成功刷新过期 token"""
        # 准备过期的 token 配置
        expired_time = int(time.time()) - 3600  # 1小时前过期
        mock_database_manager.save_trakt_config(
            {
                "user_id": "test_user",
                "access_token": "expired_token",
                "refresh_token": "valid_refresh_token",
                "expires_at": expired_time,
                "enabled": True,
            }
        )

        service = TraktAuthService()

        # 模拟 Trakt API 响应
        mock_response = {
            "access_token": "new_access_token_123",
            "refresh_token": "new_refresh_token_456",
            "expires_in": 7200,
            "scope": "public",
            "token_type": "bearer",
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_obj = Mock()
            mock_response_obj.status_code = 200
            mock_response_obj.json = Mock(return_value=mock_response)
            mock_client.post = AsyncMock(return_value=mock_response_obj)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # 执行
            result = await service.refresh_token("test_user")

            # 验证
            assert result is True

            # 验证数据库更新
            updated_config = mock_database_manager.get_trakt_config("test_user")
            assert updated_config["access_token"] == "new_access_token_123"
            assert updated_config["refresh_token"] == "new_refresh_token_456"
            assert updated_config["expires_at"] > expired_time

    @pytest.mark.asyncio
    async def test_refresh_token_still_valid(self, mock_database_manager):
        """测试未过期 token 无需刷新"""
        # 准备未过期的 token 配置
        future_time = int(time.time()) + 3600  # 1小时后过期
        mock_database_manager.save_trakt_config(
            {
                "user_id": "test_user",
                "access_token": "valid_token",
                "refresh_token": "refresh_token",
                "expires_at": future_time,
                "enabled": True,
            }
        )

        service = TraktAuthService()

        # 模拟 Trakt API - 不应该被调用
        with patch("httpx.AsyncClient.post") as mock_post:
            # 执行
            result = await service.refresh_token("test_user")

            # 验证
            assert result is True
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_token_no_config(self):
        """测试用户无 Trakt 配置时刷新 token"""
        service = TraktAuthService()

        # 执行和验证
        result = await service.refresh_token("non_existent_user")
        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_token_api_error(self, mock_database_manager):
        """测试 Trakt API 刷新 token 时出错"""
        # 准备过期的 token 配置
        expired_time = int(time.time()) - 3600
        mock_database_manager.save_trakt_config(
            {
                "user_id": "test_user",
                "access_token": "expired_token",
                "refresh_token": "refresh_token",
                "expires_at": expired_time,
                "enabled": True,
            }
        )

        service = TraktAuthService()

        # 模拟 Trakt API 返回错误
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_obj = Mock()
            mock_response_obj.status_code = 400
            mock_response_obj.text = "Invalid refresh token"
            mock_client.post = AsyncMock(return_value=mock_response_obj)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # 执行和验证
            result = await service.refresh_token("test_user")
            assert result is False

    def test_is_token_expired(self, sample_trakt_config):
        """测试 token 过期检查"""

        # 测试未过期 token
        future_time = int(time.time()) + 3600
        sample_trakt_config.expires_at = future_time
        assert not sample_trakt_config.is_token_expired()

        # 测试已过期 token
        past_time = int(time.time()) - 3600
        sample_trakt_config.expires_at = past_time
        assert sample_trakt_config.is_token_expired()

        # 测试无过期时间
        sample_trakt_config.expires_at = None
        assert sample_trakt_config.is_token_expired()

    def test_get_user_trakt_config(self, mock_database_manager):
        """测试获取用户 Trakt 配置"""
        service = TraktAuthService()

        # 准备测试数据
        mock_database_manager.save_trakt_config(
            {
                "user_id": "test_user",
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
            }
        )

        # 执行
        config = service.get_user_trakt_config("test_user")

        # 验证
        assert config is not None
        assert config.user_id == "test_user"
        assert config.access_token == "test_token"

        # 测试不存在的用户
        config = service.get_user_trakt_config("non_existent_user")
        assert config is None

    @pytest.mark.asyncio
    async def test_cleanup_expired_states(self):
        """测试清理过期的 state"""
        service = TraktAuthService()

        # 添加一些 state
        current_time = int(time.time())
        service._oauth_states = {
            "recent_state": {
                "user_id": "user1",
                "created_at": current_time - 300,  # 5分钟前
            },
            "old_state": {
                "user_id": "user2",
                "created_at": current_time - 1800,  # 30分钟前
            },
            "very_old_state": {
                "user_id": "user3",
                "created_at": current_time - 7200,  # 2小时前
            },
        }

        # 执行清理（设置10分钟过期）
        service._cleanup_expired_states(max_age=600)

        # 验证
        assert "recent_state" in service._oauth_states  # 5分钟，保留
        assert "old_state" not in service._oauth_states  # 30分钟，清理
        assert "very_old_state" not in service._oauth_states  # 2小时，清理
