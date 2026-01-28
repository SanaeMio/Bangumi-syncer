"""
安全认证模块
"""

import hashlib
import hmac
import secrets
import time
from typing import Any, Optional

from .config import config_manager
from .logging import logger


class SecurityManager:
    """安全管理器"""

    def __init__(self):
        self.active_sessions: dict[str, dict[str, Any]] = {}  # token -> session_info
        self.login_attempts: dict[
            str, dict[str, Any]
        ] = {}  # ip -> {attempts: int, locked_until: datetime}
        self._init_auth_config()

    def _init_auth_config(self) -> None:
        """初始化认证配置"""
        try:
            config = config_manager.get_config_parser()
            config_updated = False

            # 检查是否存在auth段，如果不存在则创建
            if not config.has_section("auth"):
                config.add_section("auth")
                logger.info("检测到老版本升级，正在初始化认证配置...")
                config_updated = True

            # 确保所有必要的认证配置项都存在
            auth_defaults = {
                "enabled": "True",
                "username": "admin",
                "password": "admin",
                "session_timeout": "3600",
                "secret_key": "",
                "https_only": "False",
                "max_login_attempts": "5",
                "lockout_duration": "900",
            }

            for key, default_value in auth_defaults.items():
                if not config.has_option("auth", key):
                    config.set("auth", key, default_value)
                    config_updated = True
                    logger.info(f"添加认证配置项: {key} = {default_value}")

            # 检查并生成secret_key
            current_secret_key = config.get("auth", "secret_key", fallback="")
            if not current_secret_key:
                new_secret_key = secrets.token_urlsafe(32)
                config.set("auth", "secret_key", new_secret_key)
                config_updated = True
                logger.info("生成新的认证密钥")

            # 检查密码是否需要加密
            current_password = config.get("auth", "password", fallback="admin")
            if current_password and len(current_password) < 64:
                secret_key = config.get("auth", "secret_key")
                hashed_password = self.hash_password(current_password, secret_key)
                config.set("auth", "password", hashed_password)
                config_updated = True
                logger.info("密码已自动加密")

            # 如果配置有更新，保存到文件
            if config_updated:
                config_manager._save_config(config)
                auth_config = self.get_auth_config()
                logger.info("==========================================")
                logger.info("认证配置已初始化完成！")
                logger.info("默认登录信息：")
                logger.info(f"  用户名: {auth_config['username']}")
                logger.info("  密码: admin")
                logger.info("请访问 Web 界面并立即修改默认密码！")
                logger.info("==========================================")

        except Exception as e:
            logger.error(f"初始化认证配置失败: {e}")

    def get_auth_config(self) -> dict[str, Any]:
        """获取认证配置"""
        return {
            "enabled": config_manager.get("auth", "enabled", fallback=True),
            "username": config_manager.get("auth", "username", fallback="admin"),
            "password": config_manager.get("auth", "password", fallback="admin"),
            "session_timeout": config_manager.get(
                "auth", "session_timeout", fallback=3600
            ),
            "secret_key": config_manager.get("auth", "secret_key", fallback=""),
            "https_only": config_manager.get("auth", "https_only", fallback=False),
            "max_login_attempts": config_manager.get(
                "auth", "max_login_attempts", fallback=5
            ),
            "lockout_duration": config_manager.get(
                "auth", "lockout_duration", fallback=900
            ),
        }

    def hash_password(self, password: str, secret_key: str) -> str:
        """使用HMAC-SHA256哈希密码"""
        return hmac.new(
            secret_key.encode(), password.encode(), hashlib.sha256
        ).hexdigest()

    def generate_session_token(self) -> str:
        """生成会话令牌"""
        return secrets.token_urlsafe(32)

    def create_session(self, username: str) -> str:
        """创建会话"""
        token = self.generate_session_token()
        auth_config = self.get_auth_config()
        session_info = {
            "username": username,
            "created_at": time.time(),
            "expires_at": time.time() + auth_config["session_timeout"],
            "last_activity": time.time(),
        }
        self.active_sessions[token] = session_info
        logger.info(f"用户 {username} 登录成功")
        return token

    def validate_session(self, token: str) -> Optional[dict[str, Any]]:
        """验证会话"""
        if not token or token not in self.active_sessions:
            return None

        session = self.active_sessions[token]
        current_time = time.time()

        # 检查会话是否过期
        if current_time > session["expires_at"]:
            del self.active_sessions[token]
            logger.debug(f"会话已过期: {token[:8]}...")
            return None

        # 更新最后活动时间
        session["last_activity"] = current_time
        return session

    def remove_session(self, token: str) -> None:
        """删除会话"""
        if token in self.active_sessions:
            username = self.active_sessions[token]["username"]
            del self.active_sessions[token]
            logger.info(f"用户 {username} 登出，删除会话: {token[:8]}...")

    def cleanup_expired_sessions(self) -> None:
        """清理过期会话"""
        current_time = time.time()
        expired_tokens = [
            token
            for token, session in self.active_sessions.items()
            if current_time > session["expires_at"]
        ]

        for token in expired_tokens:
            username = self.active_sessions[token]["username"]
            del self.active_sessions[token]
            logger.debug(f"清理过期会话: {username} - {token[:8]}...")

    def check_login_attempts(self, ip: str) -> bool:
        """检查IP是否被锁定"""
        self.get_auth_config()
        current_time = time.time()

        if ip in self.login_attempts:
            attempt_info = self.login_attempts[ip]

            # 检查是否还在锁定期内
            if (
                "locked_until" in attempt_info
                and current_time < attempt_info["locked_until"]
            ):
                return False

            # 如果锁定期已过，重置尝试次数
            if (
                "locked_until" in attempt_info
                and current_time >= attempt_info["locked_until"]
            ):
                self.login_attempts[ip] = {"attempts": 0}

        return True

    def record_login_failure(self, ip: str) -> None:
        """记录登录失败"""
        auth_config = self.get_auth_config()
        current_time = time.time()

        if ip not in self.login_attempts:
            self.login_attempts[ip] = {"attempts": 0}

        self.login_attempts[ip]["attempts"] += 1

        # 如果超过最大尝试次数，锁定IP
        if self.login_attempts[ip]["attempts"] >= auth_config["max_login_attempts"]:
            lockout_until = current_time + auth_config["lockout_duration"]
            self.login_attempts[ip]["locked_until"] = lockout_until
            lockout_time_str = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(lockout_until)
            )
            logger.warning(f"IP {ip} 因登录失败次数过多被锁定至 {lockout_time_str}")

            # 发送IP锁定通知
            from ..utils.notifier import send_notify

            send_notify(
                "ip_locked",
                ip=ip,
                locked_until=lockout_time_str,
                attempt_count=self.login_attempts[ip]["attempts"],
                max_attempts=auth_config["max_login_attempts"],
            )

    def reset_login_attempts(self, ip: str) -> None:
        """重置登录尝试次数"""
        if ip in self.login_attempts:
            del self.login_attempts[ip]

    def cleanup_expired_lockouts(self) -> None:
        """清理过期的IP锁定"""
        current_time = time.time()
        expired_ips = []

        for ip, attempt_info in self.login_attempts.items():
            if (
                "locked_until" in attempt_info
                and current_time >= attempt_info["locked_until"]
            ):
                expired_ips.append(ip)

        for ip in expired_ips:
            del self.login_attempts[ip]
            logger.debug(f"清理过期IP锁定: {ip}")

    def get_login_attempts(self, ip: str) -> dict[str, Any]:
        """获取登录尝试信息"""
        return self.login_attempts.get(ip, {"attempts": 0})

    def get_lockout_info(self, ip: str) -> dict[str, Any]:
        """获取锁定信息"""
        if ip in self.login_attempts and "locked_until" in self.login_attempts[ip]:
            return self.login_attempts[ip]
        return {}

    def is_ip_locked(self, ip: str) -> bool:
        """检查IP是否被锁定"""
        self.get_auth_config()
        current_time = time.time()

        if ip in self.login_attempts:
            attempt_info = self.login_attempts[ip]

            # 检查是否还在锁定期内
            if (
                "locked_until" in attempt_info
                and current_time < attempt_info["locked_until"]
            ):
                return True

        return False

    def verify_password(self, username: str, password: str) -> bool:
        """验证用户名和密码"""
        auth_config = self.get_auth_config()

        if username != auth_config["username"]:
            return False

        hashed_password = self.hash_password(password, auth_config["secret_key"])
        return hashed_password == auth_config["password"]

    def authenticate_user(self, username: str, password: str) -> bool:
        """验证用户凭据"""
        try:
            # 清理过期会话和锁定
            self.cleanup_expired_sessions()
            self.cleanup_expired_lockouts()

            # 验证用户名和密码
            return self.verify_password(username, password)

        except Exception as e:
            logger.error(f"用户认证失败: {e}")
            return False


# 全局安全实例
security_manager = SecurityManager()
