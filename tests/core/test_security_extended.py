"""
Security 模块扩展测试
"""

import time
from unittest.mock import MagicMock, patch


class TestSecurityManager:
    """SecurityManager 类测试"""

    def test_security_manager_import(self):
        """测试导入 SecurityManager"""
        from app.core.security import SecurityManager

        assert SecurityManager is not None

    @patch("app.core.security.config_manager")
    def test_security_manager_init(self, mock_config):
        """测试 SecurityManager 初始化"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        assert sm is not None
        assert hasattr(sm, "active_sessions")
        assert hasattr(sm, "login_attempts")

    @patch("app.core.security.config_manager")
    def test_hash_password(self, mock_config):
        """测试 hash_password 方法"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        result = sm.hash_password("test_password", "secret_key")
        assert isinstance(result, str)
        assert len(result) > 0

    @patch("app.core.security.config_manager")
    def test_generate_session_token(self, mock_config):
        """测试 generate_session_token 方法"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        token1 = sm.generate_session_token()
        token2 = sm.generate_session_token()
        assert isinstance(token1, str)
        assert len(token1) > 0
        # 每次调用应该产生不同的 token
        assert token1 != token2

    @patch("app.core.security.config_manager")
    def test_create_session(self, mock_config):
        """测试 create_session 方法"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        token = sm.create_session("testuser")
        assert isinstance(token, str)
        assert len(token) > 0
        # 验证 session 已创建
        assert token in sm.active_sessions

    @patch("app.core.security.config_manager")
    def test_validate_session_invalid(self, mock_config):
        """测试 validate_session 方法 - 无效 session"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        session = sm.validate_session("invalid_token")
        assert session is None

    @patch("app.core.security.config_manager")
    def test_remove_session(self, mock_config):
        """测试 remove_session 方法"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        token = sm.create_session("testuser")
        assert token in sm.active_sessions
        sm.remove_session(token)
        assert token not in sm.active_sessions

    @patch("app.core.security.config_manager")
    def test_get_login_attempts(self, mock_config):
        """测试 get_login_attempts 方法"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        # 初始状态
        result = sm.get_login_attempts("127.0.0.1")
        assert isinstance(result, dict)
        assert "attempts" in result


class TestSecurityManagerExtended:
    """SecurityManager 扩展测试 - 覆盖更多方法"""

    @patch("app.core.security.config_manager")
    def test_validate_session_expired(self, mock_config):
        """测试验证过期会话"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        # 创建一个已过期的会话
        expired_token = "expired_token_123"
        sm.active_sessions[expired_token] = {
            "username": "testuser",
            "created_at": time.time() - 7200,
            "expires_at": time.time() - 3600,  # 1小时前过期
            "last_activity": time.time() - 3600,
        }

        result = sm.validate_session(expired_token)
        assert result is None
        # 验证会话已被清理
        assert expired_token not in sm.active_sessions

    @patch("app.core.security.config_manager")
    def test_validate_session_valid(self, mock_config):
        """测试验证有效会话"""
        mock_config.get_config_parser.return_value = MagicMock()
        mock_config.get.side_effect = lambda *args, **kwargs: {
            ("auth", "session_timeout"): 3600,
        }.get((args[0], args[1]), kwargs.get("fallback"))
        from app.core.security import SecurityManager

        sm = SecurityManager()

        # 创建一个有效会话
        valid_token = "valid_token_456"
        sm.active_sessions[valid_token] = {
            "username": "testuser",
            "created_at": time.time(),
            "expires_at": time.time() + 3600,  # 1小时后过期
            "last_activity": time.time(),
        }

        result = sm.validate_session(valid_token)
        assert result is not None
        assert result["username"] == "testuser"

    @patch("app.core.security.config_manager")
    def test_cleanup_expired_sessions(self, mock_config):
        """测试清理过期会话"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        # 添加一个已过期的会话
        expired_token = "expired_token_789"
        sm.active_sessions[expired_token] = {
            "username": "expired_user",
            "expires_at": time.time() - 100,
        }

        # 添加一个有效会话
        valid_token = "valid_token_abc"
        sm.active_sessions[valid_token] = {
            "username": "valid_user",
            "expires_at": time.time() + 3600,
        }

        sm.cleanup_expired_sessions()

        # 验证过期会话被清理
        assert expired_token not in sm.active_sessions
        # 验证有效会话保留
        assert valid_token in sm.active_sessions

    @patch("app.core.security.config_manager")
    def test_check_login_attempts_no_record(self, mock_config):
        """测试检查登录尝试 - 无记录"""
        mock_config.get.side_effect = lambda *args, **kwargs: {
            ("auth", "max_login_attempts"): 5,
            ("auth", "lockout_duration"): 900,
        }.get((args[0], args[1]), kwargs.get("fallback"))
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        result = sm.check_login_attempts("192.168.1.100")
        assert result is True

    @patch("app.core.security.config_manager")
    def test_check_login_attempts_locked(self, mock_config):
        """测试检查登录尝试 - 已锁定"""
        mock_config.get.side_effect = lambda *args, **kwargs: {
            ("auth", "max_login_attempts"): 5,
            ("auth", "lockout_duration"): 900,
        }.get((args[0], args[1]), kwargs.get("fallback"))
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        # 设置一个已锁定的IP
        sm.login_attempts["192.168.1.100"] = {
            "attempts": 5,
            "locked_until": time.time() + 3600,  # 1小时后解锁
        }

        result = sm.check_login_attempts("192.168.1.100")
        assert result is False

    @patch("app.core.security.config_manager")
    def test_check_login_attempts_lockout_expired(self, mock_config):
        """测试检查登录尝试 - 锁定已过期"""
        mock_config.get.side_effect = lambda *args, **kwargs: {
            ("auth", "max_login_attempts"): 5,
            ("auth", "lockout_duration"): 900,
        }.get((args[0], args[1]), kwargs.get("fallback"))
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        # 设置一个锁定已过期的IP
        sm.login_attempts["192.168.1.100"] = {
            "attempts": 5,
            "locked_until": time.time() - 100,  # 已过期
        }

        result = sm.check_login_attempts("192.168.1.100")
        # 应该返回True且重置了尝试次数
        assert result is True
        assert sm.login_attempts["192.168.1.100"]["attempts"] == 0

    @patch("app.utils.notifier.send_notify")
    @patch("app.core.security.config_manager")
    def test_record_login_failure_no_lockout(self, mock_config, mock_notify):
        """测试记录登录失败 - 未达到锁定阈值"""
        mock_config.get.side_effect = lambda *args, **kwargs: {
            ("auth", "max_login_attempts"): 5,
            ("auth", "lockout_duration"): 900,
        }.get((args[0], args[1]), kwargs.get("fallback"))
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        sm.record_login_failure("192.168.1.100")

        assert sm.login_attempts["192.168.1.100"]["attempts"] == 1
        assert "locked_until" not in sm.login_attempts["192.168.1.100"]
        mock_notify.assert_not_called()

    @patch("app.utils.notifier.send_notify")
    @patch("app.core.security.config_manager")
    def test_record_login_failure_triggers_lockout(self, mock_config, mock_notify):
        """测试记录登录失败 - 触发锁定"""
        mock_config.get.side_effect = lambda *args, **kwargs: {
            ("auth", "max_login_attempts"): 3,
            ("auth", "lockout_duration"): 900,
        }.get((args[0], args[1]), kwargs.get("fallback"))
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        # 触发锁定
        sm.record_login_failure("192.168.1.100")
        sm.record_login_failure("192.168.1.100")
        sm.record_login_failure("192.168.1.100")

        assert sm.login_attempts["192.168.1.100"]["attempts"] == 3
        assert "locked_until" in sm.login_attempts["192.168.1.100"]
        mock_notify.assert_called_once()

    @patch("app.core.security.config_manager")
    def test_reset_login_attempts(self, mock_config):
        """测试重置登录尝试"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        sm.login_attempts = {
            "192.168.1.100": {"attempts": 5, "locked_until": time.time() + 3600}
        }

        sm.reset_login_attempts("192.168.1.100")

        assert "192.168.1.100" not in sm.login_attempts

    @patch("app.core.security.config_manager")
    def test_cleanup_expired_lockouts(self, mock_config):
        """测试清理过期锁定"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        sm.login_attempts = {
            "192.168.1.100": {"attempts": 0, "locked_until": time.time() - 100},
            "192.168.1.101": {"attempts": 0, "locked_until": time.time() + 3600},
        }

        sm.cleanup_expired_lockouts()

        assert "192.168.1.100" not in sm.login_attempts
        assert "192.168.1.101" in sm.login_attempts

    @patch("app.core.security.config_manager")
    def test_get_lockout_info_no_lockout(self, mock_config):
        """测试获取锁定信息 - 未锁定"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        result = sm.get_lockout_info("192.168.1.100")
        assert result == {}

    @patch("app.core.security.config_manager")
    def test_get_lockout_info_locked(self, mock_config):
        """测试获取锁定信息 - 已锁定"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        sm.login_attempts = {
            "192.168.1.100": {"attempts": 5, "locked_until": time.time() + 3600}
        }

        result = sm.get_lockout_info("192.168.1.100")
        assert "locked_until" in result
        assert result["attempts"] == 5

    @patch("app.core.security.config_manager")
    def test_is_ip_locked_not_locked(self, mock_config):
        """测试IP锁定检查 - 未锁定"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        result = sm.is_ip_locked("192.168.1.100")
        assert result is False

    @patch("app.core.security.config_manager")
    def test_is_ip_locked_currently_locked(self, mock_config):
        """测试IP锁定检查 - 已锁定"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        sm.login_attempts = {
            "192.168.1.100": {"attempts": 5, "locked_until": time.time() + 3600}
        }

        result = sm.is_ip_locked("192.168.1.100")
        assert result is True

    @patch("app.core.security.config_manager")
    def test_is_ip_locked_expired(self, mock_config):
        """测试IP锁定检查 - 锁定已过期"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        sm.login_attempts = {
            "192.168.1.100": {"attempts": 5, "locked_until": time.time() - 100}
        }

        result = sm.is_ip_locked("192.168.1.100")
        assert result is False

    @patch("app.core.security.config_manager")
    def test_verify_password_correct(self, mock_config):
        """测试密码验证 - 正确"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        # 设置正确的密码哈希
        correct_password = "testpassword"
        secret_key = "secret_key_123"
        hashed = sm.hash_password(correct_password, secret_key)

        sm.get_auth_config = lambda: {
            "username": "admin",
            "password": hashed,
            "secret_key": secret_key,
        }

        result = sm.verify_password("admin", correct_password)
        assert result is True

    @patch("app.core.security.config_manager")
    def test_verify_password_incorrect(self, mock_config):
        """测试密码验证 - 错误"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        secret_key = "secret_key_123"
        hashed = sm.hash_password("correctpassword", secret_key)

        sm.get_auth_config = lambda: {
            "username": "admin",
            "password": hashed,
            "secret_key": secret_key,
        }

        result = sm.verify_password("admin", "wrongpassword")
        assert result is False

    @patch("app.core.security.config_manager")
    def test_verify_password_wrong_username(self, mock_config):
        """测试密码验证 - 用户名错误"""
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()

        sm.get_auth_config = lambda: {"username": "admin", "password": "hash"}

        result = sm.verify_password("wronguser", "anypassword")
        assert result is False

    @patch("app.core.security.SecurityManager.cleanup_expired_sessions")
    @patch("app.core.security.SecurityManager.cleanup_expired_lockouts")
    @patch("app.core.security.SecurityManager.verify_password")
    @patch("app.core.security.config_manager")
    def test_authenticate_user_success(
        self, mock_config, mock_verify, mock_cleanup_lockouts, mock_cleanup_sessions
    ):
        """测试用户认证 - 成功"""
        mock_verify.return_value = True
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        sm.get_auth_config = lambda: {"username": "admin", "password": "hash"}

        result = sm.authenticate_user("admin", "password123")

        assert result is True
        mock_cleanup_sessions.assert_called_once()
        mock_cleanup_lockouts.assert_called_once()

    @patch("app.core.security.SecurityManager.cleanup_expired_sessions")
    @patch("app.core.security.SecurityManager.cleanup_expired_lockouts")
    @patch("app.core.security.SecurityManager.verify_password")
    @patch("app.core.security.config_manager")
    def test_authenticate_user_failure(
        self, mock_config, mock_verify, mock_cleanup_lockouts, mock_cleanup_sessions
    ):
        """测试用户认证 - 失败"""
        mock_verify.return_value = False
        mock_config.get_config_parser.return_value = MagicMock()
        from app.core.security import SecurityManager

        sm = SecurityManager()
        sm.get_auth_config = lambda: {"username": "admin", "password": "hash"}

        result = sm.authenticate_user("admin", "wrongpassword")

        assert result is False
