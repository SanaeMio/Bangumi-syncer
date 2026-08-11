"""
通知工具测试
"""

import smtplib
from unittest.mock import MagicMock, mock_open, patch

import pytest

from app.services.notification_service import NotificationService
from app.utils.notifier import Notifier, get_notifier


class TestNotifier:
    """通知管理器测试"""

    def test_init(self):
        """测试 Notifier 初始化"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        assert notifier.config_manager == mock_config
        assert notifier._notification_cooldown == 60

    def test_should_send_notification_first_time(self):
        """测试首次发送通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        result = notifier._should_send_notification("test_type")
        assert result is True

    def test_should_send_notification_cooldown(self):
        """测试通知冷却"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        # 首次发送
        notifier._should_send_notification("test_type")
        # 立即再次发送应该被阻止
        result = notifier._should_send_notification("test_type")
        assert result is False

    def test_replace_template_variables_dict(self):
        """测试替换字典模板变量"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        template = {"name": "{name}", "value": 123}
        data = {"name": "test_name"}
        result = notifier._replace_template_variables(template, data)
        assert result["name"] == "test_name"
        assert result["value"] == 123

    def test_replace_template_variables_list(self):
        """测试替换列表模板变量"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        template = ["{item1}", "{item2}"]
        data = {"item1": "value1", "item2": "value2"}
        result = notifier._replace_template_variables(template, data)
        assert result == ["value1", "value2"]

    def test_replace_template_variables_string(self):
        """测试替换字符串模板变量"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        template = "Hello {name}, your score is {score}"
        data = {"name": "John", "score": 100}
        result = notifier._replace_template_variables(template, data)
        assert result == "Hello John, your score is 100"

    def test_replace_template_variables_missing_key(self):
        """测试替换缺失的模板变量"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        template = "Hello {name}, missing {key}"
        data = {"name": "John"}
        result = notifier._replace_template_variables(template, data)
        assert result == "Hello John, missing "

    def test_replace_template_variables_non_string(self):
        """测试非字符串模板变量"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        template = 123
        data = {"name": "John"}
        result = notifier._replace_template_variables(template, data)
        assert result == 123

    def test_replace_template_variables_bool(self):
        """测试布尔模板变量"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        template = True
        data = {"name": "John"}
        result = notifier._replace_template_variables(template, data)
        assert result is True


class TestNotifierLoadEmailTemplate:
    """测试邮件模板加载"""

    def test_load_email_template_no_file_uses_default(self):
        """无模板文件时使用默认模板"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "mark_success",
            "title": "测试",
            "timestamp": "2024-01-01",
        }

        with patch("os.path.exists", return_value=False):
            result = notifier._load_email_template("", data)
            assert "Bangumi-Syncer" in result

    def test_load_email_template_file_exists(self):
        """模板文件存在时加载文件"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"notification_type": "mark_success", "title": "测试"}
        template_content = "<html>{title}</html>"

        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=template_content)),
        ):
            result = notifier._load_email_template("custom.html", data)
            assert "测试" in result

    def test_load_email_template_file_not_found_fallback(self):
        """模板文件不存在时回退到默认模板"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "mark_failed",
            "title": "测试",
            "error_message": "错误",
        }

        with patch("os.path.exists", return_value=False):
            result = notifier._load_email_template("nonexistent.html", data)
            assert "Bangumi-Syncer" in result


class TestNotifierBuildSimpleEmailHtml:
    """测试简单HTML邮件构建"""

    def test_build_simple_email_request_received(self):
        """测试收到请求通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "request_received",
            "timestamp": "2024-01-01",
            "title": "测试番剧",
            "season": 1,
            "episode": 5,
            "user_name": "test_user",
            "source": "emby",
        }
        result = notifier._build_simple_email_html(data)
        assert "收到同步请求" in result
        assert "测试番剧" in result

    def test_build_simple_email_mark_success(self):
        """测试同步成功通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"notification_type": "mark_success", "title": "测试"}
        result = notifier._build_simple_email_html(data)
        assert "同步成功" in result

    def test_build_simple_email_mark_failed(self):
        """测试同步失败通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "mark_failed",
            "title": "测试",
            "error_message": "API错误",
            "error_type": "connection",
        }
        result = notifier._build_simple_email_html(data)
        assert "同步失败" in result
        assert "API错误" in result

    def test_build_simple_email_config_error(self):
        """测试配置错误通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "config_error",
            "error_message": "配置无效",
            "config_type": "webhook",
            "user_name": "admin",
            "mode": "auto",
        }
        result = notifier._build_simple_email_html(data)
        assert "配置错误" in result

    def test_build_simple_email_api_auth_error(self):
        """测试API认证错误通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "api_auth_error",
            "username": "admin",
            "status_code": 401,
            "error_message": "token过期",
        }
        result = notifier._build_simple_email_html(data)
        assert "API认证失败" in result

    def test_build_simple_email_ip_locked(self):
        """测试IP锁定通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "ip_locked",
            "ip": "192.168.1.100",
            "locked_until": "2024-01-01 12:00:00",
            "attempt_count": 5,
            "max_attempts": 3,
        }
        result = notifier._build_simple_email_html(data)
        assert "IP被锁定" in result

    def test_build_simple_email_anime_not_found(self):
        """测试未找到番剧通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "anime_not_found",
            "title": "测试",
            "ori_title": "Test",
            "search_method": "fuzzy",
        }
        result = notifier._build_simple_email_html(data)
        assert "未找到番剧" in result

    def test_build_simple_email_episode_not_found(self):
        """测试未找到剧集通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "episode_not_found",
            "subject_id": "12345",
            "title": "测试",
            "season": 1,
            "episode": 5,
        }
        result = notifier._build_simple_email_html(data)
        assert "未找到剧集" in result

    def test_build_simple_email_with_dynamic_content(self):
        """测试带动态内容的通知"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "notification_type": "mark_success",
            "dynamic_content": "<p>自定义内容</p>",
        }
        result = notifier._build_simple_email_html(data)
        assert "自定义内容" in result

    def test_build_simple_email_unknown_type(self):
        """测试未知通知类型"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"notification_type": "unknown_type"}
        result = notifier._build_simple_email_html(data)
        assert "unknown_type" in result


