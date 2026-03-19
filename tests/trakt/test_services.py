"""
Trakt服务测试
"""

import time
from unittest.mock import patch

import pytest

from app.services.trakt.auth import TraktAuthService


class TestTraktAuthService:
    """Trakt 认证服务测试"""

    def test_init(self):
        """测试 TraktAuthService 初始化"""
        service = TraktAuthService()
        assert service.base_url == "https://api.trakt.tv"
        assert service.auth_url == "https://trakt.tv/oauth/authorize"
        assert service.token_url == "https://api.trakt.tv/oauth/token"

    @patch("app.services.trakt.auth.config_manager")
    def test_get_config(self, mock_config):
        """测试获取配置"""
        mock_config.get_trakt_config.return_value = {
            "client_id": "test_id",
            "client_secret": "test_secret",
        }
        service = TraktAuthService()
        config = service._get_config()
        assert config["client_id"] == "test_id"

    @patch("app.services.trakt.auth.config_manager")
    def test_validate_config_valid(self, mock_config):
        """测试验证有效配置"""
        mock_config.get_trakt_config.return_value = {
            "client_id": "test_id",
            "client_secret": "test_secret",
            "redirect_uri": "http://localhost",
        }
        service = TraktAuthService()
        assert service._validate_config() is True

    @patch("app.services.trakt.auth.config_manager")
    def test_validate_config_missing_client_id(self, mock_config):
        """测试验证缺少 client_id"""
        mock_config.get_trakt_config.return_value = {
            "client_secret": "test_secret",
            "redirect_uri": "http://localhost",
        }
        service = TraktAuthService()
        assert service._validate_config() is False

    @patch("app.services.trakt.auth.config_manager")
    def test_validate_config_empty_client_id(self, mock_config):
        """测试验证空 client_id"""
        mock_config.get_trakt_config.return_value = {
            "client_id": "",
            "client_secret": "test_secret",
            "redirect_uri": "http://localhost",
        }
        service = TraktAuthService()
        assert service._validate_config() is False

    @patch("app.services.trakt.auth.config_manager")
    def test_validate_config_missing_secret(self, mock_config):
        """测试验证缺少 client_secret"""
        mock_config.get_trakt_config.return_value = {
            "client_id": "test_id",
            "redirect_uri": "http://localhost",
        }
        service = TraktAuthService()
        assert service._validate_config() is False

    @patch("app.services.trakt.auth.config_manager")
    @pytest.mark.asyncio
    async def test_init_oauth(self, mock_config):
        """测试初始化 OAuth"""
        mock_config.get_trakt_config.return_value = {
            "client_id": "test_id",
            "client_secret": "test_secret",
            "redirect_uri": "http://localhost/callback",
        }
        service = TraktAuthService()
        result = await service.init_oauth("test_user")
        assert result is not None

    @patch("app.services.trakt.auth.config_manager")
    @pytest.mark.asyncio
    async def test_init_oauth_invalid_config(self, mock_config):
        """测试无效配置初始化 OAuth"""
        mock_config.get_trakt_config.return_value = {}
        service = TraktAuthService()
        result = await service.init_oauth("test_user")
        assert result is None

    def test_save_oauth_state(self):
        """测试保存 OAuth 状态"""
        service = TraktAuthService()
        service._save_oauth_state("test_user", "test_state")
        assert "test_user:test_state" in service._oauth_states

    def test_extract_user_id_from_state(self):
        """测试从 state 提取用户 ID"""
        service = TraktAuthService()
        service._save_oauth_state("test_user", "test_state_123")
        user_id = service.extract_user_id_from_state("test_state_123")
        assert user_id == "test_user"

    def test_verify_oauth_state(self):
        """测试验证 OAuth 状态"""
        service = TraktAuthService()
        service._save_oauth_state("test_user", "test_state_456")
        result = service._verify_oauth_state("test_user", "test_state_456")
        assert result is True

    def test_verify_oauth_state_invalid(self):
        """测试验证无效 OAuth 状态"""
        service = TraktAuthService()
        result = service._verify_oauth_state("test_user", "invalid_state")
        assert result is False

    def test_calculate_expires_at(self):
        """测试计算过期时间"""
        service = TraktAuthService()
        expires_at = service._calculate_expires_at(3600)
        assert expires_at is not None
        assert expires_at > time.time()

    def test_calculate_expires_at_none(self):
        """测试无过期时间"""
        service = TraktAuthService()
        expires_at = service._calculate_expires_at(None)
        assert expires_at is None

    @patch("app.services.trakt.auth.database_manager")
    def test_get_user_trakt_config(self, mock_db):
        """测试获取用户 Trakt 配置"""
        mock_db.get_trakt_config.return_value = {
            "user_id": "test_user",
            "access_token": "test_token",
        }
        service = TraktAuthService()
        config = service.get_user_trakt_config("test_user")
        assert config is not None
