"""通知渠道与渲染测试（新架构）

原 tests/utils/test_notifier.py（1597 行）针对已删除的旧 Notifier 及其
mixin（WebhookMixin / EmailSenderMixin / html_builders / selftest）编写。
重构后那些实现已不存在，其中大量断言针对的是被删除实现里的中文硬编码
文案，无法也不应迁移。

本文件只保留**在新架构下依然成立且有价值**的契约：

1. 模板渲染原语（render_string / render_value）
2. 请求头解析
3. WebhookChannel / EmailChannel 的传输分支（HTTP 成功/失败/异常、
   STARTTLS / SSL、缺收件人、SMTP 认证失败）
4. 渠道级 enabled=False 抑制
5. watching_summary 的正文在 email 渲染中可达（Issue #245 回归保护）
6. 规则分发与冷却的协作
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.notification_service import NotificationService
from app.utils.notifier.channels import ChannelRegistry, ChannelSendResult
from app.utils.notifier.channels_impl import (
    EmailChannel,
    WebhookChannel,
    _parse_headers,
)
from app.utils.notifier.template_manager import NotificationTemplateManager

# ─────────────────────────────────────────────────────────────────────────
# 模板渲染原语
# ─────────────────────────────────────────────────────────────────────────


class TestRenderString:
    def test_replaces_placeholder(self):
        assert (
            NotificationTemplateManager.render_string("你好 {name}", {"name": "世界"})
            == "你好 世界"
        )

    def test_missing_key_becomes_empty(self):
        assert NotificationTemplateManager.render_string("a{missing}b", {}) == "ab"

    def test_none_value_becomes_empty(self):
        assert NotificationTemplateManager.render_string("x{n}y", {"n": None}) == "xy"

    def test_int_value_rendered(self):
        assert NotificationTemplateManager.render_string("{n}", {"n": 12}) == "12"

    def test_no_placeholder_unchanged(self):
        assert NotificationTemplateManager.render_string("plain", {"a": 1}) == "plain"


class TestRenderValue:
    def test_dict_recurses(self):
        out = NotificationTemplateManager.render_value({"a": "{x}"}, {"x": "1"})
        assert out == {"a": "1"}

    def test_list_recurses(self):
        out = NotificationTemplateManager.render_value(["{x}", "b"], {"x": "1"})
        assert out == ["1", "b"]

    def test_nested_structure(self):
        tpl = {"outer": {"inner": ["{x}"]}}
        out = NotificationTemplateManager.render_value(tpl, {"x": "v"})
        assert out == {"outer": {"inner": ["v"]}}

    def test_non_string_passthrough(self):
        assert NotificationTemplateManager.render_value(5, {}) == 5
        assert NotificationTemplateManager.render_value(True, {}) is True
        assert NotificationTemplateManager.render_value(None, {}) is None


# ─────────────────────────────────────────────────────────────────────────
# 请求头解析（函数在新架构中依然存在）
# ─────────────────────────────────────────────────────────────────────────


class TestParseHeaders:
    def test_empty_keeps_user_agent(self):
        assert "User-Agent" in _parse_headers("")

    def test_json_format(self):
        result = _parse_headers('{"Authorization": "Bearer token123"}')
        assert result["Authorization"] == "Bearer token123"

    def test_comma_separated_format(self):
        result = _parse_headers("Authorization: Bearer token123, X-Custom: value")
        assert "Authorization" in result
        assert result["X-Custom"] == "value"

    def test_non_string_input(self):
        assert "User-Agent" in _parse_headers(123)


# ─────────────────────────────────────────────────────────────────────────
# WebhookChannel 传输分支
# ─────────────────────────────────────────────────────────────────────────


def _webhook_channel(**overrides) -> WebhookChannel:
    cfg = {"url": "https://example.com/hook", "method": "POST", "enabled": True}
    cfg.update(overrides)
    return WebhookChannel("notify-webhook-1", cfg)


class TestWebhookChannelSend:
    def _ok_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        return resp

    def test_post_success(self):
        channel = _webhook_channel()
        with patch("app.utils.notifier.channels_impl.SyncHttpClient") as mock_cls:
            mock_cls.return_value.prefix.return_value.success_tpl.return_value.failure_tpl.return_value.post.return_value = self._ok_response()
            result = channel.send("mark_failed", {"title": "t"})
        assert result.success is True

    def test_http_error_reports_failure(self):
        channel = _webhook_channel()
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "boom"
        with patch("app.utils.notifier.channels_impl.SyncHttpClient") as mock_cls:
            mock_cls.return_value.prefix.return_value.success_tpl.return_value.failure_tpl.return_value.post.return_value = resp
            result = channel.send("mark_failed", {"title": "t"})
        assert result.success is False

    def test_network_exception_reports_failure(self):
        channel = _webhook_channel()
        with patch("app.utils.notifier.channels_impl.SyncHttpClient") as mock_cls:
            mock_cls.return_value.prefix.return_value.success_tpl.return_value.failure_tpl.return_value.post.side_effect = RuntimeError(
                "network down"
            )
            result = channel.send("mark_failed", {"title": "t"})
        assert result.success is False
        assert "network down" in result.message

    def test_missing_url_skips(self):
        channel = WebhookChannel("notify-webhook-1", {"enabled": True})
        result = channel.send("mark_failed", {"title": "t"})
        assert result.success is False


# ─────────────────────────────────────────────────────────────────────────
# EmailChannel 传输分支
# ─────────────────────────────────────────────────────────────────────────


def _email_channel(**overrides) -> EmailChannel:
    cfg = {
        "smtp_server": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "u@example.com",
        "smtp_password": "pw",
        "smtp_use_tls": True,
        "email_to": "dest@example.com",
        "enabled": True,
    }
    cfg.update(overrides)
    return EmailChannel("notify-email-1", cfg)


class TestEmailChannelSend:
    def test_missing_recipient_reports_failure(self):
        channel = _email_channel(email_to="")
        result = channel.send("mark_failed", {}, {"subject": "s", "body": "b"})
        assert result.success is False
        assert "收件人" in result.message

    def test_starttls_success(self):
        channel = _email_channel(smtp_port=587)
        with patch("app.utils.notifier.channels_impl.smtplib.SMTP") as mock_smtp:
            server = mock_smtp.return_value
            result = channel.send(
                "mark_failed", {}, {"subject": "s", "body": "b", "html": "<p>x</p>"}
            )
        assert result.success is True
        server.starttls.assert_called_once()
        server.send_message.assert_called_once()
        server.quit.assert_called_once()

    def test_ssl_used_for_port_465(self):
        channel = _email_channel(smtp_port=465)
        with patch("app.utils.notifier.channels_impl.smtplib.SMTP_SSL") as mock_ssl:
            result = channel.send("mark_failed", {}, {"subject": "s", "body": "b"})
        assert result.success is True
        mock_ssl.assert_called_once()

    def test_auth_error_reports_failure(self):
        import smtplib

        channel = _email_channel()
        with patch("app.utils.notifier.channels_impl.smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(
                535, b"bad credentials"
            )
            result = channel.send("mark_failed", {}, {"subject": "s", "body": "b"})
        assert result.success is False

    def test_smtp_exception_reports_failure(self):
        import smtplib

        channel = _email_channel()
        with patch("app.utils.notifier.channels_impl.smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.send_message.side_effect = smtplib.SMTPException(
                "send failed"
            )
            result = channel.send("mark_failed", {}, {"subject": "s", "body": "b"})
        assert result.success is False

    def test_uses_config_template_name(self):
        """template 字段存模板名时，经 render_email 按名查找"""
        mgr = NotificationTemplateManager()
        rendered = mgr.render_email(
            {"notification_type": "mark_failed", "type_display_name": "同步失败"}
        )
        assert "subject" in rendered


# ─────────────────────────────────────────────────────────────────────────
# watching_summary 回归保护（Issue #245）
# ─────────────────────────────────────────────────────────────────────────


class TestWatchingSummaryRegression:
    def _data(self, **overrides):
        data = {
            "notification_type": "watching_summary_每日总结",
            "type_display_name": "追番总结",
            "type_icon": "📊",
            "timestamp": "2026-01-01 00:00:00",
            "user_name": "tester",
            "title": "unknown",
            "season": 0,
            "episode": 0,
            "source": "summary",
            "job_name": "每日总结",
            "summary_text": "本周更新 8 集",
            "date_range": "2026-01-01 ~ 2026-01-07",
            "record_count": 8,
        }
        data.update(overrides)
        return data

    def test_summary_text_reaches_email(self):
        """总结正文必须出现在渲染后的邮件中（此前无覆盖）"""
        svc = NotificationService()
        channel = EmailChannel(
            "notify-email-1",
            {"smtp_server": "s", "email_to": "d@e.com", "enabled": True},
        )
        rendered = svc._render_for_channel(
            channel, "watching_summary_每日总结", self._data()
        )
        assert "本周更新 8 集" in (rendered.get("body") or "")
        assert "本周更新 8 集" in (rendered.get("html") or "")

    def test_summary_text_reaches_webhook(self):
        svc = NotificationService()
        channel = WebhookChannel(
            "notify-webhook-1", {"url": "https://x", "enabled": True}
        )
        rendered = svc._render_for_channel(
            channel, "watching_summary_每日总结", self._data()
        )
        assert rendered["payload"]["summary"] == "本周更新 8 集"

    def test_job_name_in_title(self):
        """多个总结任务靠标题里的 job_name 区分"""
        svc = NotificationService()
        channel = WebhookChannel(
            "notify-webhook-1", {"url": "https://x", "enabled": True}
        )
        rendered = svc._render_for_channel(
            channel, "watching_summary_每日总结", self._data()
        )
        assert "每日总结" in rendered["payload"]["title"]


# ─────────────────────────────────────────────────────────────────────────
# 渠道级开关
# ─────────────────────────────────────────────────────────────────────────


class _RecordingChannel(WebhookChannel):
    def __init__(self, channel_id, enabled=True):
        super().__init__(channel_id, {"url": "https://x", "enabled": enabled})
        self.calls: list[str] = []

    def send(self, notification_type, payload, rendered=None):
        self.calls.append(notification_type)
        return ChannelSendResult(
            success=True, channel_id=self.channel_id, channel_name=self.channel_label
        )


class TestChannelEnabledSuppression:
    def _rules_cfg(self, channels: str):
        cfg = MagicMock()
        cfg.get_config_parser.return_value.sections.return_value = ["notify-rule-1"]
        cfg.get_section.return_value = {
            "enabled": True,
            "types": "all",
            "channels": channels,
        }
        return cfg

    def test_disabled_channel_not_called(self):
        """渠道 enabled=False 时规则不再投递该渠道（新架构此前无覆盖）"""
        chan = _RecordingChannel("notify-webhook-1", enabled=False)
        svc = NotificationService(channel_registry=ChannelRegistry())
        svc.channel_registry.register(chan)
        svc.cooldown.cooldown_seconds = 0

        cfg = self._rules_cfg("notify-webhook-1")
        with (
            patch("app.core.config.config_manager", cfg),
            patch.object(NotificationService, "_lazy_load_channels", lambda self: None),
            patch.object(
                NotificationService, "_render_for_channel", return_value={"payload": {}}
            ),
        ):
            svc.notify("mark_failed", source="t")
        assert chan.calls == []

    def test_enabled_channel_called(self):
        chan = _RecordingChannel("notify-webhook-1", enabled=True)
        svc = NotificationService(channel_registry=ChannelRegistry())
        svc.channel_registry.register(chan)
        svc.cooldown.cooldown_seconds = 0

        cfg = self._rules_cfg("notify-webhook-1")
        with (
            patch("app.core.config.config_manager", cfg),
            patch.object(NotificationService, "_lazy_load_channels", lambda self: None),
            patch.object(
                NotificationService, "_render_for_channel", return_value={"payload": {}}
            ),
        ):
            svc.notify("mark_failed", source="t")
        assert chan.calls == ["mark_failed"]


@pytest.mark.parametrize("port,tls", [(587, True), (587, False), (25, False)])
def test_email_port_tls_matrix(port, tls):
    """不同端口/TLS 组合都应能走通发送流程"""
    channel = _email_channel(smtp_port=port, smtp_use_tls=tls)
    with patch("app.utils.notifier.channels_impl.smtplib.SMTP") as mock_smtp:
        result = channel.send("mark_failed", {}, {"subject": "s", "body": "b"})
    assert result.success is True
    if tls:
        mock_smtp.return_value.starttls.assert_called_once()
    else:
        mock_smtp.return_value.starttls.assert_not_called()
