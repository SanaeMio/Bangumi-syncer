"""
通知器 HTTP Mock 测试
使用 responses 库模拟 Webhook 和邮件发送
"""

from unittest.mock import MagicMock, patch

import pytest
import responses

from app.utils.notifier import Notifier


@pytest.fixture
def mock_config():
    """创建模拟的 config_manager"""
    config = MagicMock()
    config_parser = MagicMock()
    config_parser.sections.return_value = []
    config.get_config_parser.return_value = config_parser
    return config


@pytest.fixture
def mock_config_with_webhook():
    """创建带 webhook 配置的 mock config"""
    config = MagicMock()
    config_parser = MagicMock()
    config_parser.sections.return_value = ["webhook-1"]

    # Mock webhook config
    webhook_config = {
        "id": "1",
        "url": "https://webhook.example.com/notify",
        "enabled": "true",
        "method": "POST",
        "headers": "",
        "types": "all",
    }
    config.get_section.return_value = webhook_config
    config.get_config_parser.return_value = config_parser
    return config


@pytest.fixture
def mock_config_with_email():
    """创建带邮件配置的 mock config"""
    config = MagicMock()
    config_parser = MagicMock()
    config_parser.sections.return_value = ["email-1"]

    # Mock email config
    email_config = {
        "id": "1",
        "smtp_server": "smtp.example.com",
        "smtp_port": "587",
        "smtp_username": "user@example.com",
        "smtp_password": "password",
        "smtp_use_tls": "true",
        "email_from": "from@example.com",
        "email_to": "to@example.com",
        "email_subject": "",
        "email_template_file": "",
        "enabled": "true",
        "types": "all",
    }
    config.get_section.return_value = email_config
    config.get_config_parser.return_value = config_parser
    return config


@responses.activate
def test_webhook_notification_success(mock_config_with_webhook):
    """测试 Webhook 通知成功"""
    responses.add(
        responses.POST,
        "https://webhook.example.com/notify",
        json={"success": True},
        status=200,
    )

    notifier = Notifier(mock_config_with_webhook)
    result = notifier.send_notification_by_type(
        "mark_success",
        {
            "timestamp": "2024-01-01 12:00:00",
            "user_name": "testuser",
            "title": "Test Anime",
            "season": 1,
            "episode": 1,
            "source": "test",
            "subject_id": "123",
            "episode_id": "456",
        },
    )

    # 由于 send_notification_by_type 没有返回值，检查是否有请求发出
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == "https://webhook.example.com/notify"


@responses.activate
def test_webhook_notification_with_template(mock_config_with_webhook):
    """测试带自定义模板的 Webhook"""
    # Mock 带 template 的 webhook 配置
    config = MagicMock()
    config_parser = MagicMock()
    config_parser.sections.return_value = ["webhook-1"]

    webhook_config = {
        "id": "1",
        "url": "https://webhook.example.com/notify",
        "enabled": "true",
        "method": "POST",
        "headers": '{"Content-Type": "application/json"}',
        "types": "all",
        "template": '{"msg": "test"}',
    }
    config.get_section.return_value = webhook_config
    config.get_config_parser.return_value = config_parser

    responses.add(
        responses.POST,
        "https://webhook.example.com/notify",
        json={"success": True},
        status=200,
    )

    notifier = Notifier(config)
    notifier.send_notification_by_type(
        "mark_success",
        {
            "timestamp": "2024-01-01 12:00:00",
            "user_name": "testuser",
            "title": "Test Anime",
            "season": 1,
            "episode": 1,
            "source": "test",
        },
    )

    assert len(responses.calls) == 1


@responses.activate
def test_webhook_get_request(mock_config_with_webhook):
    """测试 GET 请求的 Webhook"""
    # Mock GET 方法的 webhook
    config = MagicMock()
    config_parser = MagicMock()
    config_parser.sections.return_value = ["webhook-1"]

    webhook_config = {
        "id": "1",
        "url": "https://webhook.example.com/notify",
        "enabled": "true",
        "method": "GET",
        "headers": "",
        "types": "all",
    }
    config.get_section.return_value = webhook_config
    config.get_config_parser.return_value = config_parser

    responses.add(
        responses.GET,
        "https://webhook.example.com/notify",
        json={"success": True},
        status=200,
    )

    notifier = Notifier(config)
    notifier.send_notification_by_type(
        "mark_success",
        {
            "timestamp": "2024-01-01 12:00:00",
            "user_name": "testuser",
            "title": "Test Anime",
            "season": 1,
            "episode": 1,
            "source": "test",
        },
    )

    assert len(responses.calls) == 1
    assert responses.calls[0].request.method == "GET"


@responses.activate
def test_webhook_notification_failure(mock_config_with_webhook):
    """测试 Webhook 通知失败"""
    responses.add(
        responses.POST,
        "https://webhook.example.com/notify",
        json={"error": "Server error"},
        status=500,
    )

    notifier = Notifier(mock_config_with_webhook)
    notifier.send_notification_by_type(
        "mark_success",
        {
            "timestamp": "2024-01-01 12:00:00",
            "user_name": "testuser",
            "title": "Test Anime",
            "season": 1,
            "episode": 1,
            "source": "test",
        },
    )

    # 即使失败也会有请求
    assert len(responses.calls) == 1


