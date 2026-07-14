"""通知测试辅助方法（mixin）"""

from datetime import datetime
from typing import Any, Optional


class TestHelpersMixin:
    """通知测试相关方法（供 Notifier 组合）"""

    def test_notification(
        self,
        notification_type: Optional[str] = None,
        webhook_id: Optional[int] = None,
        email_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        测试通知功能

        Args:
            notification_type: 通知类型，可选值: 'webhook', 'email', 'all'
            webhook_id: 指定测试的webhook ID（可选）
            email_id: 指定测试的email ID（可选）

        Returns:
            测试结果字典
        """
        results: dict[str, Any] = {
            "webhook": {
                "enabled": False,
                "success": False,
                "message": "",
                "webhooks": [],
            },
            "email": {"enabled": False, "success": False, "message": "", "emails": []},
        }

        test_data = self._build_test_notification_data()

        if notification_type in (None, "webhook", "all"):
            self._test_webhook_channels(results, test_data, webhook_id)

        if notification_type in (None, "email", "all"):
            self._test_email_channels(results, test_data, email_id)

        return results

    @staticmethod
    def _build_test_notification_data() -> dict[str, Any]:
        """构建测试通知数据"""
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_name": "TestUser",
            "title": "测试番剧",
            "season": 1,
            "episode": 1,
            "source": "test",
            "error_message": "这是一条测试通知",
        }

    def _test_webhook_channels(
        self,
        results: dict[str, Any],
        test_data: dict[str, Any],
        webhook_id: Optional[int],
    ) -> None:
        """测试所有 webhook 通道"""
        webhook_configs = self._get_webhook_configs()
        if webhook_id:
            webhook_configs = [w for w in webhook_configs if w.get("id") == webhook_id]

        for webhook_config in webhook_configs:
            webhook_result: dict[str, Any] = {
                "id": webhook_config.get("id"),
                "url": webhook_config.get("url", ""),
                "enabled": webhook_config.get("enabled", False),
                "success": False,
                "message": "",
            }
            if webhook_config.get("enabled", False):
                webhook_result["enabled"] = True
                try:
                    success = self._send_webhook_by_config(
                        webhook_config, "mark_success", test_data
                    )
                    if success:
                        webhook_result["success"] = True
                        webhook_result["message"] = (
                            f"Webhook {webhook_config['id']} 测试成功"
                        )
                    else:
                        webhook_result["message"] = (
                            f"Webhook {webhook_config['id']} 测试失败"
                        )
                except Exception as e:
                    webhook_result["message"] = (
                        f"Webhook {webhook_config['id']} 测试失败: {str(e)}"
                    )
            else:
                webhook_result["message"] = f"Webhook {webhook_config['id']} 未启用"

            results["webhook"]["webhooks"].append(webhook_result)

        results["webhook"]["enabled"] = len(webhook_configs) > 0
        results["webhook"]["message"] = f"测试了 {len(webhook_configs)} 个webhook"

    def _test_email_channels(
        self,
        results: dict[str, Any],
        test_data: dict[str, Any],
        email_id: Optional[int],
    ) -> None:
        """测试所有邮件通道"""
        email_configs = self._get_email_configs()
        if email_id:
            email_configs = [e for e in email_configs if e.get("id") == email_id]

        for email_config in email_configs:
            email_result: dict[str, Any] = {
                "id": email_config.get("id"),
                "email_to": email_config.get("email_to", ""),
                "enabled": email_config.get("enabled", False),
                "success": False,
                "message": "",
            }
            if email_config.get("enabled", False):
                email_result["enabled"] = True
                try:
                    success = self._send_email_by_config(
                        email_config, "mark_success", test_data
                    )
                    if success:
                        email_result["success"] = True
                        email_result["message"] = (
                            f"邮件配置 {email_config['id']} 测试成功"
                        )
                    else:
                        email_result["message"] = (
                            f"邮件配置 {email_config['id']} 测试失败"
                        )
                except Exception as e:
                    email_result["message"] = (
                        f"邮件配置 {email_config['id']} 测试失败: {str(e)}"
                    )
            else:
                email_result["message"] = f"邮件配置 {email_config['id']} 未启用"

            results["email"]["emails"].append(email_result)

        results["email"]["enabled"] = len(email_configs) > 0
        results["email"]["message"] = f"测试了 {len(email_configs)} 个邮件配置"
