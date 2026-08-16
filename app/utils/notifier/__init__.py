"""通知模块 - 支持 Webhook、邮件和站内信通知

本模块提供两种使用方式：

1. **推荐**：通过 :class:`~app.services.notification_service.NotificationService` 统一入口，
   自动完成「模板渲染 → 渠道路由 → 冷却检查 → 发送」的完整流程。
2. **兼容**：保留原有 :class:`Notifier` 及其 mixin（:mod:`email_sender` /
   :mod:`webhook` / :mod:`html_builders` / :mod:`selftest`），供旧代码和单元测试继续使用。

新增渠道请继承 :class:`NotificationChannel`（见 :mod:`.channels`），
新增模板请放到 ``templates/notifications/<channel>/`` 目录。
"""

from __future__ import annotations

from typing import Any

from ...core.config import config_manager
from ...core.logging import logger
from .channels import (
    ChannelRegistry,
    ChannelSendResult,
    NotificationChannel,
    channel_registry,
)
from .channels_impl import (
    DingTalkChannel,
    EmailChannel,
    InAppChannel,
    WebhookChannel,
    WeChatWorkChannel,
)

# 旧版 Notifier 及其 mixin（保留，用于测试与向后兼容）
from .email_sender import EmailSenderMixin
from .html_builders import EmailHtmlMixin
from .selftest import TestHelpersMixin
from .template_manager import (
    NotificationTemplateManager,
    template_manager,
)
from .webhook import WebhookMixin

ITEM_LEVEL_TYPES = {
    "pending_candidate",
    "mark_failed",
    "mark_success",
    "mark_skipped",
    "sync_replayed",
    "sync_queued",
    "bangumi_id_found",
    "request_received",
}


class Notifier(EmailHtmlMixin, WebhookMixin, EmailSenderMixin, TestHelpersMixin):
    """旧版通知管理器（保留）

    新代码请使用 :class:`~app.services.notification_service.NotificationService`。
    """

    def __init__(self, config_manager: Any) -> None:
        import time

        self.config_manager = config_manager
        self._last_notification_time: dict[str, float] = {}
        self._notification_cooldown = 60
        self._time_module = time

    def _should_send_notification(self, notification_type: str) -> bool:
        current_time = self._time_module.time()
        last_time = self._last_notification_time.get(notification_type, 0)
        if current_time - last_time < self._notification_cooldown:
            logger.debug(f"通知冷却中，跳过 {notification_type} 类型通知")
            return False
        self._last_notification_time[notification_type] = current_time
        return True

    def _build_cooldown_key(
        self, channel_id: str, notification_type: str, data: dict[str, Any]
    ) -> str:
        key = f"{channel_id}_{notification_type}"
        if notification_type in ITEM_LEVEL_TYPES:
            item_key = (
                f"{data.get('title', '')}_{data.get('season', 0)}_"
                f"{data.get('episode', 0)}"
            )
            key = f"{key}_{item_key}"
        return key

    @staticmethod
    def _type_matches(notification_type: str, types: str) -> bool:
        if types == "all":
            return True
        type_list = [t.strip() for t in types.split(",")]
        return notification_type in type_list

    def _get_webhook_configs(self) -> list:
        config = self.config_manager.get_config_parser()
        webhook_configs = []
        for section_name in config.sections():
            if section_name.startswith("notify-webhook-"):
                section_config = self.config_manager.get_section(section_name)
                if section_config.get("url"):
                    webhook_configs.append(section_config)
        return webhook_configs

    def _get_email_configs(self) -> list:
        config = self.config_manager.get_config_parser()
        email_configs = []
        for section_name in config.sections():
            if section_name.startswith("notify-email-"):
                section_config = self.config_manager.get_section(section_name)
                if section_config.get("smtp_server"):
                    email_configs.append(section_config)
        return email_configs

    def send_notification_by_type(
        self,
        notification_type: str,
        data: dict[str, Any],
        skip_cooldown: bool = False,
    ) -> None:
        for webhook_config in self._get_webhook_configs():
            if not webhook_config.get("enabled", False):
                continue
            types = webhook_config.get("types", "")
            if not self._type_matches(notification_type, types):
                continue
            cooldown_key = self._build_cooldown_key(
                str(webhook_config.get("id", "")), notification_type, data
            )
            if not skip_cooldown and not self._should_send_notification(cooldown_key):
                continue
            self._send_webhook_by_config(webhook_config, notification_type, data)

        for email_config in self._get_email_configs():
            if not email_config.get("enabled", False):
                continue
            types = email_config.get("types", "")
            if not self._type_matches(notification_type, types):
                continue
            cooldown_key = self._build_cooldown_key(
                f"email_{email_config.get('id', '')}", notification_type, data
            )
            if not skip_cooldown and not self._should_send_notification(cooldown_key):
                continue
            self._send_email_by_config(email_config, notification_type, data)


# 全局通知器实例（延迟初始化，保持向后兼容）
_notifier_instance: Notifier | None = None


def get_notifier() -> Notifier:
    """获取旧版通知器实例（保留兼容）

    新代码推荐使用 :func:`~app.services.notification_service.notify`。
    """
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = Notifier(config_manager)
    return _notifier_instance


__all__ = [
    # 新架构
    "NotificationChannel",
    "ChannelRegistry",
    "ChannelSendResult",
    "channel_registry",
    "NotificationTemplateManager",
    "template_manager",
    "WebhookChannel",
    "EmailChannel",
    "WeChatWorkChannel",
    "DingTalkChannel",
    "InAppChannel",
    # 旧架构（保留）
    "Notifier",
    "get_notifier",
    "EmailSenderMixin",
    "EmailHtmlMixin",
    "WebhookMixin",
    "TestHelpersMixin",
    "ITEM_LEVEL_TYPES",
]
