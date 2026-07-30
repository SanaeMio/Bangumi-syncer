"""通知统一服务（NotificationService）

解耦后的通知入口：

``事件（notification_type + data）→ 模板渲染 → 渠道路由 → 发送``

核心职责：
1. 构造统一的数据 payload
2. 通过 :class:`NotificationTemplateManager` 为每个渠道渲染模板
3. 通过 :class:`ChannelRegistry` 找到订阅该事件的所有启用渠道
4. 执行冷却检查（委托给 :class:`CooldownPolicy`）
5. 分发到各渠道发送，收集结果
6. 按站内信映射写入数据库

调用方只需：
``notification_service.notify(type, item, source, **kwargs)``
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..core.logging import logger
from ..core.notification_registry import (
    get_type_meta,
    resolve_in_app_type,
)
from ..utils.notifier.channels import ChannelRegistry, NotificationChannel
from ..utils.notifier.channels_impl import (
    DingTalkChannel,
    EmailChannel,
    InAppChannel,
    WebhookChannel,
    WeChatWorkChannel,
)
from ..utils.notifier.template_manager import (
    NotificationTemplateManager,
    template_manager,
)

# ─────────────────────────────────────────────────────────────────────────
# 冷却策略
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class CooldownPolicy:
    """冷却策略：防止通知轰炸

    - 「条目级」类型（mark_failed / mark_success / ...）按 ``channel_id + type + title + season + episode`` 冷却
    - 「系统级」类型按 ``channel_id + type`` 冷却
    """

    cooldown_seconds: int = 60

    def __post_init__(self) -> None:
        self._last_sent: dict[str, float] = {}

    def _key(
        self, channel_id: str, notification_type: str, data: dict[str, Any]
    ) -> str:
        key = f"{channel_id}::{notification_type}"
        meta = get_type_meta(notification_type)
        if meta and meta.is_item_level:
            item_key = f"{data.get('title', '')}::S{data.get('season', 0)}E{data.get('episode', 0)}"
            key = f"{key}::{item_key}"
        return key

    def allow(
        self,
        channel_id: str,
        notification_type: str,
        data: dict[str, Any],
        skip: bool = False,
    ) -> bool:
        if skip:
            return True
        now = time.time()
        key = self._key(channel_id, notification_type, data)
        last = self._last_sent.get(key, 0.0)
        if now - last < self.cooldown_seconds:
            logger.debug(
                f"通知冷却中，跳过 channel={channel_id} type={notification_type}"
            )
            return False
        self._last_sent[key] = now
        return True

    def reset(self) -> None:
        self._last_sent.clear()


# ─────────────────────────────────────────────────────────────────────────
# 服务主类
# ─────────────────────────────────────────────────────────────────────────


class NotificationService:
    """通知统一服务（单例）

    依赖注入：
        - ``channel_registry``：可替换的渠道注册表（默认使用全局单例）
        - ``template_mgr``：可替换的模板管理器（默认使用全局单例）
    """

    def __init__(
        self,
        channel_registry: ChannelRegistry | None = None,
        template_mgr: NotificationTemplateManager | None = None,
    ) -> None:
        self.channel_registry = channel_registry or ChannelRegistry()
        self.template_mgr = template_mgr or template_manager
        self.cooldown = CooldownPolicy()
        self._db_manager: Any = None  # 延迟初始化
        self._in_app_channel: InAppChannel | None = None

    # ── 延迟初始化依赖 ──────────────────────────────────────────────────

    def _get_db_manager(self) -> Any:
        if self._db_manager is None:
            from ..core.database import database_manager

            self._db_manager = database_manager
        return self._db_manager

    # ── 渠道装配 ────────────────────────────────────────────────────────

    def load_channels_from_config(self, config_manager: Any) -> int:
        """从 INI 配置中加载所有 webhook/email/wecom/dingtalk 渠道

        约定：
        - ``notify-webhook-N`` 段 → :class:`WebhookChannel`
        - ``notify-email-N`` 段 → :class:`EmailChannel`
        - ``notify-wecom-N`` 段 → :class:`WeChatWorkChannel`
        - ``notify-dingtalk-N`` 段 → :class:`DingTalkChannel`
        同时注册一个 :class:`InAppChannel`（由 ``in_app_notification`` 配置开关决定）。

        Returns:
            本次注册的渠道数
        """
        self.channel_registry.clear()
        count = 0

        try:
            parser = config_manager.get_config_parser()
        except Exception as e:
            logger.warning(f"加载通知配置失败: {e}")
            return 0

        for section in parser.sections():
            if section.startswith("notify-webhook-"):
                cfg = config_manager.get_section(section)
                if not cfg.get("url"):
                    continue
                ch = WebhookChannel(channel_id=section, config=cfg)
                self.channel_registry.register(ch)
                count += 1
            elif section.startswith("notify-email-"):
                cfg = config_manager.get_section(section)
                if not cfg.get("smtp_server"):
                    continue
                ch = EmailChannel(channel_id=section, config=cfg)
                self.channel_registry.register(ch)
                count += 1
            elif section.startswith("notify-wecom-"):
                cfg = config_manager.get_section(section)
                if not cfg.get("key"):
                    continue
                ch = WeChatWorkChannel(channel_id=section, config=cfg)
                self.channel_registry.register(ch)
                count += 1
            elif section.startswith("notify-dingtalk-"):
                cfg = config_manager.get_section(section)
                if not cfg.get("access_token"):
                    continue
                ch = DingTalkChannel(channel_id=section, config=cfg)
                self.channel_registry.register(ch)
                count += 1

        # 站内信作为一个特殊渠道注册（enabled 取决于配置）
        in_app_cfg = {"in_app_notification": True}
        try:
            in_app_cfg = config_manager.get_section("notify-in-app") or in_app_cfg
        except Exception:
            pass
        in_app = InAppChannel(channel_id="in_app", config=in_app_cfg)
        self._in_app_channel = in_app
        self.channel_registry.register(in_app)

        logger.info(f"已加载 {count} 个外部通知渠道 + 1 个站内信渠道")
        return count

    # ── 对外主入口 ──────────────────────────────────────────────────────

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
        in_app_type: str | None = None,
        **kwargs: Any,
    ) -> bool:
        """统一通知入口

        Args:
            notification_type: 通知类型标识
            item: CustomItem 对象或 None
            source: 来源（覆盖 item.source）
            skip_cooldown: 跳过冷却检查
            write_in_app: 是否写站内信
            in_app_title: 自定义站内信标题
            in_app_body: 自定义站内信正文
            in_app_ref_id: 站内信 ref_id
            in_app_type: 自定义站内信 type
            **kwargs: 额外数据字段
        """
        try:
            data = self._build_data(item, source, **kwargs)

            # 扩展：若注册表为空，尝试从 config_manager 延迟加载
            if not self.channel_registry.all():
                self._lazy_load_channels()

            # 1. 判断是否存在通知规则配置
            from ..core.config import config_manager as _cfg_mgr

            has_rules = self._has_rules(_cfg_mgr)

            if has_rules:
                # 规则模式：按 notify-rule 中配置的 channels 路由
                self._dispatch_by_rules(
                    notification_type, data, skip_cooldown, _cfg_mgr
                )
            else:
                # 传统模式：所有启用渠道自行判断 types
                self._dispatch_to_channels(notification_type, data, skip_cooldown)

            # 2. 写站内信
            if write_in_app:
                self._write_in_app_notification(
                    notification_type,
                    data,
                    in_app_title,
                    in_app_body,
                    in_app_ref_id,
                    in_app_type,
                )

            return True
        except Exception as e:
            logger.error(f"发送 {notification_type} 通知失败: {e}")
            return False

    def _lazy_load_channels(self) -> None:
        """当首次调用时注册表为空，尝试从 config_manager 加载"""
        try:
            from ..core.config import config_manager

            self.load_channels_from_config(config_manager)
        except Exception as e:
            logger.debug(f"延迟加载通知渠道失败（可忽略）: {e}")

    def _has_rules(self, config_manager: Any) -> bool:
        """检查是否存在 notify-rule 配置"""
        try:
            parser = config_manager.get_config_parser()
            for section in parser.sections():
                if section.startswith("notify-rule-"):
                    return True
        except Exception:
            pass
        return False

    def _get_enabled_rules(
        self, config_manager: Any, notification_type: str
    ) -> list[dict[str, Any]]:
        """获取所有启用且匹配指定通知类型的规则"""
        rules: list[dict[str, Any]] = []
        try:
            parser = config_manager.get_config_parser()
            for section in parser.sections():
                if not section.startswith("notify-rule-"):
                    continue
                cfg = config_manager.get_section(section)
                if not cfg.get("enabled", False):
                    continue
                types_str = str(cfg.get("types", "all")).strip()
                # 空字符串或 "all" 表示订阅全部事件（与渠道 supports 行为一致）
                if types_str and types_str != "all":
                    lookup = notification_type
                    if lookup.startswith("watching_summary"):
                        lookup = "watching_summary"
                    if lookup not in {
                        t.strip() for t in types_str.split(",") if t.strip()
                    }:
                        continue
                rules.append(dict(cfg))
        except Exception as e:
            logger.debug(f"获取通知规则失败: {e}")
        return rules

    # ── 构建 payload ────────────────────────────────────────────────────

    def _build_data(
        self,
        item: Any,
        source: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """从 item + kwargs 构建通知数据"""
        data: dict[str, Any] = {
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
        # 补充字段别名，保持与 webhook/default.json 模板占位符一致
        data.setdefault("user", data.get("user_name", ""))
        data.setdefault("anime", data.get("title", ""))
        data.setdefault("error", data.get("error_message", ""))
        return data

    # ── 渠道分发 ──────────────────────────────────────────────────────

    def _dispatch_to_channels(
        self,
        notification_type: str,
        data: dict[str, Any],
        skip_cooldown: bool,
    ) -> None:
        """将事件分发到所有匹配的外部渠道（webhook / email）"""
        for channel in self.channel_registry.iter_enabled(notification_type):
            if channel.channel_type == "in_app":
                continue  # 站内信走单独路径

            if not self.cooldown.allow(
                channel.channel_id, notification_type, data, skip_cooldown
            ):
                continue

            try:
                # 模板渲染
                rendered = self._render_for_channel(channel, notification_type, data)

                # 发送
                result = channel.send(notification_type, rendered["payload"], rendered)
                if not result.success:
                    logger.warning(
                        f"渠道 {channel.channel_id} 发送失败: {result.message}"
                    )
            except Exception as e:
                logger.error(f"渠道 {channel.channel_id} 发送异常: {e}")

    def _dispatch_by_rules(
        self,
        notification_type: str,
        data: dict[str, Any],
        skip_cooldown: bool,
        config_manager: Any,
    ) -> None:
        """按 notify-rule 配置将事件分发到指定渠道"""
        rules = self._get_enabled_rules(config_manager, notification_type)
        if not rules:
            logger.debug(f"无匹配规则，跳过 {notification_type} 通知")
            return

        for rule in rules:
            channels_str = str(rule.get("channels", "")).strip()
            if not channels_str:
                continue

            channel_ids = {c.strip() for c in channels_str.split(",") if c.strip()}
            for channel_id in channel_ids:
                channel = self.channel_registry.get(channel_id)
                if channel is None:
                    logger.warning(f"规则引用的渠道未找到: {channel_id}")
                    continue
                if not channel.enabled:
                    continue
                if channel.channel_type == "in_app":
                    continue

                if not self.cooldown.allow(
                    channel.channel_id, notification_type, data, skip_cooldown
                ):
                    continue

                try:
                    rendered = self._render_for_channel(
                        channel,
                        notification_type,
                        data,
                        rule_template=rule.get("template", ""),
                    )
                    result = channel.send(
                        notification_type, rendered["payload"], rendered
                    )
                    if not result.success:
                        logger.warning(
                            f"渠道 {channel.channel_id} 发送失败: {result.message}"
                        )
                except Exception as e:
                    logger.error(f"渠道 {channel.channel_id} 发送异常: {e}")

    def _render_for_channel(
        self,
        channel: NotificationChannel,
        notification_type: str,
        data: dict[str, Any],
        rule_template: str = "",
    ) -> dict[str, Any]:
        """针对不同渠道类型渲染模板，返回统一的 ``{"payload": ..., ...}`` 结构

        ``rule_template`` 为通知规则级模板，优先于渠道自身配置的 template。
        """
        # 扩展 data，注入 meta 中的显示名称等
        meta = get_type_meta(notification_type)
        type_data = dict(data)
        type_data.setdefault("notification_type", notification_type)
        if meta:
            type_data.setdefault("type_display_name", meta.display_name)
            type_data.setdefault("type_icon", meta.icon)

        channel_type = channel.channel_type
        if channel_type == "webhook":
            # 规则级模板优先于渠道级
            inline_tpl = (rule_template or channel.config.get("template", "")).strip()
            if inline_tpl:
                try:
                    import json

                    obj = json.loads(inline_tpl)
                    payload = self.template_mgr.render_value(obj, type_data)
                except Exception:
                    payload = self.template_mgr.render_webhook_payload(
                        type_data, fallback=type_data
                    )
            else:
                payload = self.template_mgr.render_webhook_payload(
                    type_data, fallback=type_data
                )
            return {"payload": payload}

        if channel_type == "email":
            # 规则级模板优先于渠道级
            email_tpl_name = (
                rule_template or channel.config.get("template", "")
            ).strip()
            if email_tpl_name:
                # template 字段存储模板名（如 "default"），通过模板名查找并渲染
                rendered = self.template_mgr.render_email(
                    type_data, template_name=email_tpl_name
                )
            else:
                rendered = self.template_mgr.render_email(type_data)
            # 如果配置了自定义 subject 则覆盖
            custom_subject = channel.config.get("email_subject", "").strip()
            if custom_subject:
                rendered["subject"] = self.template_mgr.render_string(
                    custom_subject, type_data
                )
            rendered["payload"] = type_data
            return rendered

        # 默认回退：直接把 data 当 payload
        return {"payload": type_data}

    # ── 站内信写入 ──────────────────────────────────────────────────────

    def _write_in_app_notification(
        self,
        notification_type: str,
        data: dict[str, Any],
        custom_title: str | None,
        custom_body: str | None,
        ref_id: int | None,
        custom_in_app_type: str | None = None,
    ) -> None:
        """写站内信"""
        # 检查站内信总开关（[notify-in-app] in_app_notification）
        if self._in_app_channel is not None and not self._in_app_channel.enabled:
            return

        in_app_type = custom_in_app_type or resolve_in_app_type(notification_type)
        if in_app_type is None:
            return

        # 站内信模板渲染
        in_app_data = dict(data)
        in_app_data["notification_type"] = notification_type

        rendered = self.template_mgr.render_in_app(notification_type, in_app_data)

        # 若没有模板，使用旧的默认规则
        meta = get_type_meta(notification_type)
        if custom_title:
            title = custom_title
        elif rendered.get("title"):
            title = rendered["title"] or ""
        elif meta and meta.in_app_title_template:
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

        body = (
            custom_body
            or rendered.get("body")
            or data.get("error_message", "")
            or data.get("message", "")
        )

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
