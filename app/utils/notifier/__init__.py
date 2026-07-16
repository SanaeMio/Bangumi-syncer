"""
通知模块 - 支持 Webhook 和邮件通知
"""

from __future__ import annotations

import time
from typing import Any

from ...core.config import config_manager
from ...core.logging import logger
from .email_sender import EmailSenderMixin
from .html_builders import EmailHtmlMixin
from .selftest import TestHelpersMixin
from .webhook import WebhookMixin


class Notifier(EmailHtmlMixin, WebhookMixin, EmailSenderMixin, TestHelpersMixin):
    """通知管理器（组合各 mixin，提供通知发送能力）"""

    def __init__(self, config_manager: Any) -> None:
        self.config_manager = config_manager
        self._last_notification_time = {}
        self._notification_cooldown = 60  # 同一类型通知的冷却时间（秒）

    def _should_send_notification(self, notification_type: str) -> bool:
        """检查是否应该发送通知（防止通知轰炸）"""
        current_time = time.time()
        last_time = self._last_notification_time.get(notification_type, 0)

        if current_time - last_time < self._notification_cooldown:
            logger.debug(f"通知冷却中，跳过 {notification_type} 类型通知")
            return False

        self._last_notification_time[notification_type] = current_time
        return True

    def _get_webhook_configs(self) -> list:
        """获取所有webhook配置"""
        config = self.config_manager.get_config_parser()
        webhook_configs = []

        for section_name in config.sections():
            if section_name.startswith("webhook-"):
                section_config = self.config_manager.get_section(section_name)
                if section_config.get("url"):  # 必须有URL才有效
                    webhook_configs.append(section_config)

        return webhook_configs

    def _get_email_configs(self) -> list:
        """获取所有邮件配置"""
        config = self.config_manager.get_config_parser()
        email_configs = []

        for section_name in config.sections():
            if section_name.startswith("email-"):
                section_config = self.config_manager.get_section(section_name)
                if section_config.get("smtp_server"):  # 必须有SMTP服务器才有效
                    email_configs.append(section_config)

        return email_configs

    def send_notification_by_type(
        self, notification_type: str, data: dict[str, Any]
    ) -> None:
        """
        根据通知类型发送通知

        Args:
            notification_type: 通知类型（request_received, bangumi_id_found, mark_success, mark_failed, mark_skipped）
            data: 通知数据（包含timestamp, user_name, title, season, episode, source等）
        """
        # 获取所有启用的webhook配置
        webhook_configs = self._get_webhook_configs()

        for webhook_config in webhook_configs:
            # 检查是否启用
            if not webhook_config.get("enabled", False):
                continue

            # 检查是否支持此通知类型
            types = webhook_config.get("types", "")
            if types != "all" and notification_type not in types:
                continue

            # 检查冷却时间（pending_candidate 按 item 维度冷却，避免不同番剧互相静默）
            cooldown_key = f"{webhook_config['id']}_{notification_type}"
            if notification_type == "pending_candidate":
                cooldown_key = (
                    f"{cooldown_key}_{data.get('title', '')}_{data.get('season', 0)}"
                )
            if not self._should_send_notification(cooldown_key):
                continue

            # 发送webhook通知
            self._send_webhook_by_config(webhook_config, notification_type, data)

        # 获取所有启用的邮件配置
        email_configs = self._get_email_configs()

        for email_config in email_configs:
            # 检查是否启用
            if not email_config.get("enabled", False):
                continue

            # 检查是否支持此通知类型
            types = email_config.get("types", "")
            if types != "all" and notification_type not in types:
                continue

            # 检查冷却时间（pending_candidate 按 item 维度冷却，避免不同番剧互相静默）
            cooldown_key = f"email_{email_config['id']}_{notification_type}"
            if notification_type == "pending_candidate":
                cooldown_key = (
                    f"{cooldown_key}_{data.get('title', '')}_{data.get('season', 0)}"
                )
            if not self._should_send_notification(cooldown_key):
                continue

            # 发送邮件通知
            self._send_email_by_config(email_config, notification_type, data)


# 全局通知器实例（延迟初始化）
_notifier_instance: Notifier | None = None


def get_notifier() -> Notifier:
    """获取通知器实例"""
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = Notifier(config_manager)
    return _notifier_instance


def send_notify(
    notification_type: str, item: Any = None, source: str = None, **kwargs
) -> bool:
    """
    安全发送通知的便捷函数

    Args:
        notification_type: 通知类型
        item: CustomItem 对象或 None，自动提取 user_name, title, ori_title, season, episode
              如果为 None 或字段不存在，使用默认值
        source: 来源（覆盖 item.source）
        **kwargs: 额外的通知数据字段，会覆盖从 item 提取的同名字段

    Returns:
        bool: 是否发送成功
    """
    try:
        notifier = get_notifier()

        # 基础数据，带默认值
        data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user_name": "unknown",
            "title": "unknown",
            "ori_title": "",
            "season": 0,
            "episode": 0,
            "source": "",
        }

        # 从 item 对象提取字段（安全访问）
        if item is not None:
            data["user_name"] = getattr(item, "user_name", "unknown")
            data["title"] = getattr(item, "title", "unknown")
            data["ori_title"] = getattr(item, "ori_title", "") or ""
            data["season"] = getattr(item, "season", 0)
            data["episode"] = getattr(item, "episode", 0)
            data["source"] = getattr(item, "source", "")

        # source 参数覆盖
        if source is not None:
            data["source"] = source

        # kwargs 覆盖所有同名字段
        data.update(kwargs)

        notifier.send_notification_by_type(notification_type, data)
        return True
    except Exception as e:
        logger.error(f"发送 {notification_type} 通知失败: {e}")
        return False
