"""通知统一服务（NotificationService）

作为所有通知发送的单一入口，替代原先的 send_notify + insert_notification 双调用模式。
调用方只需 ``notification_service.notify(type, item, source, **kwargs)``，
Service 内部根据 NotificationTypeRegistry 的元数据决定：
- 发送到 webhook/email 渠道（委托给现有 Notifier）
- 是否写站内信（根据 in_app_type 映射）

CooldownPolicy 从 Notifier 提取为独立策略，支持可配置冷却时长。
"""

from __future__ import annotations

import time
from typing import Any

from ..core.logging import logger
from ..core.notification_registry import (
    get_type_meta,
    is_item_level_type,
    resolve_in_app_type,
)
from ..utils.notifier import get_notifier


class CooldownPolicy:
    """通知冷却策略

    支持全局冷却时长 + 按类型自定义。
    条目级类型按 title+season+episode 维度冷却，避免不同番剧互相静默。
    """

    def __init__(self, default_cooldown: int = 60) -> None:
        self._default_cooldown = default_cooldown
        self._type_cooldowns: dict[str, int] = {}
        self._last_time: dict[str, float] = {}

    def set_type_cooldown(self, type_id: str, seconds: int) -> None:
        """为指定类型设置自定义冷却时长"""
        self._type_cooldowns[type_id] = seconds

    def get_cooldown(self, type_id: str) -> int:
        """获取类型的冷却时长"""
        return self._type_cooldowns.get(type_id, self._default_cooldown)

    def _build_key(self, channel_id: str, type_id: str, data: dict[str, Any]) -> str:
        """构造冷却 key"""
        key = f"{channel_id}_{type_id}"
        if is_item_level_type(type_id):
            item_key = (
                f"{data.get('title', '')}_{data.get('season', 0)}_"
                f"{data.get('episode', 0)}"
            )
            key = f"{key}_{item_key}"
        return key

    def should_send(
        self,
        channel_id: str,
        type_id: str,
        data: dict[str, Any],
        skip_cooldown: bool = False,
    ) -> bool:
        """检查是否应该发送（冷却未过期）"""
        if skip_cooldown:
            return True
        key = self._build_key(channel_id, type_id, data)
        now = time.time()
        last = self._last_time.get(key, 0)
        cooldown = self.get_cooldown(type_id)
        if now - last < cooldown:
            logger.debug(f"通知冷却中，跳过 {type_id} 类型通知")
            return False
        self._last_time[key] = now
        return True


class NotificationService:
    """通知统一服务（单例）"""

    def __init__(self) -> None:
        self._notifier = get_notifier()
        self._cooldown = CooldownPolicy()
        self._db_manager: Any = None  # 延迟初始化，避免循环 import

    def _get_db_manager(self) -> Any:
        if self._db_manager is None:
            from ..core.database import database_manager

            self._db_manager = database_manager
        return self._db_manager

    def notify(
        self,
        notification_type: str,
        item: Any = None,
        source: str | None = None,
        *,
        skip_cooldown: bool = False,
        write_in_app: bool = True,
        in_app_title: str | None = None,
        in_app_body: str | None = None,
        in_app_ref_id: int | None = None,
        **kwargs: Any,
    ) -> bool:
        """统一通知入口

        Args:
            notification_type: 通知类型标识
            item: CustomItem 对象或 None
            source: 来源（覆盖 item.source）
            skip_cooldown: 跳过冷却检查
            write_in_app: 是否写站内信（仅在类型有 in_app_type 映射时生效）
            in_app_title: 自定义站内信标题（覆盖模板）
            in_app_body: 自定义站内信正文（覆盖 message）
            in_app_ref_id: 站内信 ref_id（关联 sync_records.id）
            **kwargs: 额外数据字段
        """
        try:
            # 构建通知数据
            data = self._build_data(item, source, **kwargs)

            # 1. 发送 webhook + email（委托给现有 Notifier）
            self._send_to_channels(notification_type, data, skip_cooldown)

            # 2. 写站内信（如果类型有 in_app_type 映射）
            if write_in_app:
                self._write_in_app_notification(
                    notification_type, data, in_app_title, in_app_body, in_app_ref_id
                )

            return True
        except Exception as e:
            logger.error(f"发送 {notification_type} 通知失败: {e}")
            return False

    def _build_data(
        self,
        item: Any,
        source: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """从 item + kwargs 构建通知数据"""
        data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user_name": "unknown",
            "title": "unknown",
            "ori_title": "",
            "season": 0,
            "episode": 0,
            "source": "",
        }
        if item is not None:
            data["user_name"] = getattr(item, "user_name", "unknown")
            data["title"] = getattr(item, "title", "unknown")
            data["ori_title"] = getattr(item, "ori_title", "") or ""
            data["season"] = getattr(item, "season", 0)
            data["episode"] = getattr(item, "episode", 0)
            data["source"] = getattr(item, "source", "")
        if source is not None:
            data["source"] = source
        data.update(kwargs)
        return data

    def _send_to_channels(
        self,
        notification_type: str,
        data: dict[str, Any],
        skip_cooldown: bool,
    ) -> None:
        """发送到 webhook + email 渠道（委托给现有 Notifier）"""
        self._notifier.send_notification_by_type(
            notification_type, data, skip_cooldown=skip_cooldown
        )

    def _write_in_app_notification(
        self,
        notification_type: str,
        data: dict[str, Any],
        custom_title: str | None,
        custom_body: str | None,
        ref_id: int | None,
    ) -> None:
        """写站内信（如果类型有 in_app_type 映射）"""
        in_app_type = resolve_in_app_type(notification_type)
        if in_app_type is None:
            return

        meta = get_type_meta(notification_type)
        if custom_title:
            title = custom_title
        elif meta and meta.in_app_title_template:
            # 构造 ep_label
            media_type = data.get("media_type", "episode")
            ep_label = (
                f"S{data.get('season', 0)}E{data.get('episode', 0)}"
                if media_type == "episode"
                else "剧场版"
            )
            title = meta.in_app_title_template.format(
                title=data.get("title", "unknown"),
                ep_label=ep_label,
            )
        else:
            title = data.get("title", "unknown")

        body = custom_body or data.get("error_message", "") or data.get("message", "")

        try:
            self._get_db_manager().insert_notification(in_app_type, title, body, ref_id)
        except Exception as e:
            logger.debug(f"写站内信失败: {e}")


# 模块级单例
notification_service = NotificationService()


def notify(
    notification_type: str,
    item: Any = None,
    source: str | None = None,
    **kwargs: Any,
) -> bool:
    """便捷函数：通过 NotificationService 发送通知"""
    return notification_service.notify(notification_type, item, source, **kwargs)
