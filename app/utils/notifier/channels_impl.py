"""Webhook / Email / In-App 三种渠道实现

每个渠道类继承 :class:`NotificationChannel`，仅关心「如何把一个已渲染好的 payload
发出去」，不再关心模板查找、冷却策略、路由等业务逻辑 —— 这些均由上层
:class:`NotificationService` 和 :class:`NotificationTemplateManager` 负责。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import smtplib
import ssl
import time
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any

from ...core.logging import logger
from ...utils.http_base import SyncHttpClient
from .channels import ChannelSendResult, NotificationChannel

# ─────────────────────────────────────────────────────────────────────────
# 通用工具
# ─────────────────────────────────────────────────────────────────────────


def _parse_headers(headers_str: str) -> dict[str, str]:
    """解析 webhook 自定义请求头，兼容 JSON / 逗号分隔键值对。"""
    headers: dict[str, str] = {"User-Agent": "Bangumi-Syncer-Notifier"}
    if not headers_str:
        return headers
    if not isinstance(headers_str, str):
        headers_str = str(headers_str)
    try:
        import json

        parsed = json.loads(headers_str)
        if isinstance(parsed, dict):
            headers.update({str(k): str(v) for k, v in parsed.items()})
    except Exception:
        for h in headers_str.split(","):
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
    return headers


# ─────────────────────────────────────────────────────────────────────────
# Webhook 渠道
# ─────────────────────────────────────────────────────────────────────────


class WebhookChannel(NotificationChannel):
    """Webhook 推送渠道

    配置字段：``url``, ``method``(GET/POST), ``headers``, ``types``,
    ``template``（预留自定义 JSON 模板，优先于模板目录查找）。
    """

    channel_type = "webhook"
    channel_label = "Webhook"

    def send(
        self,
        notification_type: str,
        payload: dict[str, Any],
        rendered: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        url = self.config.get("url", "")
        method = str(self.config.get("method", "POST")).upper()
        headers = _parse_headers(self.config.get("headers", ""))

        try:
            client = (
                SyncHttpClient(
                    label=f"Webhook#{self.channel_id}", timeout=10.0, max_retries=0
                )
                .prefix("🔔")
                .success_tpl("通知发送成功")
                .failure_tpl("通知发送失败")
            )

            if method == "GET":
                response = client.get(url, params=payload, headers=headers)
            else:
                response = client.post(url, json=payload, headers=headers)

            if response.status_code < 300:
                return ChannelSendResult(
                    success=True,
                    channel_id=self.channel_id,
                    channel_name=self.channel_label,
                )
            logger.warning(
                f"Webhook#{self.channel_id} 返回非成功状态码: {response.status_code}"
            )
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message=f"HTTP {response.status_code}",
            )
        except Exception as e:
            logger.error(f"Webhook#{self.channel_id} 发送异常: {e}")
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message=str(e),
            )


# ─────────────────────────────────────────────────────────────────────────
# Email 渠道
# ─────────────────────────────────────────────────────────────────────────


class EmailChannel(NotificationChannel):
    """邮件推送渠道

    配置字段：``smtp_server``, ``smtp_port``, ``smtp_username``,
    ``smtp_password``, ``smtp_use_tls``, ``email_from``, ``email_to``,
    ``email_subject``, ``types``。
    """

    channel_type = "email"
    channel_label = "Email"

    def send(
        self,
        notification_type: str,
        payload: dict[str, Any],
        rendered: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        subject = (rendered or {}).get("subject") or ""
        body = (rendered or {}).get("body") or ""
        html = (rendered or {}).get("html")

        smtp_server = self.config.get("smtp_server", "")
        smtp_port = int(self.config.get("smtp_port", 587))
        smtp_username = self.config.get("smtp_username", "")
        smtp_password = self.config.get("smtp_password", "")
        smtp_use_tls = bool(self.config.get("smtp_use_tls", True))
        from_email = self.config.get("email_from") or smtp_username
        to_email = self.config.get("email_to", "")

        if not to_email:
            logger.warning(f"邮件渠道 {self.channel_id} 未配置收件人，跳过")
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message="缺少收件人地址",
            )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject or f"[Bangumi-Syncer] {notification_type}"
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Date"] = formatdate(localtime=True)

        msg.attach(MIMEText(body or str(payload), "plain", "utf-8"))
        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            if smtp_port == 465:
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(
                    smtp_server, smtp_port, timeout=30, context=context
                )
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
                if smtp_use_tls:
                    server.starttls()

            try:
                if smtp_username and smtp_password:
                    server.login(smtp_username, smtp_password)
                server.send_message(msg)
                logger.debug(f"✅ 邮件发送成功: {to_email} (channel={self.channel_id})")
                return ChannelSendResult(
                    success=True,
                    channel_id=self.channel_id,
                    channel_name=self.channel_label,
                )
            finally:
                try:
                    server.quit()
                except Exception:
                    pass
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ 邮件认证失败 (channel={self.channel_id}): {e}")
            err_msg = str(e)
        except smtplib.SMTPException as e:
            logger.error(f"❌ SMTP 错误 (channel={self.channel_id}): {e}")
            err_msg = str(e)
        except Exception as e:
            logger.error(f"❌ 邮件发送失败 (channel={self.channel_id}): {e}")
            err_msg = str(e)
        else:
            err_msg = ""

        return ChannelSendResult(
            success=False,
            channel_id=self.channel_id,
            channel_name=self.channel_label,
            message=err_msg,
        )


# ─────────────────────────────────────────────────────────────────────────
# 企业微信渠道
# ─────────────────────────────────────────────────────────────────────────


class WeChatWorkChannel(NotificationChannel):
    """企业微信群机器人渠道

    配置字段：``key``（机器人 webhook key，或完整 URL）、``msg_type``
    （``text`` / ``markdown``，默认 ``text``）、``types``。
    """

    channel_type = "wecom"
    channel_label = "企业微信"

    _API_BASE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"

    def _build_url(self) -> str:
        """从配置中推导完整 URL：支持直接填 key 或完整 URL。"""
        raw = str(self.config.get("key", "")).strip()
        if not raw:
            return ""
        if raw.startswith("http"):
            return raw
        return f"{self._API_BASE}?key={raw}"

    @staticmethod
    def _build_text_content(payload: dict[str, Any]) -> str:
        """从 payload 构造纯文本消息内容。"""
        lines: list[str] = []
        title = payload.get("title", "")
        if title:
            lines.append(str(title))
        lines.append(f"时间: {payload.get('timestamp', '')}")
        anime = payload.get("anime") or payload.get("title", "")
        if anime:
            ep = payload.get("episode", "")
            lines.append(f"番剧: {anime} {ep}".rstrip())
        user = payload.get("user") or payload.get("user_name", "")
        if user:
            lines.append(f"用户: {user}")
        source = payload.get("source", "")
        if source:
            lines.append(f"来源: {source}")
        err = payload.get("error") or payload.get("error_message", "")
        if err:
            lines.append(f"错误: {err}")
        return "\n".join(lines)

    @staticmethod
    def _build_markdown_content(payload: dict[str, Any]) -> str:
        """从 payload 构造 Markdown 消息内容。"""
        title = payload.get("title", "通知")
        parts: list[str] = [f"**{title}**"]
        parts.append(f"> 时间: {payload.get('timestamp', '')}")
        anime = payload.get("anime") or payload.get("title", "")
        if anime:
            ep = payload.get("episode", "")
            parts.append(f"> 番剧: {anime} {ep}".rstrip())
        user = payload.get("user") or payload.get("user_name", "")
        if user:
            parts.append(f"> 用户: {user}")
        source = payload.get("source", "")
        if source:
            parts.append(f"> 来源: {source}")
        err = payload.get("error") or payload.get("error_message", "")
        if err:
            parts.append(f"> 错误: {err}")
        return "\n".join(parts)

    @staticmethod
    def _render_template_value(value: Any, data: dict[str, Any]) -> Any:
        """递归渲染模板值中的占位符"""
        if isinstance(value, dict):
            return {
                k: WeChatWorkChannel._render_template_value(v, data)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [
                WeChatWorkChannel._render_template_value(item, data) for item in value
            ]
        if isinstance(value, str):
            from ...utils.notifier.template_manager import NotificationTemplateManager

            return NotificationTemplateManager.render_string(value, data)
        return value

    def send(
        self,
        notification_type: str,
        payload: dict[str, Any],
        rendered: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        url = self._build_url()
        if not url:
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message="未配置企业微信 key",
            )

        msg_type = str(self.config.get("msg_type", "text")).strip().lower()
        custom_tpl = self.config.get("template", "").strip()
        if custom_tpl:
            try:
                body = json.loads(custom_tpl)
                body = self._render_template_value(body, payload)
            except json.JSONDecodeError:
                content = (
                    self._build_text_content(payload)
                    if msg_type != "markdown"
                    else self._build_markdown_content(payload)
                )
                body = {"msgtype": msg_type, msg_type: {"content": content}}
        else:
            if msg_type == "markdown":
                content = self._build_markdown_content(payload)
                body = {"msgtype": "markdown", "markdown": {"content": content}}
            else:
                content = self._build_text_content(payload)
                body = {"msgtype": "text", "text": {"content": content}}

        try:
            client = (
                SyncHttpClient(
                    label=f"WeCom#{self.channel_id}", timeout=10.0, max_retries=0
                )
                .prefix("💬")
                .success_tpl("企业微信通知发送成功")
                .failure_tpl("企业微信通知发送失败")
            )
            response = client.post(url, json=body)

            if response.status_code < 300:
                # 企业微信返回 200 但 body 中 errcode != 0 也算失败
                try:
                    resp_data = response.json()
                except Exception:
                    resp_data = {}
                errcode = resp_data.get("errcode", 0)
                if errcode == 0:
                    return ChannelSendResult(
                        success=True,
                        channel_id=self.channel_id,
                        channel_name=self.channel_label,
                    )
                errmsg = resp_data.get("errmsg", "unknown")
                logger.warning(
                    f"企业微信#{self.channel_id} 返回错误: errcode={errcode} errmsg={errmsg}"
                )
                return ChannelSendResult(
                    success=False,
                    channel_id=self.channel_id,
                    channel_name=self.channel_label,
                    message=f"errcode={errcode} {errmsg}",
                )
            logger.warning(f"企业微信#{self.channel_id} HTTP {response.status_code}")
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message=f"HTTP {response.status_code}",
            )
        except Exception as e:
            logger.error(f"企业微信#{self.channel_id} 发送异常: {e}")
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message=str(e),
            )


# ─────────────────────────────────────────────────────────────────────────
# 钉钉渠道
# ─────────────────────────────────────────────────────────────────────────


class DingTalkChannel(NotificationChannel):
    """钉钉群机器人渠道

    配置字段：``access_token``（机器人 webhook 的 access_token，或完整 URL）、
    ``secret``（加签密钥，可选）、``msg_type``（``text`` / ``markdown``，
    默认 ``text``）、``types``。

    钉钉安全设置支持「加签」方式，若配置了 ``secret`` 则自动计算签名并附加到 URL。
    """

    channel_type = "dingtalk"
    channel_label = "钉钉"

    _API_BASE = "https://oapi.dingtalk.com/robot/send"

    def _build_url(self) -> str:
        """从配置中推导完整 URL，若配置了 secret 则附加加签参数。"""
        raw = str(self.config.get("access_token", "")).strip()
        if raw.startswith("http"):
            base_url = raw
        elif raw:
            base_url = f"{self._API_BASE}?access_token={raw}"
        else:
            return ""

        secret = str(self.config.get("secret", "")).strip()
        if not secret:
            return base_url

        # 钉钉加签算法：timestamp + "\n" + secret 做 HMAC-SHA256，再 base64 编码
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}timestamp={timestamp}&sign={sign}"

    @staticmethod
    def _build_text_content(payload: dict[str, Any]) -> str:
        """从 payload 构造纯文本消息内容。"""
        lines: list[str] = []
        title = payload.get("title", "")
        if title:
            lines.append(str(title))
        lines.append(f"时间: {payload.get('timestamp', '')}")
        anime = payload.get("anime") or payload.get("title", "")
        if anime:
            ep = payload.get("episode", "")
            lines.append(f"番剧: {anime} {ep}".rstrip())
        user = payload.get("user") or payload.get("user_name", "")
        if user:
            lines.append(f"用户: {user}")
        source = payload.get("source", "")
        if source:
            lines.append(f"来源: {source}")
        err = payload.get("error") or payload.get("error_message", "")
        if err:
            lines.append(f"错误: {err}")
        return "\n".join(lines)

    @staticmethod
    def _build_markdown_content(payload: dict[str, Any]) -> tuple[str, str]:
        """从 payload 构造 Markdown 消息，返回 (title, text)。"""
        title = str(payload.get("title", "通知"))
        parts: list[str] = []
        parts.append(f"- 时间: {payload.get('timestamp', '')}")
        anime = payload.get("anime") or payload.get("title", "")
        if anime:
            ep = payload.get("episode", "")
            parts.append(f"- 番剧: {anime} {ep}".rstrip())
        user = payload.get("user") or payload.get("user_name", "")
        if user:
            parts.append(f"- 用户: {user}")
        source = payload.get("source", "")
        if source:
            parts.append(f"- 来源: {source}")
        err = payload.get("error") or payload.get("error_message", "")
        if err:
            parts.append(f"- 错误: {err}")
        return title, "\n".join(parts)

    @staticmethod
    def _render_template_value(value: Any, data: dict[str, Any]) -> Any:
        """递归渲染模板值中的占位符"""
        if isinstance(value, dict):
            return {
                k: DingTalkChannel._render_template_value(v, data)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [
                DingTalkChannel._render_template_value(item, data) for item in value
            ]
        if isinstance(value, str):
            from ...utils.notifier.template_manager import NotificationTemplateManager

            return NotificationTemplateManager.render_string(value, data)
        return value

    def send(
        self,
        notification_type: str,
        payload: dict[str, Any],
        rendered: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        url = self._build_url()
        if not url:
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message="未配置钉钉 access_token",
            )

        msg_type = str(self.config.get("msg_type", "text")).strip().lower()
        custom_tpl = self.config.get("template", "").strip()

        def _build_default_body() -> dict[str, Any]:
            """按 msg_type 构造钉钉消息体（与无 custom_tpl 时一致）"""
            if msg_type == "markdown":
                title, text = self._build_markdown_content(payload)
                return {
                    "msgtype": "markdown",
                    "markdown": {"title": title, "text": text},
                }
            content = self._build_text_content(payload)
            return {"msgtype": "text", "text": {"content": content}}

        if custom_tpl:
            try:
                body = json.loads(custom_tpl)
                body = self._render_template_value(body, payload)
            except json.JSONDecodeError:
                # custom_tpl 非法时回退到默认消息体（避免 markdown 分支字段错误）
                body = _build_default_body()
        else:
            body = _build_default_body()

        try:
            client = (
                SyncHttpClient(
                    label=f"DingTalk#{self.channel_id}", timeout=10.0, max_retries=0
                )
                .prefix("📌")
                .success_tpl("钉钉通知发送成功")
                .failure_tpl("钉钉通知发送失败")
            )
            response = client.post(url, json=body)

            if response.status_code < 300:
                try:
                    resp_data = response.json()
                except Exception:
                    resp_data = {}
                errcode = resp_data.get("errcode", 0)
                if errcode == 0:
                    return ChannelSendResult(
                        success=True,
                        channel_id=self.channel_id,
                        channel_name=self.channel_label,
                    )
                errmsg = resp_data.get("errmsg", "unknown")
                logger.warning(
                    f"钉钉#{self.channel_id} 返回错误: errcode={errcode} errmsg={errmsg}"
                )
                return ChannelSendResult(
                    success=False,
                    channel_id=self.channel_id,
                    channel_name=self.channel_label,
                    message=f"errcode={errcode} {errmsg}",
                )
            logger.warning(f"钉钉#{self.channel_id} HTTP {response.status_code}")
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message=f"HTTP {response.status_code}",
            )
        except Exception as e:
            logger.error(f"钉钉#{self.channel_id} 发送异常: {e}")
            return ChannelSendResult(
                success=False,
                channel_id=self.channel_id,
                channel_name=self.channel_label,
                message=str(e),
            )


# ─────────────────────────────────────────────────────────────────────────
# 站内信渠道（由 NotificationService 直接调用 db，这里仅做结构占位）
# ─────────────────────────────────────────────────────────────────────────


class InAppChannel(NotificationChannel):
    """站内信渠道

    与其他渠道不同，站内信的数据落库由上层 ``NotificationService`` 完成，
    本类主要用于在 :class:`ChannelRegistry` 中保持一致的接口形态，便于
    未来替换为真正的异步通道。
    """

    channel_type = "in_app"
    channel_label = "站内信"

    def __init__(self, channel_id: str, config: dict[str, Any]) -> None:
        super().__init__(channel_id, config)
        # 站内信的启用开关由配置 ``in_app_notification`` 决定；默认 True
        self.enabled = bool(config.get("in_app_notification", True))

    def send(
        self,
        notification_type: str,
        payload: dict[str, Any],
        rendered: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        # 站内信的实际写入由 NotificationService._write_in_app_notification 完成；
        # 此处不再重复实现，仅返回占位结果以便于统计。
        return ChannelSendResult(
            success=True, channel_id=self.channel_id, channel_name=self.channel_label
        )