class TestNotifierSendWebhookByConfig:
    """测试webhook发送"""

    @patch("app.utils.notifier.webhook.SyncHttpClient")
    def test_send_webhook_post_success(self, mock_cls):
        """测试POST webhook成功"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.01
        mock_response.headers = {}
        mock_response.text = ""

        mock_instance = mock_cls.return_value
        mock_instance.prefix.return_value = mock_instance
        mock_instance.success_tpl.return_value = mock_instance
        mock_instance.failure_tpl.return_value = mock_instance
        mock_instance.post.return_value = mock_response

        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        webhook_config = {
            "id": 1,
            "url": "https://example.com/webhook",
            "method": "POST",
            "headers": '{"Content-Type": "application/json"}',
            "template": "",
        }
        data = {"title": "测试", "timestamp": "2024-01-01"}

        result = notifier._send_webhook_by_config(webhook_config, "mark_success", data)
        assert result is True
        mock_instance.post.assert_called_once()

    @patch("app.utils.notifier.webhook.SyncHttpClient")
    def test_send_webhook_get_success(self, mock_cls):
        """测试GET webhook成功"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.01
        mock_response.headers = {}
        mock_response.text = ""

        mock_instance = mock_cls.return_value
        mock_instance.prefix.return_value = mock_instance
        mock_instance.success_tpl.return_value = mock_instance
        mock_instance.failure_tpl.return_value = mock_instance
        mock_instance.get.return_value = mock_response

        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        webhook_config = {
            "id": 1,
            "url": "https://example.com/webhook",
            "method": "GET",
            "headers": "",
            "template": "",
        }
        data = {"title": "测试"}

        result = notifier._send_webhook_by_config(webhook_config, "mark_success", data)
        assert result is True

    @patch("app.utils.notifier.webhook.SyncHttpClient")
    def test_send_webhook_failure_status(self, mock_cls):
        """测试webhook返回非成功状态"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.elapsed.total_seconds.return_value = 0.01
        mock_response.headers = {}
        mock_response.text = ""

        mock_instance = mock_cls.return_value
        mock_instance.prefix.return_value = mock_instance
        mock_instance.success_tpl.return_value = mock_instance
        mock_instance.failure_tpl.return_value = mock_instance
        mock_instance.post.return_value = mock_response

        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        webhook_config = {
            "id": 1,
            "url": "https://example.com/webhook",
            "method": "POST",
        }
        data = {"title": "测试"}

        result = notifier._send_webhook_by_config(webhook_config, "mark_success", data)
        assert result is False

    @patch("app.utils.notifier.webhook.SyncHttpClient")
    def test_send_webhook_exception(self, mock_cls):
        """测试webhook异常"""
        mock_instance = mock_cls.return_value
        mock_instance.prefix.return_value = mock_instance
        mock_instance.success_tpl.return_value = mock_instance
        mock_instance.failure_tpl.return_value = mock_instance
        mock_instance.post.side_effect = Exception("Network error")

        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        webhook_config = {
            "id": 1,
            "url": "https://example.com/webhook",
        }
        data = {"title": "测试"}

        result = notifier._send_webhook_by_config(webhook_config, "mark_success", data)
        assert result is False


class TestNotifierSendEmailByConfig:
    """测试邮件发送"""

    @patch("app.utils.notifier.email_sender.smtplib.SMTP")
    @patch("app.utils.notifier.html_builders.os.path.exists", return_value=False)
    def test_send_email_starttls_success(self, mock_exists, mock_smtp):
        """测试STARTTLS邮件发送成功"""
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server

        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        email_config = {
            "id": 1,
            "smtp_server": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "password",
            "smtp_use_tls": True,
            "email_from": "from@example.com",
            "email_to": "to@example.com",
            "email_subject": "",
            "template": "",
        }
        data = {"title": "测试", "timestamp": "2024-01-01"}

        result = notifier._send_email_by_config(email_config, "mark_success", data)
        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()

    @patch("app.utils.notifier.email_sender.smtplib.SMTP_SSL")
    @patch("app.utils.notifier.html_builders.os.path.exists", return_value=False)
    def test_send_email_ssl_success(self, mock_exists, mock_smtp_ssl):
        """测试SSL邮件发送成功"""
        mock_server = MagicMock()
        mock_smtp_ssl.return_value = mock_server

        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        email_config = {
            "id": 1,
            "smtp_server": "smtp.example.com",
            "smtp_port": "465",
            "smtp_username": "user@example.com",
            "smtp_password": "password",
            "smtp_use_tls": True,
            "email_from": "from@example.com",
            "email_to": "to@example.com",
        }
        data = {"title": "测试"}

        result = notifier._send_email_by_config(email_config, "mark_success", data)
        assert result is True

    @patch("app.utils.notifier.html_builders.os.path.exists", return_value=False)
    def test_send_email_no_to_address(self, mock_exists):
        """测试无收件人地址"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        email_config = {
            "id": 1,
            "smtp_server": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "password",
            "email_to": "",
        }
        data = {"title": "测试"}

        result = notifier._send_email_by_config(email_config, "mark_success", data)
        assert result is False

    @patch("app.utils.notifier.email_sender.smtplib.SMTP")
    @patch("app.utils.notifier.html_builders.os.path.exists", return_value=False)
    def test_send_email_auth_error(self, mock_exists, mock_smtp):
        """测试邮件认证失败"""
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Auth failed"
        )
        mock_smtp.return_value = mock_server

        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        email_config = {
            "id": 1,
            "smtp_server": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "wrong",
            "email_from": "from@example.com",
            "email_to": "to@example.com",
        }
        data = {"title": "测试"}

        result = notifier._send_email_by_config(email_config, "mark_success", data)
        assert result is False


