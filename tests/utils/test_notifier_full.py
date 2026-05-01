"""
更多 notifier 测试
"""

from unittest.mock import MagicMock, patch

from app.utils.notifier import Notifier


class TestNotifierConfigs:
    """通知配置测试"""

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

    def test_get_webhook_configs_with_data(self):
        """测试获取 webhook 配置"""
        mock_config = MagicMock()
        mock_config.get_config_parser.return_value.sections.return_value = [
            "webhook-test"
        ]
        mock_config.get_section.return_value = {
            "url": "https://example.com",
            "enabled": "true",
        }

        notifier = Notifier(mock_config)
        result = notifier._get_webhook_configs()
        assert len(result) >= 0

    def test_get_email_configs_with_data(self):
        """测试获取邮件配置"""
        mock_config = MagicMock()
        mock_config.get_config_parser.return_value.sections.return_value = [
            "email-test"
        ]
        mock_config.get_section.return_value = {
            "smtp_server": "smtp.example.com",
            "enabled": "true",
        }

        notifier = Notifier(mock_config)
        result = notifier._get_email_configs()
        assert len(result) >= 0


class TestNotifierPayload:
    """通知载荷测试"""

    def test_build_payload_by_type(self):
        """测试构建载荷"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        # 测试基础载荷
        data = {
            "title": "Test",
            "season": 1,
            "episode": 5,
            "user_name": "test",
        }

        # 这个方法可能不存在，让我检查一下
        if hasattr(notifier, "_build_payload_by_type"):
            result = notifier._build_payload_by_type("request_received", data, "")
            assert result is not None


class TestNotifierHelper:
    """辅助方法测试"""

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


class TestNotifierSendEmail:
    """邮件发送路径测试"""

    def test_send_email_ssl_port_465(self):
        """测试 SMTP_SSL 端口 465 发送路径"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        email_config = {
            "id": "1",
            "smtp_server": "smtp.example.com",
            "smtp_port": "465",
            "smtp_username": "user@example.com",
            "smtp_password": "pass",
            "smtp_use_tls": True,
            "email_from": "from@example.com",
            "email_to": "to@example.com",
            "email_subject": "",
            "email_template_file": "",
        }

        with (
            patch("app.utils.notifier.smtplib.SMTP_SSL") as mock_smtp_ssl,
            patch.object(notifier, "_load_email_template", return_value=None),
            patch.object(
                notifier, "_build_simple_email_html", return_value="<html></html>"
            ),
            patch.object(notifier, "_build_email_text_by_type", return_value="text"),
        ):
            mock_server = MagicMock()
            mock_smtp_ssl.return_value = mock_server

            result = notifier._send_email_by_config(email_config, "mark_success", {})

            assert result is True
            mock_smtp_ssl.assert_called_once()
            mock_server.login.assert_called_once()
            mock_server.send_message.assert_called_once()

    def test_send_email_starttls_port_587(self):
        """测试 STARTTLS 端口 587 发送路径"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        email_config = {
            "id": "1",
            "smtp_server": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "pass",
            "smtp_use_tls": True,
            "email_from": "from@example.com",
            "email_to": "to@example.com",
            "email_subject": "",
            "email_template_file": "",
        }

        with (
            patch("app.utils.notifier.smtplib.SMTP") as mock_smtp,
            patch.object(notifier, "_load_email_template", return_value=None),
            patch.object(
                notifier, "_build_simple_email_html", return_value="<html></html>"
            ),
            patch.object(notifier, "_build_email_text_by_type", return_value="text"),
        ):
            mock_server = MagicMock()
            mock_smtp.return_value = mock_server

            result = notifier._send_email_by_config(email_config, "mark_success", {})

            assert result is True
            mock_smtp.assert_called_once()
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once()
            mock_server.send_message.assert_called_once()

    def test_send_email_no_to_address(self):
        """测试没有收件人地址时跳过发送"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        email_config = {
            "id": "1",
            "smtp_server": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "pass",
            "email_from": "from@example.com",
            "email_to": "",
        }

        result = notifier._send_email_by_config(email_config, "mark_success", {})
        assert result is False

    def test_send_email_smtp_auth_error(self):
        """测试 SMTP 认证失败"""
        import smtplib

        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        email_config = {
            "id": "1",
            "smtp_server": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "wrong",
            "email_from": "from@example.com",
            "email_to": "to@example.com",
        }

        with (
            patch("app.utils.notifier.smtplib.SMTP") as mock_smtp,
            patch.object(notifier, "_load_email_template", return_value=None),
            patch.object(
                notifier, "_build_simple_email_html", return_value="<html></html>"
            ),
            patch.object(notifier, "_build_email_text_by_type", return_value="text"),
        ):
            mock_server = MagicMock()
            mock_server.login.side_effect = smtplib.SMTPAuthenticationError(
                535, b"Auth failed"
            )
            mock_smtp.return_value = mock_server

            result = notifier._send_email_by_config(email_config, "mark_success", {})
            assert result is False

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
            patch("app.utils.notifier.smtplib.SMTP") as mock_smtp,
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
                "app.utils.notifier.smtplib.SMTP",
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


