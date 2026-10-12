"""通知模块 - 渠道实现、模板管理与注册表

本模块是通知能力的底层设施；业务代码应通过
:class:`~app.services.notification_service.NotificationService` 统一入口发送通知，
由后者完成「模板渲染 → 渠道路由 → 冷却检查 → 发送」的完整流程。

- 新增渠道：继承 :class:`NotificationChannel`（见 :mod:`.channels`），
  并在 :mod:`.channels_impl` 中实现 ``send()``。
- 新增模板：放到 ``templates/notifications/<channel>/`` 目录。
- 新增通知类型：在 :mod:`app.core.notification_registry` 登记元数据。
"""

from __future__ import annotations

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
from .template_manager import (
    NotificationTemplateManager,
    template_manager,
)

__all__ = [
    # 渠道抽象与注册表
    "NotificationChannel",
    "ChannelRegistry",
    "ChannelSendResult",
    "channel_registry",
    # 模板
    "NotificationTemplateManager",
    "template_manager",
    # 各渠道实现
    "WebhookChannel",
    "EmailChannel",
    "WeChatWorkChannel",
    "DingTalkChannel",
    "InAppChannel",
]
