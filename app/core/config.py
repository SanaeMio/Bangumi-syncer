"""
配置管理模块
"""

import os
import platform
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Optional


class ConfigManager:
    """配置管理器"""

    def __init__(self):
        self.platform = platform.system()
        self.cwd = Path(__file__).parent.parent.parent

        # 配置文件路径
        self.config_paths = self._get_config_paths()
        self.active_config_path = self._find_active_config()

        # 配置缓存
        self._config_cache: Optional[ConfigParser] = None
        self._last_modified = 0

        # 初始化配置
        self._load_config()

        # 检查并执行配置迁移
        if self._needs_migration():
            self._migrate_webhook_config()
            self._migrate_email_config()
            # 重新加载配置
            self._load_config()

        # 立即输出启动信息（在模块导入时）
        from .startup_info import startup_info

        startup_info.print_banner()
        startup_info.print_system_info(self.active_config_path)

    def _get_config_paths(self) -> dict[str, Path]:
        """获取可能的配置文件路径"""
        return {
            "env": os.environ.get("CONFIG_FILE"),
            "mounted": Path("/app/config/config.ini"),
            "dev": self.cwd / "config.dev.ini",
            "default": self.cwd / "config.ini",
        }

    def _find_active_config(self) -> Path:
        """查找活动的配置文件"""
        # 1. 环境变量指定的配置文件
        if self.config_paths["env"] and Path(self.config_paths["env"]).exists():
            return Path(self.config_paths["env"])

        # 2. Docker挂载的配置文件
        if self.config_paths["mounted"].exists():
            return self.config_paths["mounted"]

        # 3. 开发配置文件
        if self.config_paths["dev"].exists():
            return self.config_paths["dev"]

        # 4. 默认配置文件
        return self.config_paths["default"]

    def _load_config(self) -> None:
        """加载配置文件"""
        config = ConfigParser()

        # 读取配置文件
        config.read(self.active_config_path, encoding="utf-8-sig")

        # 应用环境变量覆盖
        self._apply_env_overrides(config)

        # 更新缓存
        self._config_cache = config
        self._last_modified = (
            self.active_config_path.stat().st_mtime
            if self.active_config_path.exists()
            else 0
        )

    def _apply_env_overrides(self, config: ConfigParser) -> None:
        """应用环境变量覆盖"""
        env_overrides = {
            ("bangumi", "username"): "BANGUMI_USERNAME",
            ("bangumi", "access_token"): "BANGUMI_ACCESS_TOKEN",
            ("sync", "single_username"): "SINGLE_USERNAME",
            ("bangumi", "private"): "BANGUMI_PRIVATE",
            ("dev", "script_proxy"): "HTTP_PROXY",
            ("dev", "debug"): "DEBUG_MODE",
        }

        for (section, option), env_var in env_overrides.items():
            env_value = os.environ.get(env_var)
            if env_value:
                if not config.has_section(section):
                    config.add_section(section)
                config.set(section, option, env_value)

    def _check_config_updated(self) -> bool:
        """检查配置文件是否已更新"""
        if not self.active_config_path.exists():
            return False

        current_mtime = self.active_config_path.stat().st_mtime
        if current_mtime > self._last_modified:
            return True

        return False

    def get_config_parser(self) -> ConfigParser:
        """获取配置对象"""
        if self._check_config_updated():
            self._load_config()

        return self._config_cache

    def reload_config(self) -> None:
        """重新加载配置"""
        self._load_config()

    def reload(self) -> None:
        """重新加载配置（别名）"""
        self.reload_config()

    def get_section(
        self, section: str, fallback: dict[str, Any] = None
    ) -> dict[str, Any]:
        """获取配置段"""
        config = self.get_config_parser()
        if not config.has_section(section):
            return fallback or {}

        result = {}
        for key, value in config.items(section):
            # 尝试转换为适当的数据类型
            if value.lower() in ("true", "false"):
                result[key] = value.lower() == "true"
            elif value.isdigit():
                result[key] = int(value)
            else:
                result[key] = value

        return result

    def get_config(self, section: str, key: str, fallback: Any = None) -> Any:
        """获取配置值"""
        config = self.get_config_parser()
        if not config.has_section(section):
            return fallback

        if not config.has_option(section, key):
            return fallback

        value = config.get(section, key)

        # 尝试转换为适当的数据类型
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        elif value.isdigit():
            return int(value)
        else:
            return value

    def get(self, section: str, key: str, fallback: Any = None) -> Any:
        """获取配置值（别名）"""
        return self.get_config(section, key, fallback)

    def set_config(self, section: str, key: str, value: Any) -> None:
        """设置配置值"""
        config = self.get_config_parser()
        if not config.has_section(section):
            config.add_section(section)

        config.set(section, key, str(value))
        self._save_config(config)

    def set(self, section: str, key: str, value: Any) -> None:
        """设置配置值（别名）"""
        self.set_config(section, key, value)

    def _save_config(self, config: ConfigParser) -> None:
        """保存配置文件"""
        with open(self.active_config_path, "w", encoding="utf-8") as f:
            config.write(f)

        # 更新缓存
        self._config_cache = config
        self._last_modified = self.active_config_path.stat().st_mtime

    def get_bangumi_configs(self) -> dict[str, dict[str, Any]]:
        """获取所有Bangumi配置"""
        config = self.get_config_parser()
        bangumi_configs = {}

        # 遍历所有配置段，查找多账号 bangumi-* 配置段（排除 bangumi-data 和 bangumi-mapping）
        for section_name in config.sections():
            if section_name.startswith("bangumi-") and section_name not in [
                "bangumi-data",
                "bangumi-mapping",
            ]:
                section_config = self.get_section(section_name)
                if section_config.get("username") and section_config.get(
                    "access_token"
                ):
                    bangumi_configs[section_name] = section_config

        return bangumi_configs

    def get_user_mappings(self) -> dict[str, str]:
        """获取用户映射配置"""
        bangumi_configs = self.get_bangumi_configs()
        user_mappings = {}

        for section_name, config in bangumi_configs.items():
            media_server_username = config.get("media_server_username", "")
            if media_server_username:
                user_mappings[media_server_username] = section_name

        return user_mappings

    def get_trakt_config(self) -> dict[str, Any]:
        """获取 Trakt 配置"""
        config = self.get_config_parser()

        # 检查 trakt 节是否存在
        if not config.has_section("trakt"):
            return {}

        trakt_config = self.get_section("trakt")

        # 确保配置有默认值
        default_config = {
            "client_id": "",
            "client_secret": "",
            "redirect_uri": "http://localhost:8000/api/trakt/auth/callback",
            "default_sync_interval": "0 */6 * * *",
            "default_enabled": True,
        }

        # 合并配置，确保所有键都存在
        for key, default_value in default_config.items():
            if key not in trakt_config:
                trakt_config[key] = default_value
            elif key == "default_enabled":
                # 转换布尔值
                value = trakt_config[key]
                if isinstance(value, str):
                    trakt_config[key] = value.lower() in (
                        "true",
                        "1",
                        "yes",
                        "on",
                        "enabled",
                    )
                else:
                    trakt_config[key] = bool(value)

        return trakt_config

    def get_scheduler_config(self) -> dict[str, Any]:
        """获取调度器配置"""
        config = self.get_config_parser()

        # 检查 scheduler 节是否存在
        if not config.has_section("scheduler"):
            return {}

        scheduler_config = self.get_section("scheduler")

        # 确保配置有默认值并转换类型
        default_config = {
            "startup_delay": 30,
            "max_concurrent_syncs": 3,
            "job_timeout": 300,
            "max_retries": 3,
            "retry_delay": 60,
        }

        # 合并配置并转换类型
        result_config = {}
        for key, default_value in default_config.items():
            value = scheduler_config.get(key)
            if value is None or value == "":
                result_config[key] = default_value
            else:
                # 转换为整数
                try:
                    result_config[key] = int(value)
                except (ValueError, TypeError):
                    result_config[key] = default_value

        return result_config

    def get_all_config(self) -> dict[str, dict[str, Any]]:
        """获取所有配置"""
        config = self.get_config_parser()
        result = {}

        # 收集多账号配置
        multi_accounts = {}

        for section_name in config.sections():
            if section_name.startswith("bangumi-") and section_name not in [
                "bangumi-data",
                "bangumi-mapping",
            ]:
                # 这是多账号配置段，收集到 multi_accounts 中
                section_config = self.get_section(section_name)
                # 使用 display_name 作为键，如果没有则使用配置段名
                account_key = section_config.get("display_name", section_name)
                multi_accounts[account_key] = section_config
            else:
                # 统一键名格式：将连字符转换为下划线
                normalized_key = section_name.replace("-", "_")
                result[normalized_key] = self.get_section(section_name)

        # 添加多账号配置到结果中
        if multi_accounts:
            result["multi_accounts"] = multi_accounts

        return result

    def save_config(self) -> None:
        """保存配置"""
        config = self.get_config_parser()
        self._save_config(config)

    def reload_multi_account_configs(self) -> None:
        """强制重新加载多账号配置"""
        from .logging import logger

        # 清除缓存
        self._config_cache = None
        self._last_modified = 0

        # 重新加载配置
        self._load_config()

        # 获取配置以触发日志输出
        bangumi_configs = self.get_bangumi_configs()
        user_mappings = self.get_user_mappings()

        logger.info("强制重新加载多账号配置")
        logger.info(f"加载了 {len(bangumi_configs)} 个bangumi账号配置")
        logger.info(f"加载了 {len(user_mappings)} 个用户映射配置")

    def _needs_migration(self) -> bool:
        """检查是否需要执行配置迁移"""
        config = self.get_config_parser()

        # 检查是否存在旧的webhook配置
        has_old_webhook = config.has_option("notification", "webhook_url")

        # 检查是否已经存在新的webhook配置段
        has_new_webhook = any(
            section.startswith("webhook-") for section in config.sections()
        )

        # 检查是否存在旧的邮件配置
        has_old_email = config.has_option("notification", "email_enabled")

        # 检查是否已经存在新的邮件配置段
        has_new_email = any(
            section.startswith("email-") for section in config.sections()
        )

        # 如果存在旧配置且不存在新配置，则需要迁移
        return (has_old_webhook and not has_new_webhook) or (
            has_old_email and not has_new_email
        )

    def _migrate_webhook_config(self) -> None:
        """将旧的webhook配置迁移到新的多webhook结构"""
        from .logging import logger

        config = self.get_config_parser()

        # 读取旧的webhook配置
        webhook_enabled = config.get(
            "notification", "webhook_enabled", fallback="False"
        )
        webhook_url = config.get("notification", "webhook_url", fallback="")
        webhook_method = config.get("notification", "webhook_method", fallback="POST")
        webhook_headers = config.get("notification", "webhook_headers", fallback="")
        webhook_template = config.get("notification", "webhook_template", fallback="")

        # 删除旧的webhook配置字段
        if config.has_option("notification", "webhook_enabled"):
            config.remove_option("notification", "webhook_enabled")
        if config.has_option("notification", "webhook_url"):
            config.remove_option("notification", "webhook_url")
        if config.has_option("notification", "webhook_method"):
            config.remove_option("notification", "webhook_method")
        if config.has_option("notification", "webhook_headers"):
            config.remove_option("notification", "webhook_headers")
        if config.has_option("notification", "webhook_template"):
            config.remove_option("notification", "webhook_template")

        if webhook_url and webhook_headers and webhook_template:
            # 创建新的webhook-1配置段
            if not config.has_section("webhook-1"):
                config.add_section("webhook-1")

            config.set("webhook-1", "id", "1")
            config.set("webhook-1", "enabled", webhook_enabled)
            config.set("webhook-1", "url", webhook_url)
            config.set("webhook-1", "method", webhook_method)
            config.set("webhook-1", "headers", webhook_headers)
            config.set("webhook-1", "template", webhook_template)

            # 迁移策略：只启用错误通知类型，保持原有行为
            config.set("webhook-1", "types", "mark_failed")

            # 保存配置
            self._save_config(config)

            logger.info(
                "配置迁移完成：旧webhook配置已迁移到webhook-1配置段（仅启用mark_failed类型）"
            )
        else:
            # 字段不完整，删除旧配置但不创建新配置
            self._save_config(config)
            logger.info("配置迁移：旧webhook配置字段不完整，已删除旧配置")

    def _migrate_email_config(self) -> None:
        """将旧的邮件配置迁移到新的多邮件结构"""
        from .logging import logger

        config = self.get_config_parser()

        # 读取旧的邮件配置
        email_enabled = config.get("notification", "email_enabled", fallback="False")
        smtp_server = config.get("notification", "smtp_server", fallback="")
        smtp_port = config.get("notification", "smtp_port", fallback="587")
        smtp_username = config.get("notification", "smtp_username", fallback="")
        smtp_password = config.get("notification", "smtp_password", fallback="")
        smtp_use_tls = config.get("notification", "smtp_use_tls", fallback="True")
        email_from = config.get("notification", "email_from", fallback="")
        email_to = config.get("notification", "email_to", fallback="")
        email_subject = config.get("notification", "email_subject", fallback="")
        email_template_file = config.get(
            "notification", "email_template_file", fallback=""
        )

        # 删除旧的邮件配置字段
        if config.has_option("notification", "email_enabled"):
            config.remove_option("notification", "email_enabled")
        if config.has_option("notification", "smtp_server"):
            config.remove_option("notification", "smtp_server")
        if config.has_option("notification", "smtp_port"):
            config.remove_option("notification", "smtp_port")
        if config.has_option("notification", "smtp_username"):
            config.remove_option("notification", "smtp_username")
        if config.has_option("notification", "smtp_password"):
            config.remove_option("notification", "smtp_password")
        if config.has_option("notification", "smtp_use_tls"):
            config.remove_option("notification", "smtp_use_tls")
        if config.has_option("notification", "email_from"):
            config.remove_option("notification", "email_from")
        if config.has_option("notification", "email_to"):
            config.remove_option("notification", "email_to")
        if config.has_option("notification", "email_subject"):
            config.remove_option("notification", "email_subject")
        if config.has_option("notification", "email_template_file"):
            config.remove_option("notification", "email_template_file")

        # 删除notification配置空间
        if config.has_section("notification"):
            config.remove_section("notification")

        if smtp_server and smtp_username and smtp_password and email_from:
            # 创建新的email-1配置段
            if not config.has_section("email-1"):
                config.add_section("email-1")

            config.set("email-1", "id", "1")
            config.set("email-1", "enabled", email_enabled)
            config.set("email-1", "smtp_server", smtp_server)
            config.set("email-1", "smtp_port", smtp_port)
            config.set("email-1", "smtp_username", smtp_username)
            config.set("email-1", "smtp_password", smtp_password)
            config.set("email-1", "smtp_use_tls", smtp_use_tls)
            config.set("email-1", "email_from", email_from)
            config.set("email-1", "email_to", email_to)
            config.set("email-1", "email_subject", email_subject)
            config.set("email-1", "email_template_file", email_template_file)

            # 迁移策略：只启用错误通知类型，保持原有行为
            config.set("email-1", "types", "mark_failed")

            # 保存配置
            self._save_config(config)

            logger.info(
                "配置迁移完成：旧邮件配置已迁移到email-1配置段（仅启用mark_failed类型）"
            )
        else:
            # 字段不完整，删除旧配置但不创建新配置
            self._save_config(config)
            logger.info("配置迁移：旧邮件配置字段不完整，已删除旧配置")


# 全局配置实例
config_manager = ConfigManager()
