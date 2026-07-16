"""Webhook 发送与 payload 构建（mixin）"""

from typing import Any

from ...core.logging import logger
from ..http_base import SyncHttpClient


class WebhookMixin:
    """Webhook 相关方法（供 Notifier 组合）"""

    def _send_webhook_by_config(
        self,
        webhook_config: dict[str, Any],
        notification_type: str,
        data: dict[str, Any],
    ) -> bool:
        """
        根据配置发送webhook通知

        Args:
            webhook_config: webhook配置字典
            notification_type: 通知类型
            data: 通知数据
        """
        try:
            url = webhook_config["url"]
            method = webhook_config.get("method", "POST").upper()
            headers = self._parse_headers(webhook_config.get("headers", ""))
            template = webhook_config.get("template", "")

            # 构建载荷
            payload = self._build_payload_by_type(notification_type, data, template)

            # 发送请求
            logger.info(f"📤 发送 {notification_type} 通知到: {url}")

            client = (
                SyncHttpClient(label="Webhook", timeout=10.0, max_retries=0)
                .prefix("🔔")
                .success_tpl("通知发送成功")
                .failure_tpl("通知发送失败")
            )

            if method == "POST":
                response = client.post(url, json=payload, headers=headers)
            else:  # GET
                response = client.get(
                    url,
                    params=payload if isinstance(payload, dict) else None,
                    headers=headers,
                )

            if response.status_code < 300:
                return True
            else:
                logger.warning(f"⚠️  Webhook返回非成功状态码: {response.status_code}")
                return False

        except Exception:
            return False

    def _build_payload_by_type(
        self, notification_type: str, data: dict[str, Any], template: str
    ) -> dict[str, Any]:
        """
        根据通知类型构建载荷

        Args:
            notification_type: 通知类型
            data: 原始数据
            template: 自定义模板
        """
        # 添加通知类型到数据中
        data["notification_type"] = notification_type

        # 如果有自定义模板，使用模板
        if template:
            try:
                import json

                template_obj = json.loads(template)
                return self._replace_template_variables(template_obj, data)
            except Exception as e:
                logger.warning(f"自定义模板解析失败: {e}，使用默认格式")

        # 根据通知类型使用不同的默认格式
        default_templates = {
            "request_received": {
                "title": "📥 收到同步请求",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "user": data.get("user_name", ""),
                "anime": data.get("title", ""),
                "episode": f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                "source": data.get("source", ""),
            },
            "bangumi_id_found": {
                "title": "🔍 匹配到Bangumi番剧",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "user": data.get("user_name", ""),
                "anime": data.get("title", ""),
                "episode": f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                "source": data.get("source", ""),
                "subject_id": data.get("subject_id", ""),
            },
            "mark_success": {
                "title": "✅ 同步成功",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "user": data.get("user_name", ""),
                "anime": data.get("title", ""),
                "episode": f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                "source": data.get("source", ""),
                "subject_id": data.get("subject_id", ""),
                "episode_id": data.get("episode_id", ""),
            },
            "mark_failed": {
                "title": "❌ 同步失败",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "user": data.get("user_name", ""),
                "anime": data.get("title", ""),
                "episode": f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                "source": data.get("source", ""),
                "error": data.get("error_message", ""),
                "error_type": data.get("error_type", ""),
            },
            "mark_skipped": {
                "title": "⏭️ 已看过，跳过",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "user": data.get("user_name", ""),
                "anime": data.get("title", ""),
                "episode": f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                "source": data.get("source", ""),
                "subject_id": data.get("subject_id", ""),
                "episode_id": data.get("episode_id", ""),
            },
            "config_error": {
                "title": "⚠️ 配置错误",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "error_message": data.get("error_message", ""),
                "config_type": data.get("config_type", ""),
                "user_name": data.get("user_name", ""),
                "mode": data.get("mode", ""),
            },
            "anime_not_found": {
                "title": "🔍 未找到番剧",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "user": data.get("user_name", ""),
                "ori_title": data.get("ori_title", ""),
                "season": data.get("season", 0),
                "source": data.get("source", ""),
                "search_method": data.get("search_method", ""),
            },
            "episode_not_found": {
                "title": "📺 未找到剧集",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "user": data.get("user_name", ""),
                "season": data.get("season", 0),
                "episode": data.get("episode", 0),
                "subject_id": data.get("subject_id", ""),
                "source": data.get("source", ""),
            },
            "api_auth_error": {
                "title": "🔑 API认证失败",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "username": data.get("username", ""),
                "status_code": data.get("status_code", 0),
                "error_message": data.get("error_message", ""),
            },
            "api_error": {
                "title": "🌐 API错误",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "status_code": data.get("status_code", 0),
                "url": data.get("url", ""),
                "method": data.get("method", ""),
                "error_message": data.get("error_message", ""),
                "retry_count": data.get("retry_count", 0),
            },
            "api_retry_failed": {
                "title": "🔄 API重试失败",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "subject_id": data.get("subject_id", ""),
                "episode_id": data.get("episode_id", ""),
                "max_retries": data.get("max_retries", 0),
                "error_message": data.get("error_message", ""),
            },
            "ip_locked": {
                "title": "🔒 IP被锁定",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "ip": data.get("ip", ""),
                "locked_until": data.get("locked_until", ""),
                "attempt_count": data.get("attempt_count", 0),
                "max_attempts": data.get("max_attempts", 0),
            },
            "pending_candidate": {
                "title": "📝 候选待确认",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "user": data.get("user_name", ""),
                "anime": data.get("title", ""),
                "episode": f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                "source": data.get("source", ""),
                "candidates_count": data.get("candidates_count", 0),
                "top_candidate_id": data.get("top_candidate_id", ""),
                "top_candidate_name": data.get("top_candidate_name", ""),
            },
        }

        return default_templates.get(
            notification_type,
            {
                "title": f"📢 {notification_type}",
                "type": notification_type,
                "timestamp": data.get("timestamp", ""),
                "data": data,
            },
        )

    def _parse_headers(self, headers_str: str) -> dict[str, str]:
        """解析请求头字符串"""
        headers = {"User-Agent": "Bangumi-Syncer-Notifier"}

        # 确保headers_str是字符串类型
        if not headers_str:
            return headers

        # 如果headers_str不是字符串，转换为字符串
        if not isinstance(headers_str, str):
            headers_str = str(headers_str)

        try:
            # 尝试解析为JSON
            import json

            parsed = json.loads(headers_str)
            if isinstance(parsed, dict):
                headers.update(parsed)
        except Exception:
            # 如果不是JSON，尝试解析为逗号分隔的键值对
            try:
                for header in headers_str.split(","):
                    if ":" in header:
                        key, value = header.split(":", 1)
                        headers[key.strip()] = value.strip()
            except Exception as e:
                logger.warning(f"解析headers失败: {e}")

        return headers
