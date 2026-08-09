"""通知规则端到端流程测试

验证核心流程：
1. 渠道配置（认证字段 + 模板）能被 NotificationService 加载
2. 通知规则绑定事件类型 + 渠道
3. notify() 按规则路由到指定渠道，触发渠道 send()
4. 渠道配置变更后，注册表能刷新（_reload_notification_channels）
5. 规则的 types 过滤：匹配的事件触发，不匹配的事件跳过
"""

from unittest.mock import MagicMock, patch

from app.services.notification_service import NotificationService
from app.utils.notifier.channels import ChannelRegistry
from app.utils.notifier.channels_impl import WebhookChannel

# ─────────────────────────────────────────────────────────────────────────
# 测试辅助
# ─────────────────────────────────────────────────────────────────────────


class _FakeChannel(WebhookChannel):
    """记录 send() 调用的测试渠道"""

    channel_type = "webhook"
    channel_label = "测试渠道"

    def __init__(self, channel_id: str, enabled: bool = True):
        super().__init__(
            channel_id=channel_id,
            config={"url": "https://example.com/hook", "enabled": enabled},
        )
        self.send_calls: list[tuple[str, dict]] = []

    def send(self, notification_type, payload, rendered=None):
        self.send_calls.append((notification_type, payload))
        from app.utils.notifier.channels import ChannelSendResult

        return ChannelSendResult(
            success=True,
            channel_id=self.channel_id,
            channel_name=self.channel_label,
        )


def _make_config_manager(rules: list[dict], in_app_enabled: bool = True):
    """构造一个 mock config_manager，包含指定的 notify-rule 段"""
    mock = MagicMock()
    parser = MagicMock()
    sections = []
    for i, _rule in enumerate(rules, 1):
        sections.append(f"notify-rule-{i}")
    mock.get_config_parser.return_value = parser
    parser.sections.return_value = sections

    def get_section(name):
        if name.startswith("notify-rule-"):
            idx = int(name.split("-")[-1]) - 1
            return rules[idx]
        if name == "notify-in-app":
            return {"in_app_notification": in_app_enabled}
        return {}

    mock.get_section.side_effect = get_section
    return mock


# ─────────────────────────────────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────────────


def test_no_rules_skips_channel_dispatch():
    """无规则时停发外部渠道通知（渠道只是配置，规则是发布闸门）"""
    channel = _FakeChannel("notify-webhook-1")
    service = NotificationService(channel_registry=ChannelRegistry())
    service.channel_registry.register(channel)
    service.cooldown.cooldown_seconds = 0

    cfg_mgr = _make_config_manager(rules=[])

    with (
        patch("app.core.config.config_manager", cfg_mgr),
        patch(
            "app.services.notification_service.NotificationService._lazy_load_channels"
        ),
    ):
        result = service.notify("mark_failed", source="test")

    assert result is True
    assert len(channel.send_calls) == 0


def test_rule_routes_to_specified_channel():
    """规则匹配时，notify() 应调用规则中指定的渠道"""
    channel = _FakeChannel("notify-webhook-1")
    service = NotificationService(channel_registry=ChannelRegistry())
    service.channel_registry.register(channel)
    service.cooldown.cooldown_seconds = 0  # 关闭冷却

    rules = [
        {
            "enabled": True,
            "types": "mark_failed",
            "channels": "notify-webhook-1",
        }
    ]
    cfg_mgr = _make_config_manager(rules)

    with (
        patch("app.core.config.config_manager", cfg_mgr),
        patch(
            "app.services.notification_service.NotificationService._lazy_load_channels"
        ),
    ):
        service.notify("mark_failed", source="test")

    assert len(channel.send_calls) == 1
    assert channel.send_calls[0][0] == "mark_failed"


def test_rule_skips_non_matching_type():
    """规则 types 不匹配时，不应触发渠道"""
    channel = _FakeChannel("notify-webhook-1")
    service = NotificationService(channel_registry=ChannelRegistry())
    service.channel_registry.register(channel)
    service.cooldown.cooldown_seconds = 0

    rules = [
        {
            "enabled": True,
            "types": "mark_success",
            "channels": "notify-webhook-1",
        }
    ]
    cfg_mgr = _make_config_manager(rules)

    with (
        patch("app.core.config.config_manager", cfg_mgr),
        patch(
            "app.services.notification_service.NotificationService._lazy_load_channels"
        ),
    ):
        service.notify("mark_failed", source="test")

    assert len(channel.send_calls) == 0


