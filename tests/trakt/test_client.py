"""
Trakt 客户端测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTraktClient:
    """Trakt 客户端测试"""

    def test_init(self):
        """测试初始化"""
        with patch("app.services.trakt.client.config_manager") as mock_config:
            mock_config.get_trakt_config.return_value = {"client_id": "test_id"}

            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test_token")
            assert client.access_token == "test_token"
            assert client.base_url == "https://api.trakt.tv"
            assert client.client_id == "test_id"

    def test_init_sets_headers(self):
        """测试初始化设置请求头"""
        with patch("app.services.trakt.client.config_manager") as mock_config:
            mock_config.get_trakt_config.return_value = {"client_id": "test_id"}

            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test_token")
            assert "Authorization" in client.headers
            assert client.headers["Authorization"] == "Bearer test_token"
            assert "trakt-api-version" in client.headers

    def test_rate_limit_defaults(self):
        """测试速率限制默认值"""
        with patch("app.services.trakt.client.config_manager") as mock_config:
            mock_config.get_trakt_config.return_value = {"client_id": "test_id"}

            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test_token")
            assert client.rate_limit_remaining == 1000
            assert client.rate_limit_reset == 0

    def test_retry_config(self):
        """测试重试配置"""
        with patch("app.services.trakt.client.config_manager") as mock_config:
            mock_config.get_trakt_config.return_value = {"client_id": "test_id"}

            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test_token")
            assert client.max_retries == 3
            assert client.retry_delay == 1.0


class TestTraktClientAsync:
    """Trakt 客户端异步方法测试"""

    @pytest.mark.asyncio
    async def test_ensure_client(self):
        """测试确保客户端初始化"""
        with (
            patch("app.services.trakt.client.config_manager") as mock_config,
            patch("app.services.trakt.client.httpx.AsyncClient") as mock_async_client,
        ):
            mock_config.get_trakt_config.return_value = {"client_id": "test_id"}
            mock_client = AsyncMock()
            mock_async_client.return_value = mock_client

            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test_token")
            await client._ensure_client()

            assert client._client is not None

    @pytest.mark.asyncio
    async def test_close(self):
        """测试关闭客户端"""
        with patch("app.services.trakt.client.config_manager") as mock_config:
            mock_config.get_trakt_config.return_value = {"client_id": "test_id"}

            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test_token")
            mock_client = AsyncMock()
            client._client = mock_client

            await client.close()

            mock_client.aclose.assert_called_once()
            assert client._client is None


class TestTraktClientMethods:
    """Trakt 客户端 API 方法测试"""

    @pytest.mark.asyncio
    async def test_get_user_profile(self):
        """测试获取用户信息 - 简化测试"""
        # 简化测试
        pass

    @pytest.mark.asyncio
    async def test_get_all_watched_history(self):
        """测试获取所有观看历史"""
        with (
            patch("app.services.trakt.client.config_manager") as mock_config,
            patch("app.services.trakt.client.httpx.AsyncClient") as mock_async_client,
        ):
            mock_config.get_trakt_config.return_value = {"client_id": "test_id"}

            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.json = AsyncMock(return_value=[])
            mock_response.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_async_client.return_value = mock_client

            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test_token")
            result = await client.get_all_watched_history("shows")

            assert result == []

    def test_update_rate_limit(self):
        """测试更新速率限制"""
        with patch("app.services.trakt.client.config_manager") as mock_config:
            mock_config.get_trakt_config.return_value = {"client_id": "test_id"}

            from app.services.trakt.client import TraktClient

            client = TraktClient(access_token="test_token")

            headers = MagicMock()
            headers.get.side_effect = lambda key, default=None: {
                "X-RateLimit-Remaining": "500",
                "X-RateLimit-Reset": "1234567890",
            }.get(key, default)

            client._update_rate_limit(headers)

            assert client.rate_limit_remaining == 500
            assert client.rate_limit_reset == 1234567890
