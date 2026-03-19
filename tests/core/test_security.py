"""
Security 模块测试 - 简化版
"""

from unittest.mock import patch

from app.core.security import SecurityManager


class TestSecurityManager:
    """安全管理器测试 - 简化版"""

    def test_generate_session_token(self):
        """测试生成会话 token"""
        with patch("app.core.security.config_manager"):
            manager = SecurityManager.__new__(SecurityManager)
            manager.active_sessions = {}
            manager.login_attempts = {}
            manager._auth_config = {"enabled": True}

            token = manager.generate_session_token()
            assert len(token) > 0

    def test_validate_session_invalid(self):
        """测试验证无效会话"""
        with patch("app.core.security.config_manager"):
            manager = SecurityManager.__new__(SecurityManager)
            manager.active_sessions = {}
            manager.login_attempts = {}
            manager._auth_config = {"enabled": True}

            result = manager.validate_session("invalid_token")
            assert result is None

    def test_hash_password(self):
        """测试密码哈希"""
        with patch("app.core.security.config_manager"):
            manager = SecurityManager.__new__(SecurityManager)
            manager.active_sessions = {}
            manager.login_attempts = {}
            manager._auth_config = {"enabled": True}

            hashed = manager.hash_password("testpass", "secret_key")
            assert len(hashed) > 0

    def test_remove_session(self):
        """测试删除会话"""
        with patch("app.core.security.config_manager"):
            manager = SecurityManager.__new__(SecurityManager)
            manager.active_sessions = {}
            manager.login_attempts = {}
            manager._auth_config = {"enabled": True}

            token = manager.generate_session_token()
            manager.active_sessions[token] = {"username": "test"}
            manager.remove_session(token)
            assert token not in manager.active_sessions