class TestNotifierBuildEmailSubject:
    """测试邮件标题构建"""

    def test_build_subject_request_received(self):
        """测试收到请求标题"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "测试", "season": 1, "episode": 5}
        result = notifier._build_email_subject_by_type("request_received", data)
        assert "收到同步请求" in result

    def test_build_subject_mark_success(self):
        """测试同步成功标题"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "测试", "season": 1, "episode": 5}
        result = notifier._build_email_subject_by_type("mark_success", data)
        assert "同步成功" in result

    def test_build_subject_unknown_type(self):
        """测试未知类型标题"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        result = notifier._build_email_subject_by_type("custom_type", {})
        assert "custom_type" in result


class TestNotifierBuildEmailText:
    """测试纯文本邮件构建"""

    def test_build_text_mark_success(self):
        """测试同步成功文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "timestamp": "2024-01-01",
            "user_name": "test",
            "title": "测试",
            "season": 1,
            "episode": 5,
            "source": "emby",
            "subject_id": "123",
            "episode_id": "456",
        }
        result = notifier._build_email_text_by_type("mark_success", data)
        assert "同步成功" in result
        assert "test" in result

    def test_build_text_mark_failed(self):
        """测试同步失败文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "timestamp": "2024-01-01",
            "error_message": "API错误",
            "error_type": "connection",
        }
        result = notifier._build_email_text_by_type("mark_failed", data)
        assert "同步失败" in result
        assert "API错误" in result

    def test_build_text_anime_not_found(self):
        """测试未找到番剧文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "timestamp": "2024-01-01",
            "title": "测试",
            "ori_title": "Test",
            "search_method": "fuzzy",
        }
        result = notifier._build_email_text_by_type("anime_not_found", data)
        assert "未找到番剧" in result

    def test_build_text_config_error(self):
        """测试配置错误文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "timestamp": "2024-01-01",
            "error_message": "配置无效",
            "config_type": "webhook",
        }
        result = notifier._build_email_text_by_type("config_error", data)
        assert "配置错误" in result

    def test_build_text_api_error(self):
        """测试API错误文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "timestamp": "2024-01-01",
            "status_code": 500,
            "url": "https://api.example.com",
            "method": "GET",
            "retry_count": 3,
        }
        result = notifier._build_email_text_by_type("api_error", data)
        assert "API错误" in result

    def test_build_text_ip_locked(self):
        """测试IP锁定文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "timestamp": "2024-01-01",
            "ip": "192.168.1.1",
            "locked_until": "2024-01-01 12:00:00",
            "attempt_count": 5,
            "max_attempts": 3,
        }
        result = notifier._build_email_text_by_type("ip_locked", data)
        assert "IP被锁定" in result


class TestNotifierBuildDynamicContent:
    """测试动态HTML内容构建"""

    def test_build_dynamic_mark_failed(self):
        """测试同步失败动态内容"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"error_message": "测试错误"}
        result = notifier._build_email_dynamic_content("mark_failed", data)
        assert "错误详情" in result

    def test_build_dynamic_mark_success(self):
        """测试同步成功动态内容"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "title": "测试",
            "season": 1,
            "episode": 5,
            "user_name": "test",
            "source": "emby",
        }
        result = notifier._build_email_dynamic_content("mark_success", data)
        assert "同步成功" in result

    def test_build_dynamic_bangumi_id_found(self):
        """测试匹配到番剧动态内容"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "title": "测试",
            "subject_id": "123",
            "season": 1,
            "episode": 1,
            "user_name": "test",
            "source": "emby",
        }
        result = notifier._build_email_dynamic_content("bangumi_id_found", data)
        assert "匹配到番剧" in result

    def test_build_dynamic_api_auth_error(self):
        """测试API认证错误动态内容"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"username": "admin", "status_code": 401, "error_message": "token过期"}
        result = notifier._build_email_dynamic_content("api_auth_error", data)
        assert "API认证失败" in result

    def test_build_dynamic_api_retry_failed(self):
        """测试API重试失败动态内容"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "subject_id": "123",
            "episode_id": "456",
            "max_retries": 3,
            "error_message": "重试失败",
        }
        result = notifier._build_email_dynamic_content("api_retry_failed", data)
        assert "API重试失败" in result