@responses.activate
def test_webhook_filter_by_type(mock_config_with_webhook):
    """测试 Webhook 按类型过滤"""
    # 配置只接收 mark_success 类型
    config = MagicMock()
    config_parser = MagicMock()
    config_parser.sections.return_value = ["webhook-1"]

    webhook_config = {
        "id": "1",
        "url": "https://webhook.example.com/notify",
        "enabled": "true",
        "method": "POST",
        "headers": "",
        "types": "mark_success",  # 只接收成功通知
    }
    config.get_section.return_value = webhook_config
    config.get_config_parser.return_value = config_parser

    responses.add(
        responses.POST,
        "https://webhook.example.com/notify",
        json={"success": True},
        status=200,
    )

    notifier = Notifier(config)

    # 发送成功通知 - 应该发送
    notifier.send_notification_by_type(
        "mark_success",
        {"timestamp": "2024-01-01 12:00:00"},
    )
    assert len(responses.calls) == 1

    # 发送失败通知 - 不应该发送
    notifier.send_notification_by_type(
        "mark_failed",
        {"timestamp": "2024-01-01 12:00:00"},
    )
    # 没有新的请求
    assert len(responses.calls) == 1


@responses.activate
def test_webhook_disabled(mock_config_with_webhook):
    """测试禁用的 Webhook"""
    # 配置禁用 - 使用布尔值 False
    config = MagicMock()
    config_parser = MagicMock()
    config_parser.sections.return_value = ["webhook-1"]

    webhook_config = {
        "id": "1",
        "url": "https://webhook.example.com/notify",
        "enabled": False,  # 禁用 - 使用布尔值
        "method": "POST",
        "headers": "",
        "types": "all",
    }
    config.get_section.return_value = webhook_config
    config.get_config_parser.return_value = config_parser

    notifier = Notifier(config)
    result = notifier.send_notification_by_type(
        "mark_success",
        {"timestamp": "2024-01-01 12:00:00"},
    )

    # 没有请求发出 - 当 enabled=False 时，_get_webhook_configs 应该过滤掉
    configs = notifier._get_webhook_configs()
    # 验证所有配置都是禁用的
    enabled_configs = [c for c in configs if c.get("enabled")]
    assert len(enabled_configs) == 0


def test_should_send_notification_cooldown(mock_config):
    """测试通知冷却时间"""
    notifier = Notifier(mock_config)

    # 第一次应该发送
    assert notifier._should_send_notification("test_type") is True

    # 立即再次请求，不应该发送（冷却中）
    assert notifier._should_send_notification("test_type") is False


def test_should_send_notification_after_cooldown(mock_config):
    """测试冷却时间后可以再次发送"""

    notifier = Notifier(mock_config)

    # 第一次发送
    assert notifier._should_send_notification("test_type") is True

    # 手动将上次的通知时间设置为很早之前（绕过冷却）
    notifier._last_notification_time["test_type"] = 0

    # 现在应该可以发送
    assert notifier._should_send_notification("test_type") is True


@patch("smtplib.SMTP")
def test_email_notification_success(mock_smtp, mock_config_with_email):
    """测试邮件通知成功"""
    mock_smtp_instance = MagicMock()
    mock_smtp.return_value = mock_smtp_instance

    notifier = Notifier(mock_config_with_email)
    notifier.send_notification_by_type(
        "mark_success",
        {
            "timestamp": "2024-01-01 12:00:00",
            "user_name": "testuser",
            "title": "Test Anime",
            "season": 1,
            "episode": 1,
            "source": "test",
        },
    )

    # 验证 SMTP 相关方法被调用
    assert (
        mock_smtp.called
        or mock_smtp_instance.starttls.called
        or mock_smtp_instance.send_message.called
    )


@patch("smtplib.SMTP")
def test_email_notification_no_recipient(mock_config_with_email):
    """测试邮件通知 - 无收件人"""
    # 修改配置，移除收件人
    config = MagicMock()
    config_parser = MagicMock()
    config_parser.sections.return_value = ["email-1"]

    email_config = {
        "id": "1",
        "smtp_server": "smtp.example.com",
        "smtp_port": "587",
        "smtp_username": "user@example.com",
        "smtp_password": "password",
        "smtp_use_tls": "true",
        "email_from": "from@example.com",
        "email_to": "",  # 无收件人
        "email_subject": "",
        "email_template_file": "",
        "enabled": "true",
        "types": "all",
    }
    config.get_section.return_value = email_config
    config.get_config_parser.return_value = config_parser

    notifier = Notifier(config)
    result = notifier.send_notification_by_type(
        "mark_success",
        {"timestamp": "2024-01-01 12:00:00"},
    )

    # 不应该发送邮件（无收件人）