def test_rule_all_types_matches_everything():
    """types='all' 应匹配所有通知类型"""
    channel = _FakeChannel("notify-webhook-1")
    service = NotificationService(channel_registry=ChannelRegistry())
    service.channel_registry.register(channel)
    service.cooldown.cooldown_seconds = 0

    rules = [
        {
            "enabled": True,
            "types": "all",
            "channels": "notify-webhook-1",
        }
    ]
    cfg_mgr = _make_config_manager(rules)

    with (
        patch("app.core.config.config_manager", cfg_mgr),
        patch(
            "app.services.notification_service.NotificationService._lazy_load_channels"
        ),
    ):
        service.notify("mark_failed", source="test")
        service.notify("request_received", source="test")
        service.notify("config_error", source="test")

    assert len(channel.send_calls) == 3


def test_rule_disabled_skipped():
    """禁用的规则不应触发"""
    channel = _FakeChannel("notify-webhook-1")
    service = NotificationService(channel_registry=ChannelRegistry())
    service.channel_registry.register(channel)
    service.cooldown.cooldown_seconds = 0

    rules = [
        {
            "enabled": False,
            "types": "all",
            "channels": "notify-webhook-1",
        }
    ]
    cfg_mgr = _make_config_manager(rules)

    with (
        patch("app.core.config.config_manager", cfg_mgr),
        patch(
            "app.services.notification_service.NotificationService._lazy_load_channels"
        ),
    ):
        service.notify("mark_failed", source="test")

    assert len(channel.send_calls) == 0


def test_rule_multiple_channels():
    """一条规则绑定多个渠道时，所有渠道都应被触发"""
    ch1 = _FakeChannel("notify-webhook-1")
    ch2 = _FakeChannel("notify-webhook-2")
    service = NotificationService(channel_registry=ChannelRegistry())
    service.channel_registry.register(ch1)
    service.channel_registry.register(ch2)
    service.cooldown.cooldown_seconds = 0

    rules = [
        {
            "enabled": True,
            "types": "all",
            "channels": "notify-webhook-1,notify-webhook-2",
        }
    ]
    cfg_mgr = _make_config_manager(rules)

    with (
        patch("app.core.config.config_manager", cfg_mgr),
        patch(
            "app.services.notification_service.NotificationService._lazy_load_channels"
        ),
    ):
        service.notify("mark_failed", source="test")

    assert len(ch1.send_calls) == 1
    assert len(ch2.send_calls) == 1


def test_rule_unknown_channel_skipped():
    """规则引用不存在的渠道时，跳过而不报错"""
    channel = _FakeChannel("notify-webhook-1")
    service = NotificationService(channel_registry=ChannelRegistry())
    service.channel_registry.register(channel)
    service.cooldown.cooldown_seconds = 0

    rules = [
        {
            "enabled": True,
            "types": "all",
            "channels": "notify-webhook-1,notify-webhook-999",
        }
    ]
    cfg_mgr = _make_config_manager(rules)

    with (
        patch("app.core.config.config_manager", cfg_mgr),
        patch(
            "app.services.notification_service.NotificationService._lazy_load_channels"
        ),
    ):
        service.notify("mark_failed", source="test")

    # 只有存在的渠道被触发
    assert len(channel.send_calls) == 1


def test_load_channels_from_config_refreshes_registry():
    """渠道配置变更后 load_channels_from_config 应刷新注册表"""
    service = NotificationService(channel_registry=ChannelRegistry())
    assert len(service.channel_registry.all()) == 0

    # 构造含一个 webhook 段的配置
    cfg_mgr = MagicMock()
    parser = MagicMock()
    parser.sections.return_value = ["notify-webhook-1", "notify-in-app"]
    cfg_mgr.get_config_parser.return_value = parser

    def get_section(name):
        if name == "notify-webhook-1":
            return {"url": "https://example.com/hook", "enabled": True}
        if name == "notify-in-app":
            return {"in_app_notification": True}
        return {}

    cfg_mgr.get_section.side_effect = get_section

    count = service.load_channels_from_config(cfg_mgr)
    assert count == 1
    assert service.channel_registry.get("notify-webhook-1") is not None

    # 删除该段后再加载，注册表应刷新
    parser.sections.return_value = ["notify-in-app"]
    count = service.load_channels_from_config(cfg_mgr)
    assert count == 0
    assert service.channel_registry.get("notify-webhook-1") is None


def test_api_reload_hook_invokes_load():
    """_reload_notification_channels 应调用 load_channels_from_config"""
    from app.api.notification import _reload_notification_channels

    with (
        patch("app.services.notification_service.notification_service") as mock_svc,
        patch("app.api.notification.config_manager") as mock_cfg,
    ):
        _reload_notification_channels()
        mock_svc.load_channels_from_config.assert_called_once_with(mock_cfg)


def test_api_reload_hook_swallows_exceptions():
    """_reload_notification_channels 异常时不应抛出"""
    from app.api.notification import _reload_notification_channels

    with patch("app.services.notification_service.notification_service") as mock_svc:
        mock_svc.load_channels_from_config.side_effect = RuntimeError("boom")
        # 不应抛出
        _reload_notification_channels()