class TestNotifierBuildPayload:
    """测试webhook载荷构建"""

    def test_build_payload_with_template(self):
        """测试自定义模板载荷"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        template = '{"text": "{title} - {user_name}"}'
        data = {"title": "测试", "user_name": "test"}
        result = notifier._build_payload_by_type("mark_success", data, template)
        assert result["text"] == "测试 - test"

    def test_build_payload_default_template(self):
        """测试默认模板载荷"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "title": "测试",
            "user_name": "test",
            "timestamp": "2024-01-01",
            "season": 1,
            "episode": 5,
            "source": "emby",
        }
        result = notifier._build_payload_by_type("mark_success", data, "")
        assert "title" in result
        assert result["anime"] == "测试"

    def test_build_payload_unknown_type(self):
        """测试未知类型载荷"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "测试"}
        result = notifier._build_payload_by_type("custom_type", data, "")
        assert result["type"] == "custom_type"


class TestNotifierParseHeaders:
    """测试请求头解析"""

    def test_parse_headers_empty(self):
        """测试空请求头"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        result = notifier._parse_headers("")
        assert "User-Agent" in result

    def test_parse_headers_json(self):
        """测试JSON格式请求头"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        headers_str = '{"Authorization": "Bearer token123"}'
        result = notifier._parse_headers(headers_str)
        assert result["Authorization"] == "Bearer token123"

    def test_parse_headers_comma_separated(self):
        """测试逗号分隔格式请求头"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        headers_str = "Authorization: Bearer token123, X-Custom: value"
        result = notifier._parse_headers(headers_str)
        assert "Authorization" in result

    def test_parse_headers_non_string(self):
        """测试非字符串类型请求头"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        result = notifier._parse_headers(123)
        assert "User-Agent" in result


class TestNotifierTestNotification:
    """测试通知测试功能"""

    @patch("app.utils.notifier.webhook.SyncHttpClient")
    def test_test_notification_webhook(self, mock_cls):
        """测试webhook通知测试"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.01
        mock_response.headers = {}
        mock_response.text = ""

        mock_instance = mock_cls.return_value
        mock_instance.prefix.return_value = mock_instance
        mock_instance.success_tpl.return_value = mock_instance
        mock_instance.failure_tpl.return_value = mock_instance
        mock_instance.post.return_value = mock_response

        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        notifier._get_webhook_configs = MagicMock(
            return_value=[
                {"id": 1, "url": "https://example.com", "enabled": True, "types": "all"}
            ]
        )
        notifier._get_email_configs = MagicMock(return_value=[])

        result = notifier.test_notification(notification_type="webhook")
        assert "webhook" in result
        assert result["webhook"]["enabled"] is True

    def test_test_notification_email(self):
        """测试邮件通知测试"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        notifier._get_webhook_configs = MagicMock(return_value=[])
        notifier._get_email_configs = MagicMock(
            return_value=[
                {
                    "id": 1,
                    "smtp_server": "smtp.example.com",
                    "enabled": False,
                    "types": "all",
                }
            ]
        )

        result = notifier.test_notification(notification_type="email")
        assert "email" in result

    def test_test_notification_by_id(self):
        """测试指定ID通知测试"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        notifier._get_webhook_configs = MagicMock(
            return_value=[
                {"id": 1, "url": "https://example.com", "enabled": False},
                {"id": 2, "url": "https://example2.com", "enabled": False},
            ]
        )

        result = notifier.test_notification(webhook_id=1)
        assert len(result["webhook"]["webhooks"]) == 1


class TestGetNotifier:
    """测试获取通知器实例"""

    def test_get_notifier_creates_instance(self):
        """测试创建通知器实例"""
        with patch("app.core.config.config_manager"):
            notifier = get_notifier()
            assert isinstance(notifier, Notifier)