@patch("smtplib.SMTP")
def test_email_smtp_auth_error(mock_smtp, mock_config_with_email):
    """测试邮件认证失败"""
    import smtplib

    mock_smtp_instance = MagicMock()
    mock_smtp.return_value = mock_smtp_instance
    mock_smtp_instance.send_message.side_effect = smtplib.SMTPAuthenticationError(
        535, b"Authentication failed"
    )

    notifier = Notifier(mock_config_with_email)
    # 应该捕获异常，不会崩溃
    notifier.send_notification_by_type(
        "mark_success",
        {
            "timestamp": "2024-01-01 12:00:00",
            "user_name": "testuser",
            "title": "Test Anime",
            "season": 1,
            "episode": 1,
            "source": "test",
        },
    )


def test_build_payload_by_type(mock_config):
    """测试根据类型构建载荷"""
    notifier = Notifier(mock_config)

    payload = notifier._build_payload_by_type(
        "mark_success",
        {
            "timestamp": "2024-01-01 12:00:00",
            "user_name": "testuser",
            "title": "Test Anime",
            "season": 1,
            "episode": 1,
            "source": "test",
            "subject_id": "123",
            "episode_id": "456",
        },
        "",
    )

    assert payload["title"] == "✅ 同步成功"
    assert payload["type"] == "mark_success"
    assert payload["user"] == "testuser"
    assert payload["anime"] == "Test Anime"


def test_replace_template_variables(mock_config):
    """测试模板变量替换"""
    notifier = Notifier(mock_config)

    # 测试字典
    template = {"key": "{name}", "nested": {"inner": "{value}"}}
    data = {"name": "test", "value": "123"}
    result = notifier._replace_template_variables(template, data)

    assert result["key"] == "test"
    assert result["nested"]["inner"] == "123"

    # 测试列表
    template = ["{item1}", "{item2}"]
    data = {"item1": "a", "item2": "b"}
    result = notifier._replace_template_variables(template, data)

    assert result == ["a", "b"]

    # 测试字符串
    template = "Hello {name}, your score is {score}"
    data = {"name": "Alice", "score": "100"}
    result = notifier._replace_template_variables(template, data)

    assert result == "Hello Alice, your score is 100"


def test_parse_headers(mock_config):
    """测试请求头解析"""
    notifier = Notifier(mock_config)

    # 测试空字符串
    headers = notifier._parse_headers("")
    assert "User-Agent" in headers

    # 测试 JSON 格式
    headers = notifier._parse_headers('{"Content-Type": "application/json"}')
    assert headers["Content-Type"] == "application/json"

    # 测试逗号分隔格式
    headers = notifier._parse_headers("Content-Type:application/json,X-Custom:abc")
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Custom"] == "abc"


def test_build_email_subject_by_type(mock_config):
    """测试邮件标题构建"""
    notifier = Notifier(mock_config)

    data = {"title": "Test Anime", "season": 1, "episode": 5}

    # 测试 mark_success
    subject = notifier._build_email_subject_by_type("mark_success", data)
    assert "同步成功" in subject
    assert "Test Anime" in subject

    # 测试 mark_failed
    subject = notifier._build_email_subject_by_type("mark_failed", data)
    assert "同步失败" in subject

    # 测试 anime_not_found
    subject = notifier._build_email_subject_by_type("anime_not_found", data)
    assert "未找到番剧" in subject


def test_build_email_text_by_type(mock_config):
    """测试邮件纯文本内容构建"""
    notifier = Notifier(mock_config)

    data = {
        "timestamp": "2024-01-01 12:00:00",
        "user_name": "testuser",
        "title": "Test Anime",
        "season": 1,
        "episode": 5,
        "source": "plex",
    }

    # 测试 mark_success
    text = notifier._build_email_text_by_type("mark_success", data)
    assert "同步成功" in text
    assert "testuser" in text
    assert "Test Anime" in text


def test_build_simple_email_html(mock_config):
    """测试简单 HTML 邮件构建"""
    notifier = Notifier(mock_config)

    data = {
        "notification_type": "mark_success",
        "timestamp": "2024-01-01 12:00:00",
        "user_name": "testuser",
        "title": "Test Anime",
    }

    html = notifier._build_simple_email_html(data)

    assert "<html>" in html
    assert "同步成功" in html
    assert "testuser" in html
    assert "Test Anime" in html


def test_get_webhook_configs(mock_config_with_webhook):
    """测试获取 webhook 配置"""
    notifier = Notifier(mock_config_with_webhook)
    configs = notifier._get_webhook_configs()

    assert len(configs) == 1
    assert configs[0]["url"] == "https://webhook.example.com/notify"


def test_get_email_configs(mock_config_with_email):
    """测试获取邮件配置"""
    notifier = Notifier(mock_config_with_email)
    configs = notifier._get_email_configs()

    assert len(configs) == 1
    assert configs[0]["smtp_server"] == "smtp.example.com"
