"""
Security 模块测试 - 简化版
"""

import time
from unittest.mock import MagicMock, patch

from app.core.security import SecurityManager


def _make_manager():
    """创建一个跳过 _init_auth_config 的 SecurityManager"""
    with patch("app.core.security.config_manager"):
        manager = SecurityManager.__new__(SecurityManager)
        manager.active_sessions = {}
        manager.login_attempts = {}
        return manager


class TestSecurityManager:
    """安全管理器测试 - 简化版"""

    def test_generate_session_token(self):
        """测试生成会话 token"""
        manager = _make_manager()
        token = manager.generate_session_token()
        assert len(token) > 0

    def test_validate_session_invalid(self):
        """测试验证无效会话"""
        manager = _make_manager()
        result = manager.validate_session("invalid_token")
        assert result is None

    def test_hash_password(self):
        """测试密码哈希"""
        manager = _make_manager()
        hashed = manager.hash_password("testpass", "secret_key")
        assert len(hashed) > 0

    def test_remove_session(self):
        """测试删除会话"""
        manager = _make_manager()
        token = manager.generate_session_token()
        manager.active_sessions[token] = {"username": "test"}
        manager.remove_session(token)
        assert token not in manager.active_sessions

    def test_validate_session_expired(self):
        """测试过期会话返回None"""
        manager = _make_manager()
        token = "expired_token"
        manager.active_sessions[token] = {
            "username": "test",
            "expires_at": time.time() - 100,
        }
        result = manager.validate_session(token)
        assert result is None
        assert token not in manager.active_sessions

    def test_validate_session_valid(self):
        """测试有效会话返回session信息"""
        manager = _make_manager()
        token = "valid_token"
        manager.active_sessions[token] = {
            "username": "test",
            "expires_at": time.time() + 3600,
        }
        result = manager.validate_session(token)
        assert result is not None
        assert result["username"] == "test"

    def test_validate_session_empty_token(self):
        """测试空token返回None"""
        manager = _make_manager()
        assert manager.validate_session("") is None
        assert manager.validate_session(None) is None

    def test_remove_session_nonexistent(self):
        """测试删除不存在的会话不报错"""
        manager = _make_manager()
        manager.remove_session("nonexistent")
        # 不应抛出异常

    def test_cleanup_expired_sessions(self):
        """测试清理过期会话"""
        manager = _make_manager()
        manager.active_sessions["expired"] = {
            "username": "user1",
            "expires_at": time.time() - 100,
        }
        manager.active_sessions["valid"] = {
            "username": "user2",
            "expires_at": time.time() + 3600,
        }
        manager.cleanup_expired_sessions()
        assert "expired" not in manager.active_sessions
        assert "valid" in manager.active_sessions

    def test_cleanup_expired_sessions_none_expired(self):
        """测试没有过期会话时清理"""
        manager = _make_manager()
        manager.active_sessions["valid"] = {
            "username": "user",
            "expires_at": time.time() + 3600,
        }
        manager.cleanup_expired_sessions()
        assert "valid" in manager.active_sessions

    def test_check_login_attempts_not_locked(self):
        """测试未锁定的IP"""
        manager = _make_manager()
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.get.return_value = {"enabled": True}
            assert manager.check_login_attempts("192.168.1.1") is True

    def test_check_login_attempts_locked(self):
        """测试被锁定的IP"""
        manager = _make_manager()
        manager.login_attempts["192.168.1.1"] = {
            "attempts": 5,
            "locked_until": time.time() + 900,
        }
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.get.return_value = {"enabled": True}
            assert manager.check_login_attempts("192.168.1.1") is False

    def test_check_login_attempts_lock_expired(self):
        """测试锁定过期后重置"""
        manager = _make_manager()
        manager.login_attempts["192.168.1.1"] = {
            "attempts": 5,
            "locked_until": time.time() - 100,
        }
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.get.return_value = {"enabled": True}
            assert manager.check_login_attempts("192.168.1.1") is True
            assert manager.login_attempts["192.168.1.1"]["attempts"] == 0

    def test_record_login_failure(self):
        """测试记录登录失败"""
        manager = _make_manager()
        with patch.object(
            manager,
            "get_auth_config",
            return_value={
                "enabled": True,
                "max_login_attempts": 3,
                "lockout_duration": 900,
            },
        ):
            manager.record_login_failure("192.168.1.1")
            assert manager.login_attempts["192.168.1.1"]["attempts"] == 1

    def test_record_login_failure_triggers_lockout(self):
        """测试登录失败次数过多触发锁定"""
        manager = _make_manager()
        with (
            patch.object(
                manager,
                "get_auth_config",
                return_value={
                    "enabled": True,
                    "max_login_attempts": 2,
                    "lockout_duration": 900,
                },
            ),
            patch("app.utils.notifier.send_notify"),
        ):
            manager.login_attempts["192.168.1.1"] = {"attempts": 1}
            manager.record_login_failure("192.168.1.1")
            assert "locked_until" in manager.login_attempts["192.168.1.1"]

    def test_reset_login_attempts(self):
        """测试重置登录尝试次数"""
        manager = _make_manager()
        manager.login_attempts["192.168.1.1"] = {"attempts": 3}
        manager.reset_login_attempts("192.168.1.1")
        assert "192.168.1.1" not in manager.login_attempts

    def test_reset_login_attempts_nonexistent(self):
        """测试重置不存在的IP不报错"""
        manager = _make_manager()
        manager.reset_login_attempts("192.168.1.99")

    def test_cleanup_expired_lockouts(self):
        """测试清理过期的IP锁定"""
        manager = _make_manager()
        manager.login_attempts["192.168.1.1"] = {
            "attempts": 5,
            "locked_until": time.time() - 100,
        }
        manager.login_attempts["192.168.1.2"] = {
            "attempts": 5,
            "locked_until": time.time() + 900,
        }
        manager.cleanup_expired_lockouts()
        assert "192.168.1.1" not in manager.login_attempts
        assert "192.168.1.2" in manager.login_attempts

    def test_get_login_attempts(self):
        """测试获取登录尝试信息"""
        manager = _make_manager()
        manager.login_attempts["192.168.1.1"] = {"attempts": 3}
        result = manager.get_login_attempts("192.168.1.1")
        assert result["attempts"] == 3

    def test_get_login_attempts_default(self):
        """测试获取不存在IP的登录尝试信息"""
        manager = _make_manager()
        result = manager.get_login_attempts("192.168.1.99")
        assert result == {"attempts": 0}

    def test_get_lockout_info(self):
        """测试获取锁定信息"""
        manager = _make_manager()
        manager.login_attempts["192.168.1.1"] = {
            "attempts": 5,
            "locked_until": time.time() + 900,
        }
        result = manager.get_lockout_info("192.168.1.1")
        assert "locked_until" in result

    def test_get_lockout_info_empty(self):
        """测试获取未锁定IP的锁定信息"""
        manager = _make_manager()
        result = manager.get_lockout_info("192.168.1.99")
        assert result == {}

    def test_is_ip_locked_true(self):
        """测试IP被锁定"""
        manager = _make_manager()
        manager.login_attempts["192.168.1.1"] = {
            "attempts": 5,
            "locked_until": time.time() + 900,
        }
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.get.return_value = {"enabled": True}
            assert manager.is_ip_locked("192.168.1.1") is True

    def test_is_ip_locked_false(self):
        """测试IP未锁定"""
        manager = _make_manager()
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.get.return_value = {"enabled": True}
            assert manager.is_ip_locked("192.168.1.1") is False

    def test_is_ip_locked_expired(self):
        """测试IP锁定已过期"""
        manager = _make_manager()
        manager.login_attempts["192.168.1.1"] = {
            "attempts": 5,
            "locked_until": time.time() - 100,
        }
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.get.return_value = {"enabled": True}
            assert manager.is_ip_locked("192.168.1.1") is False

    def test_verify_password_correct(self):
        """测试密码验证成功"""
        manager = _make_manager()
        secret = "test_secret"
        hashed = manager.hash_password("admin", secret)
        with patch.object(
            manager,
            "get_auth_config",
            return_value={
                "username": "admin",
                "password": hashed,
                "secret_key": secret,
            },
        ):
            assert manager.verify_password("admin", "admin") is True

    def test_verify_password_wrong(self):
        """测试密码验证失败"""
        manager = _make_manager()
        secret = "test_secret"
        hashed = manager.hash_password("admin", secret)
        with patch.object(
            manager,
            "get_auth_config",
            return_value={
                "username": "admin",
                "password": hashed,
                "secret_key": secret,
            },
        ):
            assert manager.verify_password("admin", "wrong") is False

    def test_verify_password_wrong_username(self):
        """测试用户名验证失败"""
        manager = _make_manager()
        with patch.object(
            manager,
            "get_auth_config",
            return_value={
                "username": "admin",
                "password": "hash",
                "secret_key": "key",
            },
        ):
            assert manager.verify_password("wrong_user", "admin") is False

    def test_authenticate_user_success(self):
        """测试用户认证成功"""
        manager = _make_manager()
        secret = "test_secret"
        hashed = manager.hash_password("pass", secret)
        with patch.object(
            manager,
            "get_auth_config",
            return_value={
                "username": "user",
                "password": hashed,
                "secret_key": secret,
            },
        ):
            assert manager.authenticate_user("user", "pass") is True

    def test_authenticate_user_failure(self):
        """测试用户认证失败"""
        manager = _make_manager()
        with patch.object(
            manager,
            "get_auth_config",
            return_value={
                "username": "user",
                "password": "hash",
                "secret_key": "key",
            },
        ):
            assert manager.authenticate_user("user", "wrong") is False

    def test_verify_webhook_key_disabled(self):
        """测试webhook认证禁用时直接返回True"""
        manager = _make_manager()
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.get.return_value = False
            assert manager.verify_webhook_key("any_key") is True

    def test_verify_webhook_key_correct(self):
        """测试webhook key正确"""
        manager = _make_manager()
        with patch("app.core.security.config_manager") as mock_cm:
            # verify_webhook_key 调用两次 config_manager.get:
            # 1. ("auth", "webhook_auth_enabled", fallback=False) -> True
            # 2. ("auth", "webhook_key", "") -> "correct_key"
            mock_cm.get.side_effect = [True, "correct_key"]
            assert manager.verify_webhook_key("correct_key") is True

    def test_verify_webhook_key_wrong(self):
        """测试webhook key错误"""
        manager = _make_manager()
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.get.side_effect = [True, "correct_key"]
            assert manager.verify_webhook_key("wrong_key") is False

    def test_refresh_webhook_key_success(self):
        """测试刷新webhook key成功"""
        manager = _make_manager()
        with patch("app.core.security.config_manager") as mock_cm:
            result = manager.refresh_webhook_key()
            assert len(result) > 0
            mock_cm.set_config.assert_called_once()
            mock_cm.save_config.assert_called_once()

    def test_refresh_webhook_key_failure(self):
        """测试刷新webhook key失败"""
        manager = _make_manager()
        with patch("app.core.security.config_manager") as mock_cm:
            mock_cm.set_config.side_effect = Exception("save error")
            import pytest

            with pytest.raises(Exception, match="save error"):
                manager.refresh_webhook_key()

    def test_init_auth_config_no_section(self):
        """测试初始化时无auth段"""
        mock_config = MagicMock()
        mock_config.has_section.return_value = False
        mock_config.has_option.return_value = False
        mock_config.get.return_value = ""

        with (
            patch("app.core.security.config_manager") as mock_cm,
            patch(
                "app.core.security.encrypt_if_sensitive", side_effect=lambda *a: a[2]
            ),
            patch(
                "app.core.security.migrate_plaintext_sensitive_fields",
                return_value=False,
            ),
        ):
            mock_cm.get_config_parser.return_value = mock_config
            manager = SecurityManager.__new__(SecurityManager)
            manager.active_sessions = {}
            manager.login_attempts = {}
            manager._init_auth_config()
            mock_config.add_section.assert_called_with("auth")

    def test_init_auth_config_all_present(self):
        """测试初始化时所有配置已存在"""
        mock_config = MagicMock()
        mock_config.has_section.return_value = True
        mock_config.has_option.return_value = True
        mock_config.get.side_effect = lambda section, key, fallback=None: {
            "secret_key": "existing_secret",
            "webhook_key": "existing_webhook",
            "password": "a" * 64,  # 已加密的密码
        }.get(key, fallback or "value")

        with (
            patch("app.core.security.config_manager") as mock_cm,
            patch(
                "app.core.security.migrate_plaintext_sensitive_fields",
                return_value=False,
            ),
        ):
            mock_cm.get_config_parser.return_value = mock_config
            manager = SecurityManager.__new__(SecurityManager)
            manager.active_sessions = {}
            manager.login_attempts = {}
            manager._init_auth_config()
            # 不应调用 _save_config 因为没有更新
            mock_cm._save_config.assert_not_called()

    def test_create_session(self):
        """测试创建会话"""
        manager = _make_manager()
        with patch.object(
            manager,
            "get_auth_config",
            return_value={
                "session_timeout": 3600,
            },
        ):
            token = manager.create_session("testuser")
            assert len(token) > 0
            assert token in manager.active_sessions
            assert manager.active_sessions[token]["username"] == "testuser"