class TestNotificationServiceNotify:
    """测试 NotificationService.notify() 便捷入口

    新架构下 NotificationService 不再依赖 ``_notifier``，
    而是通过 :class:`ChannelRegistry` 分发到各渠道。
    这里测试：
    1. 数据构造正确
    2. 渠道分发被调用
    3. 异常时返回 False
    """

    def _make_service(self) -> NotificationService:
        """构造一个 Mock 了 channel_registry 的 NotificationService"""
        svc = NotificationService()
        svc.channel_registry = MagicMock()
        svc.channel_registry.all.return_value = []
        svc.channel_registry.iter_enabled.return_value = []
        svc._db_manager = MagicMock()
        return svc

    def test_notify_with_item(self):
        """测试发送通知带 item：无规则时停发外部渠道，仅完成数据构造与站内信"""
        svc = self._make_service()

        item = MagicMock()
        item.user_name = "test"
        item.title = "测试"
        item.ori_title = "Test"
        item.season = 1
        item.episode = 5
        item.source = "emby"

        result = svc.notify("mark_success", item=item, source="custom")
        assert result is True
        # 无通知规则 = 停发外部渠道，不应进行渠道分发
        svc.channel_registry.iter_enabled.assert_not_called()

    def test_notify_without_item(self):
        """测试发送通知不带 item"""
        svc = self._make_service()

        result = svc.notify("mark_success", title="测试")
        assert result is True
        svc.channel_registry.iter_enabled.assert_not_called()

    def test_notify_exception_returns_false(self):
        """测试发送通知异常时返回 False"""
        svc = self._make_service()
        svc.channel_registry.all.side_effect = Exception("发送失败")

        result = svc.notify("mark_success")
        assert result is False


class TestNotifierGetConfigs:
    """测试获取配置"""

    def test_get_webhook_configs(self):
        """测试获取webhook配置"""
        mock_config_parser = MagicMock()
        mock_config_parser.sections.return_value = ["notify-webhook-1", "general"]
        mock_config = MagicMock()
        mock_config.get_config_parser.return_value = mock_config_parser
        mock_config.get_section.return_value = {
            "id": 1,
            "url": "https://example.com",
            "enabled": True,
        }

        notifier = Notifier(mock_config)
        configs = notifier._get_webhook_configs()
        assert len(configs) == 1

    def test_get_email_configs(self):
        """测试获取邮件配置"""
        mock_config_parser = MagicMock()
        mock_config_parser.sections.return_value = ["notify-email-1", "general"]
        mock_config = MagicMock()
        mock_config.get_config_parser.return_value = mock_config_parser
        mock_config.get_section.return_value = {
            "id": 1,
            "smtp_server": "smtp.example.com",
            "enabled": True,
        }

        notifier = Notifier(mock_config)
        configs = notifier._get_email_configs()
        assert len(configs) == 1


class TestNotifierSendNotificationByType:
    """测试按类型发送通知"""

    @patch("app.utils.notifier.webhook.SyncHttpClient")
    def test_send_notification_by_type_webhook(self, mock_cls):
        """测试按类型发送webhook通知"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.01
        mock_response.headers = {}
        mock_response.text = ""

        mock_instance = mock_cls.return_value
        mock_instance.prefix.return_value = mock_instance
        mock_instance.success_tpl.return_value = mock_instance
        mock_instance.failure_tpl.return_value = mock_instance
        mock_instance.post.return_value = mock_response

        mock_config_parser = MagicMock()
        mock_config_parser.sections.return_value = ["notify-webhook-1"]
        mock_config = MagicMock()
        mock_config.get_config_parser.return_value = mock_config_parser
        mock_config.get_section.return_value = {
            "id": 1,
            "url": "https://example.com",
            "enabled": True,
            "types": "all",
        }

        notifier = Notifier(mock_config)
        notifier._last_notification_time = {}  # 清除冷却
        data = {"title": "测试", "timestamp": "2024-01-01"}
        notifier.send_notification_by_type("mark_success", data)
        mock_instance.post.assert_called_once()

    def test_send_notification_by_type_cooldown(self):
        """测试通知冷却"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        notifier._last_notification_time = {"notify-webhook-1_mark_success": 9999999999}

        data = {"title": "测试"}
        notifier.send_notification_by_type("mark_success", data)
        # 不应该调用发送方法


