"""
更多 notifier 测试
"""

from unittest.mock import MagicMock

from app.utils.notifier import Notifier


class TestNotifierMore:
    """更多通知器测试"""

    def test_should_send_cooldown(self):
        """测试冷却期间不发送"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        # 首次发送
        result1 = notifier._should_send_notification("test_type")
        assert result1 is True

        # 冷却期间再次发送
        result2 = notifier._should_send_notification("test_type")
        assert result2 is False

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