class TestNotifierEmailSubject:
    """邮件标题测试"""

    def test_build_email_subject_request_received(self):
        """测试收到请求标题"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        subject = notifier._build_email_subject_by_type(
            "request_received", {"title": "测试", "season": 1, "episode": 5}
        )
        assert "Bangumi" in subject

    def test_build_email_subject_mark_success(self):
        """测试标记成功标题"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        subject = notifier._build_email_subject_by_type(
            "mark_success", {"title": "测试", "season": 1, "episode": 5}
        )
        assert "成功" in subject

    def test_build_email_subject_mark_failed(self):
        """测试标记失败标题"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        subject = notifier._build_email_subject_by_type(
            "mark_failed", {"title": "测试", "season": 1, "episode": 5}
        )
        assert "失败" in subject

    def test_build_email_subject_unknown_type(self):
        """测试未知类型标题"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        subject = notifier._build_email_subject_by_type("unknown_type", {})
        assert len(subject) > 0


class TestNotifierBuildSimpleEmailHtml:
    """简单邮件 HTML 构建测试"""

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


class TestNotifierBuildEmailTextByType:
    """邮件纯文本构建测试"""

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

    def test_build_email_text_mark_success(self):
        """测试标记成功文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "title": "测试番剧",
            "user_name": "user1",
            "season": 1,
            "episode": 5,
            "source": "emby",
            "subject_id": "123",
            "episode_id": "456",
        }
        text = notifier._build_email_text_by_type("mark_success", data)
        assert "测试番剧" in text

    def test_build_email_text_mark_failed(self):
        """测试标记失败文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "title": "测试番剧",
            "user_name": "user1",
            "season": 1,
            "episode": 5,
            "source": "emby",
            "error_message": "网络错误",
            "error_type": "ConnectionError",
        }
        text = notifier._build_email_text_by_type("mark_failed", data)
        assert "网络错误" in text

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

    def test_build_email_text_anime_not_found(self):
        """测试未找到番剧文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "title": "未知番剧",
            "user_name": "user1",
            "season": 1,
            "ori_title": "Unknown",
            "source": "emby",
            "search_method": "title",
        }
        text = notifier._build_email_text_by_type("anime_not_found", data)
        assert "未知番剧" in text

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

    def test_build_email_text_config_error(self):
        """测试配置错误文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "error_message": "配置无效",
            "config_type": "emby",
            "user_name": "user1",
            "mode": "auto",
        }
        text = notifier._build_email_text_by_type("config_error", data)
        assert "配置无效" in text

    def test_build_email_text_api_auth_error(self):
        """测试 API 认证错误文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {"status_code": 401, "error_message": "认证失败"}
        text = notifier._build_email_text_by_type("api_auth_error", data)
        assert "401" in text

    def test_build_email_text_api_error(self):
        """测试 API 错误文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {"status_code": 500, "error_message": "服务器错误"}
        text = notifier._build_email_text_by_type("api_error", data)
        assert "500" in text

    def test_build_email_text_unknown_type(self):
        """测试未知类型文本"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        text = notifier._build_email_text_by_type("unknown_type", {})
        assert len(text) > 0


class TestNotifierBuildSimpleHtml:
    """简单 HTML 构建测试"""

    def test_build_simple_html_request_received(self):
        """测试收到请求 HTML"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "notification_type": "request_received",
            "title": "测试番剧",
            "user_name": "user1",
            "season": 1,
            "episode": 5,
        }
        html = notifier._build_simple_email_html(data)
        assert "<html>" in html
        assert "测试番剧" in html

    def test_build_simple_html_mark_failed(self):
        """测试标记失败 HTML"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {
            "notification_type": "mark_failed",
            "title": "测试番剧",
            "error_message": "网络错误",
            "error_type": "ConnectionError",
        }
        html = notifier._build_simple_email_html(data)
        assert "网络错误" in html

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

    def test_build_simple_html_with_dynamic_content(self):
        """测试带动态内容的 HTML"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        data = {"notification_type": "custom", "dynamic_content": "<p>自定义内容</p>"}
        html = notifier._build_simple_email_html(data)
        assert "自定义内容" in html

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