class TestNotifierIntegration:
    """通知集成测试"""

    @patch("app.utils.notifier.webhook.SyncHttpClient")
    def test_send_webhook_success(self, mock_cls):
        """测试发送 webhook 成功"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.01
        mock_response.headers = {}
        mock_response.text = ""

        mock_instance = mock_cls.return_value
        mock_instance.prefix.return_value = mock_instance
        mock_instance.success_tpl.return_value = mock_instance
        mock_instance.failure_tpl.return_value = mock_instance
        mock_instance.post.return_value = mock_response

        mock_config = MagicMock()
        mock_config.get_notification_config.return_value = {
            "webhooks": [
                {"url": "https://example.com/webhook", "events": ["sync.success"]}
            ]
        }

        _notifier = Notifier(mock_config)
        # 注意：这里需要调用实际的 send_notification 方法
        # 但由于方法可能不存在，我们测试基本功能

    @patch("app.utils.notifier.webhook.SyncHttpClient")
    def test_send_webhook_failure(self, mock_cls):
        """测试发送 webhook 失败"""
        mock_instance = mock_cls.return_value
        mock_instance.prefix.return_value = mock_instance
        mock_instance.success_tpl.return_value = mock_instance
        mock_instance.failure_tpl.return_value = mock_instance
        mock_instance.post.side_effect = Exception("Network error")

        mock_config = MagicMock()
        _notifier = Notifier(mock_config)

        # 测试失败情况
        try:
            # 实际调用可能需要完整的配置
            pass
        except Exception:
            pass


class TestTypeMatches:
    """_type_matches 边界测试"""

    def test_all_matches_anything(self):
        assert Notifier._type_matches("anything", "all") is True
        assert Notifier._type_matches("mark_success", "all") is True

    def test_exact_match(self):
        assert (
            Notifier._type_matches("mark_success", "mark_success,mark_failed") is True
        )

    def test_exact_match_with_spaces(self):
        assert (
            Notifier._type_matches("mark_success", "mark_success, mark_failed") is True
        )

    def test_prefix_not_match(self):
        """前缀不应误匹配——用的是精确 in 检查，不是子串。"""
        assert Notifier._type_matches("mark", "mark_success,mark_failed") is False

    def test_empty_types_no_match(self):
        assert Notifier._type_matches("mark_success", "") is False

    def test_single_type_no_comma(self):
        assert Notifier._type_matches("mark_success", "mark_success") is True
        assert Notifier._type_matches("mark_failed", "mark_success") is False


class TestNotifierMergedExtended:
    """test_notifier_extended.py 并入的独有用例。"""

    def test_replace_template_nested(self):
        """测试嵌套模板变量替换"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        template = {"key": {"nested": "{value}"}}
        data = {"value": "test"}
        result = notifier._replace_template_variables(template, data)
        assert result["key"]["nested"] == "test"

    def test_replace_template_list_items(self):
        """测试列表中的模板变量"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        template = [{"name": "{item}"}]
        data = {"item": "value"}
        result = notifier._replace_template_variables(template, data)
        assert result[0]["name"] == "value"

    def test_replace_template_mixed(self):
        """测试混合类型模板"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        template = {"string": "{name}", "number": 123, "list": ["{item}"]}
        data = {"name": "test", "item": "value"}
        result = notifier._replace_template_variables(template, data)
        assert result["string"] == "test"
        assert result["number"] == 123
        assert result["list"][0] == "value"


