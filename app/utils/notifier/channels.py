"""通知渠道抽象基类与注册表（NotificationChannel / ChannelRegistry）

将「渠道」与「事件/模板」解耦：
- 每个渠道仅关心如何把一个已渲染好的 payload 发出去
- 路由与绑定逻辑由 :class:`NotificationService` 负责
- 新增渠道只需继承 :class:`NotificationChannel` 并在 :class:`ChannelRegistry` 注册
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChannelSendResult:
    """单次渠道发送结果"""

    success: bool
    channel_id: str = ""
    channel_name: str = ""
    message: str = ""

    def __bool__(self) -> bool:
        return self.success


class NotificationChannel(abc.ABC):
    """通知渠道抽象基类

    子类需实现 :meth:`send` 以发送已渲染好的 payload。
    渠道的配置（url/smtp/...）通过 :meth:`configure` 注入。
    """

    #: 渠道类型标识符（如 ``"webhook"`` / ``"email"`` / ``"in_app"``）
    channel_type: str = ""
    #: 人类可读名称
    channel_label: str = ""

    def __init__(self, channel_id: str, config: dict[str, Any]) -> None:
        self.channel_id = channel_id
        self.config = config
        self.enabled: bool = bool(config.get("enabled", False))

    def configure(self, config: dict[str, Any]) -> None:
        """更新渠道配置（如配置热加载）"""
        self.config = config
        self.enabled = bool(config.get("enabled", False))

    def supports(self, notification_type: str) -> bool:
        """判断该渠道是否订阅了指定通知类型

        约定：配置中的 ``types`` 字段为逗号分隔的类型列表，``"all"`` 表示全订阅。
        """
        types = str(self.config.get("types", "all")).strip()
        if not types or types == "all":
            return True
        # watching_summary_* 归一化到 watching_summary
        lookup = notification_type
        if lookup.startswith("watching_summary"):
            lookup = "watching_summary"
        return lookup in {t.strip() for t in types.split(",") if t.strip()}

    @abc.abstractmethod
    def send(
        self,
        notification_type: str,
        payload: dict[str, Any],
        rendered: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        """发送通知

        Args:
            notification_type: 通知类型 id
            payload: 已渲染的模板变量字典（会作为 JSON / 邮件正文等源数据）
            rendered: 可选的「已完整渲染」产物（如 email 已渲染好的 subject/html）

        Returns:
            :class:`ChannelSendResult`
        """


@dataclass
class ChannelRegistry:
    """通知渠道注册表（轻量容器）

    仅在应用启动时从配置中实例化渠道；运行时通过
    :meth:`iter_enabled` 获取所有启用的、订阅指定类型的渠道。
    """

    _channels: dict[str, NotificationChannel] = field(default_factory=dict)

    def register(self, channel: NotificationChannel) -> None:
        self._channels[channel.channel_id] = channel

    def unregister(self, channel_id: str) -> None:
        self._channels.pop(channel_id, None)

    def get(self, channel_id: str) -> NotificationChannel | None:
        return self._channels.get(channel_id)

    def all(self) -> list[NotificationChannel]:
        return list(self._channels.values())

    def iter_enabled(
        self, notification_type: str | None = None
    ) -> list[NotificationChannel]:
        """返回所有启用的渠道；若传入 ``notification_type``，仅返回订阅该类型的渠道"""
        result: list[NotificationChannel] = []
        for ch in self._channels.values():
            if not ch.enabled:
                continue
            if notification_type and not ch.supports(notification_type):
                continue
            result.append(ch)
        return result

    def clear(self) -> None:
        self._channels.clear()


# 模块级单例
channel_registry = ChannelRegistry()