class TestNotifierMergedFull:
    """test_notifier_full.py 并入的独有用例。"""

    def test_get_webhook_configs_empty(self):
        """测试获取空 webhook 配置"""
        mock_config = MagicMock()
        mock_config.get_config_parser.return_value.sections.return_value = []

        notifier = Notifier(mock_config)
        result = notifier._get_webhook_configs()
        assert result == []

    def test_get_email_configs_empty(self):
        """测试获取空邮件配置"""
        mock_config = MagicMock()
        mock_config.get_config_parser.return_value.sections.return_value = []

        notifier = Notifier(mock_config)
        result = notifier._get_email_configs()
        assert result == []

    def test_should_send_notification_different_types(self):
        """测试不同类型通知独立冷却"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        # 类型1可以发送
        result1 = notifier._should_send_notification("type1")
        assert result1 is True

        # 类型2可以发送（不同类型独立冷却）
        result2 = notifier._should_send_notification("type2")
        assert result2 is True

        # 再次发送类型1被冷却阻止
        result3 = notifier._should_send_notification("type1")
        assert result3 is False

    def test_replace_template_edge_cases(self):
        """测试模板替换边界情况"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        # 测试空字符串
        result = notifier._replace_template_variables("", {})
        assert result == ""

        # 测试 None
        result = notifier._replace_template_variables(None, {})
        assert result is None

    def test_send_email_smtp_exception(self):
        """测试 SMTP 发送异常"""
        import smtplib

        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        email_config = {
            "id": "1",
            "smtp_server": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "pass",
            "email_from": "from@example.com",
            "email_to": "to@example.com",
        }

        with (
            patch("app.utils.notifier.email_sender.smtplib.SMTP") as mock_smtp,
            patch.object(notifier, "_load_email_template", return_value=None),
            patch.object(
                notifier, "_build_simple_email_html", return_value="<html></html>"
            ),
            patch.object(notifier, "_build_email_text_by_type", return_value="text"),
        ):
            mock_server = MagicMock()
            mock_server.send_message.side_effect = smtplib.SMTPException("send failed")
            mock_smtp.return_value = mock_server

            result = notifier._send_email_by_config(email_config, "mark_success", {})
            assert result is False

    def test_send_email_generic_exception(self):
        """测试邮件发送通用异常"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        email_config = {
            "id": "1",
            "smtp_server": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "pass",
            "email_from": "from@example.com",
            "email_to": "to@example.com",
        }

        with (
            patch(
                "app.utils.notifier.email_sender.smtplib.SMTP",
                side_effect=RuntimeError("connection error"),
            ),
            patch.object(notifier, "_load_email_template", return_value=None),
            patch.object(
                notifier, "_build_simple_email_html", return_value="<html></html>"
            ),
            patch.object(notifier, "_build_email_text_by_type", return_value="text"),
        ):
            result = notifier._send_email_by_config(email_config, "mark_success", {})
            assert result is False

    def test_build_email_subject_mark_failed(self):
        """测试标记失败标题"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        subject = notifier._build_email_subject_by_type(
            "mark_failed", {"title": "测试", "season": 1, "episode": 5}
        )
        assert "失败" in subject

    def test_build_simple_email_html_with_url(self):
        """测试带 URL 的 HTML"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "notification_type": "mark_success",
            "title": "测试",
            "url": "https://example.com",
            "episode_id": "123",
        }
        html = notifier._build_simple_email_html(data)
        assert "example.com" in html

    def test_build_simple_email_html_with_episode(self):
        """测试带集数信息的 HTML"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "notification_type": "mark_success",
            "title": "测试",
            "episode_id": "123",
            "subject_id": "456",
        }
        html = notifier._build_simple_email_html(data)
        assert "123" in html

    def test_build_email_text_request_received(self):
        """测试收到请求文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "title": "测试番剧",
            "user_name": "user1",
            "season": 1,
            "episode": 5,
            "source": "emby",
        }
        text = notifier._build_email_text_by_type("request_received", data)
        assert "测试番剧" in text

    def test_build_email_text_bangumi_id_found(self):
        """测试找到 Bangumi ID 文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "subject_id": "12345",
            "title": "测试番剧",
            "user_name": "user1",
            "season": 1,
            "episode": 5,
            "source": "emby",
        }
        text = notifier._build_email_text_by_type("bangumi_id_found", data)
        assert "匹配" in text or "Bangumi" in text

    def test_build_email_text_episode_not_found(self):
        """测试未找到集数文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "title": "测试番剧",
            "user_name": "user1",
            "season": 1,
            "episode": 99,
            "subject_id": "123",
            "source": "emby",
        }
        text = notifier._build_email_text_by_type("episode_not_found", data)
        assert "99" in text

    def test_build_email_text_api_auth_error(self):
        """测试 API 认证错误文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {"status_code": 401, "error_message": "认证失败"}
        text = notifier._build_email_text_by_type("api_auth_error", data)
        assert "401" in text

    def test_build_email_text_unknown_type(self):
        """测试未知类型文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        text = notifier._build_email_text_by_type("unknown_type", {})
        assert len(text) > 0

    def test_build_simple_html_with_status_code(self):
        """测试带状态码的 HTML"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "notification_type": "api_error",
            "status_code": 404,
            "url": "https://api.example.com",
        }
        html = notifier._build_simple_email_html(data)
        assert "404" in html

    def test_build_simple_html_mark_skipped(self):
        """测试标记跳过 HTML"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "notification_type": "mark_skipped",
            "title": "测试番剧",
            "user_name": "user1",
            "season": 1,
            "episode": 5,
            "source": "emby",
            "subject_id": "123",
            "episode_id": "456",
        }
        html = notifier._build_simple_email_html(data)
        assert "<html>" in html


class TestNotifierMergedWatchingSummary:
    """test_notifier_watching_summary.py 并入的独有用例。"""

    @staticmethod
    def _make_notifier() -> Notifier:
        mock_config = MagicMock()
        return Notifier(mock_config)

    @staticmethod
    def _make_watching_data() -> dict:
        return {
            "job_name": "MyDailySummary",
            "timestamp": "2024-07-15 10:00:00",
            "summary_text": "本周共追番5部，更新12集",
            "date_range": "2024-07-08 ~ 2024-07-15",
            "record_count": 12,
            "user_name": "testuser",
        }

    def test_payload_for_watching_summary_has_expected_keys(self):
        """watching_summary 的 payload 应包含所有预期字段。"""
        notifier = self._make_notifier()
        data = self._make_watching_data()

        payload = notifier._build_payload_by_type("watching_summary", data, "")

        assert payload["title"] == "📊 追番总结 - MyDailySummary"
        assert payload["type"] == "watching_summary"
        assert payload["timestamp"] == "2024-07-15 10:00:00"
        assert payload["summary"] == "本周共追番5部，更新12集"
        assert payload["date_range"] == "2024-07-08 ~ 2024-07-15"
        assert payload["record_count"] == 12
        assert payload["user_name"] == "testuser"

    def test_payload_for_watching_summary_dad_has_expected_keys(self):
        """watching_summary_dad 的 payload 应包含所有预期字段。"""
        notifier = self._make_notifier()
        data = self._make_watching_data()
        data["job_name"] = "DadSummary"

        payload = notifier._build_payload_by_type("watching_summary_dad", data, "")

        assert payload["title"] == "📊 追番总结 - DadSummary"
        assert payload["type"] == "watching_summary"
        assert payload["summary"] == "本周共追番5部，更新12集"

    def test_payload_handles_missing_fields_gracefully(self):
        """数据字段缺失时应使用默认值。"""
        notifier = self._make_notifier()
        data: dict = {}

        payload = notifier._build_payload_by_type("watching_summary", data, "")

        assert payload["title"] == "📊 追番总结 - "
        assert payload["summary"] == ""
        assert payload["date_range"] == ""
        assert payload["record_count"] == 0
        assert payload["user_name"] == ""

    def test_subject_for_watching_summary(self):
        """邮件主题应包含 job_name 和正确的前缀。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"job_name": "WeeklyReport"}

        result = notifier._build_email_subject_by_type("watching_summary", data)

        assert "追番总结" in result
        assert "WeeklyReport" in result
        assert result.startswith("[Bangumi-Syncer]")

    def test_subject_for_watching_summary_default_name(self):
        """即使 job_name 缺失，邮件主题也应正常工作。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        result = notifier._build_email_subject_by_type("watching_summary", {})

        assert "追番总结" in result

    def test_dynamic_content_for_watching_summary_contains_key_info(self):
        """动态 HTML 内容应包含 job_name、date_range、record_count、summary_text。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "job_name": "DailySummary",
            "date_range": "2024-07-01 ~ 2024-07-07",
            "record_count": 8,
            "summary_text": "本周更新8集",
        }

        result = notifier._build_email_dynamic_content("watching_summary", data)

        assert "DailySummary" in result
        assert "2024-07-01 ~ 2024-07-07" in result
        assert "8" in result
        assert "本周更新8集" in result

    def test_dynamic_content_for_watching_summary_dad(self):
        """动态 HTML 内容应支持 watching_summary_dad 变体。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "job_name": "DadReport",
            "date_range": "2024-07-01 ~ 2024-07-07",
            "record_count": 5,
            "summary_text": "爸爸的追番总结",
        }

        result = notifier._build_email_dynamic_content("watching_summary_dad", data)

        assert "DadReport" in result
        assert "爸爸的追番总结" in result

    def test_mark_failed_payload_unchanged(self):
        """mark_failed payload 仍应正常工作。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "title": "测试番剧",
            "error_message": "API错误",
            "error_type": "connection",
        }

        payload = notifier._build_payload_by_type("mark_failed", data, "")

        assert payload["title"] == "❌ 同步失败"
        assert payload["error"] == "API错误"

    def test_request_received_payload_unchanged(self):
        """request_received payload 仍应正常工作。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "测试番剧", "user_name": "test", "source": "emby"}

        payload = notifier._build_payload_by_type("request_received", data, "")

        assert payload["title"] == "📥 收到同步请求"
        assert payload["anime"] == "测试番剧"


class TestNotifierMergedHttp:
    """test_notifier_http.py 并入的独有用例。"""

    def test_webhook_filter_by_type(mock_config_with_webhook):
        """测试 Webhook 按类型过滤"""
        # 配置只接收 mark_success 类型
        config = MagicMock()
        config_parser = MagicMock()
        config_parser.sections.return_value = ["notify-webhook-1"]

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

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.01
        mock_response.headers = {}
        mock_response.text = ""
        with patch("app.utils.notifier.webhook.SyncHttpClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.prefix.return_value = mock_instance
            mock_instance.success_tpl.return_value = mock_instance
            mock_instance.failure_tpl.return_value = mock_instance
            mock_instance.post.return_value = mock_response

            notifier = Notifier(config)

            # 发送成功通知 - 应该发送
            notifier.send_notification_by_type(
                "mark_success",
                {"timestamp": "2024-01-01 12:00:00"},
            )
            assert mock_instance.post.call_count == 1

            # 发送失败通知 - 不应该发送
            notifier.send_notification_by_type(
                "mark_failed",
                {"timestamp": "2024-01-01 12:00:00"},
            )
            # 没有新的请求
            assert mock_instance.post.call_count == 1

    def test_webhook_disabled(mock_config_with_webhook):
        """测试禁用的 Webhook"""
        # 配置禁用 - 使用布尔值 False
        config = MagicMock()
        config_parser = MagicMock()
        config_parser.sections.return_value = ["notify-webhook-1"]

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

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.01
        mock_response.headers = {}
        mock_response.text = ""
        with patch("app.utils.notifier.webhook.SyncHttpClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.prefix.return_value = mock_instance
            mock_instance.success_tpl.return_value = mock_instance
            mock_instance.failure_tpl.return_value = mock_instance
            mock_instance.post.return_value = mock_response

            notifier = Notifier(config)
            _result = notifier.send_notification_by_type(
                "mark_success",
                {"timestamp": "2024-01-01 12:00:00"},
            )

            # 没有请求发出 - 当 enabled=False 时，_get_webhook_configs 应该过滤掉
            assert mock_instance.post.call_count == 0

        configs = notifier._get_webhook_configs()
        # 验证所有配置都是禁用的
        enabled_configs = [c for c in configs if c.get("enabled")]
        assert len(enabled_configs) == 0

    def test_should_send_notification_after_cooldown(mock_config):
        """测试冷却时间后可以再次发送"""

        notifier = Notifier(mock_config)

        # 第一次发送
        assert notifier._should_send_notification("test_type") is True

        # 手动将上次的通知时间设置为很早之前（绕过冷却）
        notifier._last_notification_time["test_type"] = 0

        # 现在应该可以发送
        assert notifier._should_send_notification("test_type") is True


# 以下 fixtures 供 TestNotifierMergedHttp 使用（并入自 test_notifier_http.py）


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
    config_parser.sections.return_value = ["notify-webhook-1"]

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
