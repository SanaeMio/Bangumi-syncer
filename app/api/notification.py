"""
通知API
"""

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..core.config import config_manager
from ..core.config_secret_crypto import encrypt_if_sensitive
from ..core.logging import logger
from ..utils.notifier import get_notifier
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api")


def _reload_notification_channels() -> None:
    """渠道配置变更后刷新 NotificationService 的渠道注册表

    NotificationService 启动时延迟加载一次渠道，之后配置变更不会自动同步。
    在 webhook/email/wecom/dingtalk/rules 的写操作完成后调用本函数，确保
    新增/修改/删除的渠道在下一次 notify() 时立即生效。
    """
    try:
        from ..services.notification_service import notification_service

        notification_service.load_channels_from_config(config_manager)
    except Exception as e:
        logger.debug(f"刷新通知渠道注册表失败（可忽略）: {e}")


class NotificationTestRequest(BaseModel):
    """通知测试请求"""

    notification_type: Optional[str] = "webhook"  # webhook, email, all
    webhook_id: Optional[int] = None  # 指定测试的webhook ID
    email_id: Optional[int] = None  # 指定测试的email ID


class WebhookConfigCreate(BaseModel):
    """创建webhook配置请求"""

    enabled: bool = True
    url: str
    method: str = "POST"
    headers: str = ""
    template: str = ""
    types: str = "all"


class WebhookConfigUpdate(BaseModel):
    """更新webhook配置请求"""

    enabled: Optional[bool] = None
    url: Optional[str] = None
    method: Optional[str] = None
    headers: Optional[str] = None
    template: Optional[str] = None
    types: Optional[str] = None


class EmailConfigCreate(BaseModel):
    """创建邮件配置请求"""

    enabled: bool = True
    smtp_server: str
    smtp_port: int = 465
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool = True
    email_from: str = ""
    email_to: str
    email_subject: str = ""
    template: str = ""
    types: str = "mark_failed"


class EmailConfigUpdate(BaseModel):
    """更新邮件配置请求"""

    enabled: Optional[bool] = None
    smtp_server: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: Optional[bool] = None
    email_from: Optional[str] = None
    email_to: Optional[str] = None
    email_subject: Optional[str] = None
    template: Optional[str] = None
    types: Optional[str] = None


class WeComConfigCreate(BaseModel):
    """创建企业微信配置请求"""

    enabled: bool = True
    key: str
    msg_type: str = "text"
    template: str = ""
    types: str = "all"


class WeComConfigUpdate(BaseModel):
    """更新企业微信配置请求"""

    enabled: Optional[bool] = None
    key: Optional[str] = None
    msg_type: Optional[str] = None
    template: Optional[str] = None
    types: Optional[str] = None


class DingTalkConfigCreate(BaseModel):
    """创建钉钉配置请求"""

    enabled: bool = True
    access_token: str
    secret: str = ""
    msg_type: str = "text"
    template: str = ""
    types: str = "all"


class DingTalkConfigUpdate(BaseModel):
    """更新钉钉配置请求"""

    enabled: Optional[bool] = None
    access_token: Optional[str] = None
    secret: Optional[str] = None
    msg_type: Optional[str] = None
    template: Optional[str] = None
    types: Optional[str] = None


class NotificationRuleCreate(BaseModel):
    """创建通知规则请求"""

    name: str
    enabled: bool = True
    types: str = "all"
    channels: str = ""
    template: str = ""


class NotificationRuleUpdate(BaseModel):
    """更新通知规则请求"""

    name: Optional[str] = None
    enabled: Optional[bool] = None
    types: Optional[str] = None
    channels: Optional[str] = None
    template: Optional[str] = None


@router.post("/notification/test")
async def test_notification(
    request: NotificationTestRequest,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """测试通知功能"""
    try:
        notifier = get_notifier()
        notification_type = request.notification_type or "all"

        # 根据类型测试特定的通知方式
        if notification_type == "all":
            results = await asyncio.to_thread(notifier.test_notification)
        else:
            results = await asyncio.to_thread(
                notifier.test_notification,
                notification_type=notification_type,
                webhook_id=request.webhook_id,
                email_id=request.email_id,
            )

        return {"status": "success", "data": results}
    except Exception as e:
        logger.error(f"测试通知失败: {e}")
        return {"status": "error", "message": f"测试通知失败: {str(e)}"}


@router.get("/notification/status")
async def get_notification_status(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """获取通知配置状态"""
    try:
        # 获取webhook配置数量
        webhook_count = 0
        webhook_enabled_count = 0
        for section_name in config_manager.get_config_parser().sections():
            if section_name.startswith("notify-webhook-"):
                webhook_count += 1
                section_config = config_manager.get_section(section_name)
                if section_config.get("enabled", False):
                    webhook_enabled_count += 1

        # 获取邮件配置数量
        email_count = 0
        email_enabled_count = 0
        for section_name in config_manager.get_config_parser().sections():
            if section_name.startswith("notify-email-"):
                email_count += 1
                section_config = config_manager.get_section(section_name)
                if section_config.get("enabled", False):
                    email_enabled_count += 1

        # 获取企业微信配置数量
        wecom_count = 0
        wecom_enabled_count = 0
        for section_name in config_manager.get_config_parser().sections():
            if section_name.startswith("notify-wecom-"):
                wecom_count += 1
                section_config = config_manager.get_section(section_name)
                if section_config.get("enabled", False):
                    wecom_enabled_count += 1

        # 获取钉钉配置数量
        dingtalk_count = 0
        dingtalk_enabled_count = 0
        for section_name in config_manager.get_config_parser().sections():
            if section_name.startswith("notify-dingtalk-"):
                dingtalk_count += 1
                section_config = config_manager.get_section(section_name)
                if section_config.get("enabled", False):
                    dingtalk_enabled_count += 1

        # 获取通知规则数量
        rule_count = 0
        rule_enabled_count = 0
        for section_name in config_manager.get_config_parser().sections():
            if section_name.startswith("notify-rule-"):
                rule_count += 1
                section_config = config_manager.get_section(section_name)
                if section_config.get("enabled", False):
                    rule_enabled_count += 1

        return {
            "status": "success",
            "data": {
                "webhook": {
                    "total": webhook_count,
                    "enabled": webhook_enabled_count,
                    "configured": webhook_count > 0,
                },
                "email": {
                    "total": email_count,
                    "enabled": email_enabled_count,
                    "configured": email_count > 0,
                },
                "wecom": {
                    "total": wecom_count,
                    "enabled": wecom_enabled_count,
                    "configured": wecom_count > 0,
                },
                "dingtalk": {
                    "total": dingtalk_count,
                    "enabled": dingtalk_enabled_count,
                    "configured": dingtalk_count > 0,
                },
                "rule": {
                    "total": rule_count,
                    "enabled": rule_enabled_count,
                    "configured": rule_count > 0,
                },
            },
        }
    except Exception as e:
        logger.error(f"获取通知状态失败: {e}")
        return {"status": "error", "message": f"获取通知状态失败: {str(e)}"}


# ========== Webhook配置CRUD接口 ==========


@router.get("/notification/webhooks")
async def get_webhooks(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有webhook配置"""
    try:
        webhook_configs = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith("notify-webhook-"):
                section_config = config_manager.get_section(section_name)
                webhook_configs.append(
                    {
                        "id": section_config.get("id"),
                        "enabled": section_config.get("enabled", False),
                        "url": section_config.get("url", ""),
                        "method": section_config.get("method", "POST"),
                        "headers": section_config.get("headers", ""),
                        "template": section_config.get("template", ""),
                        "types": section_config.get("types", "all"),
                    }
                )

        # 按ID排序
        webhook_configs.sort(key=lambda x: int(x["id"]))

        return {"status": "success", "data": webhook_configs}
    except Exception as e:
        logger.error(f"获取webhook配置失败: {e}")
        return {"status": "error", "message": f"获取webhook配置失败: {str(e)}"}


@router.post("/notification/webhooks")
async def create_webhook(
    webhook_data: WebhookConfigCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的webhook配置"""
    try:
        config = config_manager.get_config_parser()

        # 新 ID 取现有最大 ID + 1（避免复用已删除的 ID 导致规则引用错位）
        max_id = 0
        for section_name in config.sections():
            if section_name.startswith("notify-webhook-"):
                section_config = config_manager.get_section(section_name)
                try:
                    max_id = max(max_id, int(section_config.get("id", 0)))
                except (TypeError, ValueError):
                    continue

        new_id = max_id + 1
        section_name = f"notify-webhook-{new_id}"

        # 创建新的配置段
        if not config.has_section(section_name):
            config.add_section(section_name)

        config.set(section_name, "id", str(new_id))
        config.set(section_name, "enabled", str(webhook_data.enabled))
        config.set(section_name, "url", webhook_data.url)
        config.set(section_name, "method", webhook_data.method)
        config.set(section_name, "headers", webhook_data.headers)
        config.set(section_name, "template", webhook_data.template)
        config.set(section_name, "types", webhook_data.types)

        # 保存配置
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"创建webhook配置成功: ID={new_id}")

        return {
            "status": "success",
            "message": "Webhook配置创建成功",
            "data": {
                "id": new_id,
                "enabled": webhook_data.enabled,
                "url": webhook_data.url,
                "method": webhook_data.method,
                "headers": webhook_data.headers,
                "template": webhook_data.template,
                "types": webhook_data.types,
            },
        }
    except Exception as e:
        logger.error(f"创建webhook配置失败: {e}")
        return {"status": "error", "message": f"创建webhook配置失败: {str(e)}"}


@router.put("/notification/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int,
    webhook_data: WebhookConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新webhook配置"""
    try:
        section_name = f"notify-webhook-{webhook_id}"
        config = config_manager.get_config_parser()

        # 检查配置段是否存在
        if not config.has_section(section_name):
            return {"status": "error", "message": f"Webhook配置不存在: ID={webhook_id}"}

        # 更新配置
        if webhook_data.enabled is not None:
            config.set(section_name, "enabled", str(webhook_data.enabled))
        if webhook_data.url is not None:
            config.set(section_name, "url", webhook_data.url)
        if webhook_data.method is not None:
            config.set(section_name, "method", webhook_data.method)
        if webhook_data.headers is not None:
            config.set(section_name, "headers", webhook_data.headers)
        if webhook_data.template is not None:
            config.set(section_name, "template", webhook_data.template)
        if webhook_data.types is not None:
            config.set(section_name, "types", webhook_data.types)

        # 保存配置
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"更新webhook配置成功: ID={webhook_id}")

        # 返回更新后的配置
        section_config = config_manager.get_section(section_name)
        return {
            "status": "success",
            "message": "Webhook配置更新成功",
            "data": {
                "id": webhook_id,
                "enabled": section_config.get("enabled", False),
                "url": section_config.get("url", ""),
                "method": section_config.get("method", "POST"),
                "headers": section_config.get("headers", ""),
                "template": section_config.get("template", ""),
                "types": section_config.get("types", "all"),
            },
        }
    except Exception as e:
        logger.error(f"更新webhook配置失败: {e}")
        return {"status": "error", "message": f"更新webhook配置失败: {str(e)}"}


@router.delete("/notification/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除webhook配置"""
    try:
        section_name = f"notify-webhook-{webhook_id}"
        config = config_manager.get_config_parser()

        # 检查配置段是否存在
        if not config.has_section(section_name):
            return {"status": "error", "message": f"Webhook配置不存在: ID={webhook_id}"}

        # 删除配置段（保留其他段的原始 ID，避免破坏通知规则的渠道引用）
        config.remove_section(section_name)

        # 保存配置
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"删除webhook配置成功: ID={webhook_id}")

        return {"status": "success", "message": "Webhook配置删除成功"}
    except Exception as e:
        logger.error(f"删除webhook配置失败: {e}")
        return {"status": "error", "message": f"删除webhook配置失败: {str(e)}"}


@router.post("/notification/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """测试指定的webhook配置"""
    try:
        notifier = get_notifier()
        results = await asyncio.to_thread(
            notifier.test_notification,
            notification_type="webhook",
            webhook_id=webhook_id,
        )

        return {"status": "success", "data": results}
    except Exception as e:
        logger.error(f"测试webhook失败: {e}")
        return {"status": "error", "message": f"测试webhook失败: {str(e)}"}


# ========== 邮件配置CRUD接口 ==========


@router.get("/notification/templates/{channel}/list")
async def list_templates(
    channel: str,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """列出指定渠道的可用模板"""
    from app.utils.notifier.template_manager import template_manager

    if channel == "email":
        templates = template_manager.list_templates("email", "html")
    elif channel in ("webhook", "wecom", "dingtalk"):
        templates = template_manager.list_templates(channel, "json")
    else:
        templates = []
    return {"status": "success", "data": templates}


@router.get("/notification/templates/{channel}")
async def get_default_template(
    channel: str,
    name: str = "default",
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """获取指定渠道的模板原始内容"""
    from app.utils.notifier.template_manager import template_manager

    if channel == "email":
        content = template_manager.get_template_content("email", name, "html")
    elif channel in ("webhook", "wecom", "dingtalk"):
        content = template_manager.get_template_content(channel, name, "json")
    else:
        content = None
    return {
        "status": "success",
        "data": {"channel": channel, "name": name, "content": content or ""},
    }


@router.get("/notification/emails")
async def get_emails(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有邮件配置"""
    try:
        email_configs = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith("notify-email-"):
                section_config = config_manager.get_section(section_name)
                email_config = {
                    "id": section_config.get("id"),
                    "enabled": section_config.get("enabled", False),
                    "smtp_server": section_config.get("smtp_server", ""),
                    "smtp_port": section_config.get("smtp_port", 587),
                    "smtp_username": section_config.get("smtp_username", ""),
                    "smtp_password": "******"
                    if section_config.get("smtp_password")
                    else "",
                    "smtp_use_tls": section_config.get("smtp_use_tls", True),
                    "email_from": section_config.get("email_from", ""),
                    "email_to": section_config.get("email_to", ""),
                    "email_subject": section_config.get("email_subject", ""),
                    "template": section_config.get("template", ""),
                    "types": section_config.get("types", "mark_failed"),
                }
                email_configs.append(email_config)

        # 按ID排序
        email_configs.sort(key=lambda x: int(x["id"]))

        return {"status": "success", "data": email_configs}
    except Exception as e:
        logger.error(f"获取邮件配置失败: {e}")
        return {"status": "error", "message": f"获取邮件配置失败: {str(e)}"}


@router.post("/notification/emails")
async def create_email(
    email_data: EmailConfigCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的邮件配置"""
    try:
        config = config_manager.get_config_parser()

        # 新 ID 取现有最大 ID + 1（避免复用已删除的 ID 导致规则引用错位）
        max_id = 0
        for section_name in config.sections():
            if section_name.startswith("notify-email-"):
                section_config = config_manager.get_section(section_name)
                try:
                    max_id = max(max_id, int(section_config.get("id", 0)))
                except (TypeError, ValueError):
                    continue

        new_id = max_id + 1
        section_name = f"notify-email-{new_id}"

        # 创建新的配置段
        if not config.has_section(section_name):
            config.add_section(section_name)

        config.set(section_name, "id", str(new_id))
        config.set(section_name, "enabled", str(email_data.enabled))
        config.set(section_name, "smtp_server", email_data.smtp_server)
        config.set(section_name, "smtp_port", str(email_data.smtp_port))
        config.set(section_name, "smtp_username", email_data.smtp_username)
        config.set(
            section_name,
            "smtp_password",
            encrypt_if_sensitive(
                section_name, "smtp_password", email_data.smtp_password
            ),
        )
        config.set(section_name, "smtp_use_tls", str(email_data.smtp_use_tls))
        config.set(section_name, "email_from", email_data.email_from)
        config.set(section_name, "email_to", email_data.email_to)
        config.set(section_name, "email_subject", email_data.email_subject)
        config.set(section_name, "template", email_data.template)
        config.set(section_name, "types", email_data.types)

        # 保存配置
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"创建邮件配置成功: ID={new_id}")

        return {
            "status": "success",
            "message": "邮件配置创建成功",
            "data": {
                "id": new_id,
                "enabled": email_data.enabled,
                "smtp_server": email_data.smtp_server,
                "smtp_port": email_data.smtp_port,
                "smtp_username": email_data.smtp_username,
                "smtp_password": "******" if email_data.smtp_password else "",
                "smtp_use_tls": email_data.smtp_use_tls,
                "email_from": email_data.email_from,
                "email_to": email_data.email_to,
                "email_subject": email_data.email_subject,
                "template": email_data.template,
                "types": email_data.types,
            },
        }
    except Exception as e:
        logger.error(f"创建邮件配置失败: {e}")
        return {"status": "error", "message": f"创建邮件配置失败: {str(e)}"}


@router.put("/notification/emails/{email_id}")
async def update_email(
    email_id: int,
    email_data: EmailConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新邮件配置"""
    try:
        section_name = f"notify-email-{email_id}"
        config = config_manager.get_config_parser()

        # 检查配置段是否存在
        if not config.has_section(section_name):
            return {"status": "error", "message": f"邮件配置不存在: ID={email_id}"}

        # 更新配置
        if email_data.enabled is not None:
            config.set(section_name, "enabled", str(email_data.enabled))
        if email_data.smtp_server is not None:
            config.set(section_name, "smtp_server", email_data.smtp_server)
        if email_data.smtp_port is not None:
            config.set(section_name, "smtp_port", str(email_data.smtp_port))
        if email_data.smtp_username is not None:
            config.set(section_name, "smtp_username", email_data.smtp_username)
        # 只有当密码不为空且不是掩码时才更新密码
        if (
            email_data.smtp_password is not None
            and email_data.smtp_password.strip()
            and email_data.smtp_password != "******"
        ):
            config.set(
                section_name,
                "smtp_password",
                encrypt_if_sensitive(
                    section_name, "smtp_password", email_data.smtp_password
                ),
            )
        if email_data.smtp_use_tls is not None:
            config.set(section_name, "smtp_use_tls", str(email_data.smtp_use_tls))
        if email_data.email_from is not None:
            config.set(section_name, "email_from", email_data.email_from)
        if email_data.email_to is not None:
            config.set(section_name, "email_to", email_data.email_to)
        if email_data.email_subject is not None:
            config.set(section_name, "email_subject", email_data.email_subject)
        if email_data.template is not None:
            config.set(section_name, "template", email_data.template)
        if email_data.types is not None:
            config.set(section_name, "types", email_data.types)

        # 保存配置
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"更新邮件配置成功: ID={email_id}")

        # 返回更新后的配置
        section_config = config_manager.get_section(section_name)
        return {
            "status": "success",
            "message": "邮件配置更新成功",
            "data": {
                "id": email_id,
                "enabled": section_config.get("enabled", False),
                "smtp_server": section_config.get("smtp_server", ""),
                "smtp_port": section_config.get("smtp_port", 587),
                "smtp_username": section_config.get("smtp_username", ""),
                "smtp_password": "******"
                if section_config.get("smtp_password")
                else "",
                "smtp_use_tls": section_config.get("smtp_use_tls", True),
                "email_from": section_config.get("email_from", ""),
                "email_to": section_config.get("email_to", ""),
                "email_subject": section_config.get("email_subject", ""),
                "template": section_config.get("template", ""),
                "types": section_config.get("types", "mark_failed"),
            },
        }
    except Exception as e:
        logger.error(f"更新邮件配置失败: {e}")
        return {"status": "error", "message": f"更新邮件配置失败: {str(e)}"}


@router.delete("/notification/emails/{email_id}")
async def delete_email(
    email_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除邮件配置"""
    try:
        section_name = f"notify-email-{email_id}"
        config = config_manager.get_config_parser()

        # 检查配置段是否存在
        if not config.has_section(section_name):
            return {"status": "error", "message": f"邮件配置不存在: ID={email_id}"}

        # 删除配置段（保留其他段的原始 ID，避免破坏通知规则的渠道引用）
        config.remove_section(section_name)

        # 保存配置
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"删除邮件配置成功: ID={email_id}")

        return {"status": "success", "message": "邮件配置删除成功"}
    except Exception as e:
        logger.error(f"删除邮件配置失败: {e}")
        return {"status": "error", "message": f"删除邮件配置失败: {str(e)}"}


@router.post("/notification/emails/{email_id}/test")
async def test_email(
    email_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """测试指定的邮件配置"""
    try:
        notifier = get_notifier()
        results = await asyncio.to_thread(
            notifier.test_notification,
            notification_type="email",
            email_id=email_id,
        )

        return {"status": "success", "data": results}
    except Exception as e:
        logger.error(f"测试邮件失败: {e}")
        return {"status": "error", "message": f"测试邮件失败: {str(e)}"}


# ========== 企业微信配置CRUD接口 ==========


@router.get("/notification/wecoms")
async def get_wecoms(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有企业微信配置"""
    try:
        wecom_configs = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith("notify-wecom-"):
                section_config = config_manager.get_section(section_name)
                wecom_configs.append(
                    {
                        "id": section_config.get("id"),
                        "enabled": section_config.get("enabled", False),
                        "key": "******" if section_config.get("key") else "",
                        "msg_type": section_config.get("msg_type", "text"),
                        "template": section_config.get("template", ""),
                        "types": section_config.get("types", "all"),
                    }
                )

        wecom_configs.sort(key=lambda x: int(x["id"]))

        return {"status": "success", "data": wecom_configs}
    except Exception as e:
        logger.error(f"获取企业微信配置失败: {e}")
        return {"status": "error", "message": f"获取企业微信配置失败: {str(e)}"}


@router.post("/notification/wecoms")
async def create_wecom(
    wecom_data: WeComConfigCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的企业微信配置"""
    try:
        config = config_manager.get_config_parser()

        # 新 ID 取现有最大 ID + 1（避免复用已删除的 ID 导致规则引用错位）
        max_id = 0
        for section_name in config.sections():
            if section_name.startswith("notify-wecom-"):
                section_config = config_manager.get_section(section_name)
                try:
                    max_id = max(max_id, int(section_config.get("id", 0)))
                except (TypeError, ValueError):
                    continue

        new_id = max_id + 1
        section_name = f"notify-wecom-{new_id}"

        if not config.has_section(section_name):
            config.add_section(section_name)

        config.set(section_name, "id", str(new_id))
        config.set(section_name, "enabled", str(wecom_data.enabled))
        config.set(section_name, "key", wecom_data.key)
        config.set(section_name, "msg_type", wecom_data.msg_type)
        config.set(section_name, "template", wecom_data.template)
        config.set(section_name, "types", wecom_data.types)

        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"创建企业微信配置成功: ID={new_id}")

        return {
            "status": "success",
            "message": "企业微信配置创建成功",
            "data": {
                "id": new_id,
                "enabled": wecom_data.enabled,
                "key": "******" if wecom_data.key else "",
                "msg_type": wecom_data.msg_type,
                "template": wecom_data.template,
                "types": wecom_data.types,
            },
        }
    except Exception as e:
        logger.error(f"创建企业微信配置失败: {e}")
        return {"status": "error", "message": f"创建企业微信配置失败: {str(e)}"}


@router.put("/notification/wecoms/{wecom_id}")
async def update_wecom(
    wecom_id: int,
    wecom_data: WeComConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新企业微信配置"""
    try:
        section_name = f"notify-wecom-{wecom_id}"
        config = config_manager.get_config_parser()

        if not config.has_section(section_name):
            return {"status": "error", "message": f"企业微信配置不存在: ID={wecom_id}"}

        if wecom_data.enabled is not None:
            config.set(section_name, "enabled", str(wecom_data.enabled))
        if (
            wecom_data.key is not None
            and wecom_data.key.strip()
            and wecom_data.key != "******"
        ):
            config.set(
                section_name,
                "key",
                encrypt_if_sensitive(section_name, "key", wecom_data.key),
            )
        if wecom_data.msg_type is not None:
            config.set(section_name, "msg_type", wecom_data.msg_type)
        if wecom_data.template is not None:
            config.set(section_name, "template", wecom_data.template)
        if wecom_data.types is not None:
            config.set(section_name, "types", wecom_data.types)

        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"更新企业微信配置成功: ID={wecom_id}")

        section_config = config_manager.get_section(section_name)
        return {
            "status": "success",
            "message": "企业微信配置更新成功",
            "data": {
                "id": wecom_id,
                "enabled": section_config.get("enabled", False),
                "key": "******" if section_config.get("key") else "",
                "msg_type": section_config.get("msg_type", "text"),
                "template": section_config.get("template", ""),
                "types": section_config.get("types", "all"),
            },
        }
    except Exception as e:
        logger.error(f"更新企业微信配置失败: {e}")
        return {"status": "error", "message": f"更新企业微信配置失败: {str(e)}"}


@router.delete("/notification/wecoms/{wecom_id}")
async def delete_wecom(
    wecom_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除企业微信配置"""
    try:
        section_name = f"notify-wecom-{wecom_id}"
        config = config_manager.get_config_parser()

        if not config.has_section(section_name):
            return {"status": "error", "message": f"企业微信配置不存在: ID={wecom_id}"}

        config.remove_section(section_name)

        # 保留其他段的原始 ID，避免破坏通知规则的渠道引用
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"删除企业微信配置成功: ID={wecom_id}")

        return {"status": "success", "message": "企业微信配置删除成功"}
    except Exception as e:
        logger.error(f"删除企业微信配置失败: {e}")
        return {"status": "error", "message": f"删除企业微信配置失败: {str(e)}"}


@router.post("/notification/wecoms/{wecom_id}/test")
async def test_wecom(
    wecom_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """测试指定的企业微信配置"""
    try:
        import time

        from ..utils.notifier.channels_impl import WeChatWorkChannel

        section_name = f"notify-wecom-{wecom_id}"
        config = config_manager.get_config_parser()
        if not config.has_section(section_name):
            return {"status": "error", "message": f"企业微信配置不存在: ID={wecom_id}"}

        cfg = config_manager.get_section(section_name)
        channel = WeChatWorkChannel(channel_id=section_name, config=cfg)

        payload = {
            "title": "🔧 测试通知",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "anime": "测试番剧",
            "episode": "S1E1",
            "user": "test",
            "source": "test",
            "error": "",
        }
        result = await asyncio.to_thread(channel.send, "mark_success", payload, None)

        return {
            "status": "success" if result.success else "error",
            "message": result.message or "测试成功",
        }
    except Exception as e:
        logger.error(f"测试企业微信失败: {e}")
        return {"status": "error", "message": f"测试企业微信失败: {str(e)}"}


# ========== 钉钉配置CRUD接口 ==========


@router.get("/notification/dingtalks")
async def get_dingtalks(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有钉钉配置"""
    try:
        dingtalk_configs = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith("notify-dingtalk-"):
                section_config = config_manager.get_section(section_name)
                dingtalk_configs.append(
                    {
                        "id": section_config.get("id"),
                        "enabled": section_config.get("enabled", False),
                        "access_token": "******"
                        if section_config.get("access_token")
                        else "",
                        "secret": "******" if section_config.get("secret") else "",
                        "msg_type": section_config.get("msg_type", "text"),
                        "template": section_config.get("template", ""),
                        "types": section_config.get("types", "all"),
                    }
                )

        dingtalk_configs.sort(key=lambda x: int(x["id"]))

        return {"status": "success", "data": dingtalk_configs}
    except Exception as e:
        logger.error(f"获取钉钉配置失败: {e}")
        return {"status": "error", "message": f"获取钉钉配置失败: {str(e)}"}


@router.post("/notification/dingtalks")
async def create_dingtalk(
    dingtalk_data: DingTalkConfigCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的钉钉配置"""
    try:
        config = config_manager.get_config_parser()

        # 新 ID 取现有最大 ID + 1（避免复用已删除的 ID 导致规则引用错位）
        max_id = 0
        for section_name in config.sections():
            if section_name.startswith("notify-dingtalk-"):
                section_config = config_manager.get_section(section_name)
                try:
                    max_id = max(max_id, int(section_config.get("id", 0)))
                except (TypeError, ValueError):
                    continue

        new_id = max_id + 1
        section_name = f"notify-dingtalk-{new_id}"

        if not config.has_section(section_name):
            config.add_section(section_name)

        config.set(section_name, "id", str(new_id))
        config.set(section_name, "enabled", str(dingtalk_data.enabled))
        config.set(section_name, "access_token", dingtalk_data.access_token)
        config.set(
            section_name,
            "secret",
            encrypt_if_sensitive(section_name, "secret", dingtalk_data.secret),
        )
        config.set(section_name, "msg_type", dingtalk_data.msg_type)
        config.set(section_name, "template", dingtalk_data.template)
        config.set(section_name, "types", dingtalk_data.types)

        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"创建钉钉配置成功: ID={new_id}")

        return {
            "status": "success",
            "message": "钉钉配置创建成功",
            "data": {
                "id": new_id,
                "enabled": dingtalk_data.enabled,
                "access_token": "******" if dingtalk_data.access_token else "",
                "secret": "******" if dingtalk_data.secret else "",
                "msg_type": dingtalk_data.msg_type,
                "template": dingtalk_data.template,
                "types": dingtalk_data.types,
            },
        }
    except Exception as e:
        logger.error(f"创建钉钉配置失败: {e}")
        return {"status": "error", "message": f"创建钉钉配置失败: {str(e)}"}


@router.put("/notification/dingtalks/{dingtalk_id}")
async def update_dingtalk(
    dingtalk_id: int,
    dingtalk_data: DingTalkConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新钉钉配置"""
    try:
        section_name = f"notify-dingtalk-{dingtalk_id}"
        config = config_manager.get_config_parser()

        if not config.has_section(section_name):
            return {"status": "error", "message": f"钉钉配置不存在: ID={dingtalk_id}"}

        if dingtalk_data.enabled is not None:
            config.set(section_name, "enabled", str(dingtalk_data.enabled))
        if (
            dingtalk_data.access_token is not None
            and dingtalk_data.access_token.strip()
            and dingtalk_data.access_token != "******"
        ):
            config.set(
                section_name,
                "access_token",
                encrypt_if_sensitive(
                    section_name, "access_token", dingtalk_data.access_token
                ),
            )
        if (
            dingtalk_data.secret is not None
            and dingtalk_data.secret.strip()
            and dingtalk_data.secret != "******"
        ):
            config.set(
                section_name,
                "secret",
                encrypt_if_sensitive(section_name, "secret", dingtalk_data.secret),
            )
        if dingtalk_data.msg_type is not None:
            config.set(section_name, "msg_type", dingtalk_data.msg_type)
        if dingtalk_data.template is not None:
            config.set(section_name, "template", dingtalk_data.template)
        if dingtalk_data.types is not None:
            config.set(section_name, "types", dingtalk_data.types)

        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"更新钉钉配置成功: ID={dingtalk_id}")

        section_config = config_manager.get_section(section_name)
        return {
            "status": "success",
            "message": "钉钉配置更新成功",
            "data": {
                "id": dingtalk_id,
                "enabled": section_config.get("enabled", False),
                "access_token": "******" if section_config.get("access_token") else "",
                "secret": "******" if section_config.get("secret") else "",
                "msg_type": section_config.get("msg_type", "text"),
                "template": section_config.get("template", ""),
                "types": section_config.get("types", "all"),
            },
        }
    except Exception as e:
        logger.error(f"更新钉钉配置失败: {e}")
        return {"status": "error", "message": f"更新钉钉配置失败: {str(e)}"}


@router.delete("/notification/dingtalks/{dingtalk_id}")
async def delete_dingtalk(
    dingtalk_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除钉钉配置"""
    try:
        section_name = f"notify-dingtalk-{dingtalk_id}"
        config = config_manager.get_config_parser()

        if not config.has_section(section_name):
            return {"status": "error", "message": f"钉钉配置不存在: ID={dingtalk_id}"}

        config.remove_section(section_name)

        # 保留其他段的原始 ID，避免破坏通知规则的渠道引用
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"删除钉钉配置成功: ID={dingtalk_id}")

        return {"status": "success", "message": "钉钉配置删除成功"}
    except Exception as e:
        logger.error(f"删除钉钉配置失败: {e}")
        return {"status": "error", "message": f"删除钉钉配置失败: {str(e)}"}


@router.post("/notification/dingtalks/{dingtalk_id}/test")
async def test_dingtalk(
    dingtalk_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """测试指定的钉钉配置"""
    try:
        import time

        from ..utils.notifier.channels_impl import DingTalkChannel

        section_name = f"notify-dingtalk-{dingtalk_id}"
        config = config_manager.get_config_parser()
        if not config.has_section(section_name):
            return {"status": "error", "message": f"钉钉配置不存在: ID={dingtalk_id}"}

        cfg = config_manager.get_section(section_name)
        channel = DingTalkChannel(channel_id=section_name, config=cfg)

        payload = {
            "title": "🔧 测试通知",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "anime": "测试番剧",
            "episode": "S1E1",
            "user": "test",
            "source": "test",
            "error": "",
        }
        result = await asyncio.to_thread(channel.send, "mark_success", payload, None)

        return {
            "status": "success" if result.success else "error",
            "message": result.message or "测试成功",
        }
    except Exception as e:
        logger.error(f"测试钉钉失败: {e}")
        return {"status": "error", "message": f"测试钉钉失败: {str(e)}"}


# ========== 通知规则CRUD接口 ==========


@router.get("/notification/rules")
async def get_notification_rules(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """获取所有通知规则"""
    try:
        rules = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith("notify-rule-"):
                section_config = config_manager.get_section(section_name)
                rules.append(
                    {
                        "id": section_config.get("id"),
                        "name": section_config.get("name", ""),
                        "enabled": section_config.get("enabled", False),
                        "types": section_config.get("types", "all"),
                        "channels": section_config.get("channels", ""),
                        "template": section_config.get("template", ""),
                    }
                )

        rules.sort(key=lambda x: int(x["id"]))

        return {"status": "success", "data": rules}
    except Exception as e:
        logger.error(f"获取通知规则失败: {e}")
        return {"status": "error", "message": f"获取通知规则失败: {str(e)}"}


@router.post("/notification/rules")
async def create_notification_rule(
    rule_data: NotificationRuleCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的通知规则"""
    try:
        config = config_manager.get_config_parser()

        # 新 ID 取现有最大 ID + 1（避免复用已删除的 ID）
        max_id = 0
        for section_name in config.sections():
            if section_name.startswith("notify-rule-"):
                section_config = config_manager.get_section(section_name)
                try:
                    max_id = max(max_id, int(section_config.get("id", 0)))
                except (TypeError, ValueError):
                    continue

        new_id = max_id + 1
        section_name = f"notify-rule-{new_id}"

        if not config.has_section(section_name):
            config.add_section(section_name)

        config.set(section_name, "id", str(new_id))
        config.set(section_name, "name", rule_data.name)
        config.set(section_name, "enabled", str(rule_data.enabled))
        config.set(section_name, "types", rule_data.types)
        config.set(section_name, "channels", rule_data.channels)
        config.set(section_name, "template", rule_data.template)

        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"创建通知规则成功: ID={new_id} name={rule_data.name}")

        return {
            "status": "success",
            "message": "通知规则创建成功",
            "data": {
                "id": new_id,
                "name": rule_data.name,
                "enabled": rule_data.enabled,
                "types": rule_data.types,
                "channels": rule_data.channels,
                "template": rule_data.template,
            },
        }
    except Exception as e:
        logger.error(f"创建通知规则失败: {e}")
        return {"status": "error", "message": f"创建通知规则失败: {str(e)}"}


@router.put("/notification/rules/{rule_id}")
async def update_notification_rule(
    rule_id: int,
    rule_data: NotificationRuleUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新通知规则"""
    try:
        section_name = f"notify-rule-{rule_id}"
        config = config_manager.get_config_parser()

        if not config.has_section(section_name):
            return {"status": "error", "message": f"通知规则不存在: ID={rule_id}"}

        if rule_data.name is not None:
            config.set(section_name, "name", rule_data.name)
        if rule_data.enabled is not None:
            config.set(section_name, "enabled", str(rule_data.enabled))
        if rule_data.types is not None:
            config.set(section_name, "types", rule_data.types)
        if rule_data.channels is not None:
            config.set(section_name, "channels", rule_data.channels)
        if rule_data.template is not None:
            config.set(section_name, "template", rule_data.template)

        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"更新通知规则成功: ID={rule_id}")

        section_config = config_manager.get_section(section_name)
        return {
            "status": "success",
            "message": "通知规则更新成功",
            "data": {
                "id": rule_id,
                "name": section_config.get("name", ""),
                "enabled": section_config.get("enabled", False),
                "types": section_config.get("types", "all"),
                "channels": section_config.get("channels", ""),
                "template": section_config.get("template", ""),
            },
        }
    except Exception as e:
        logger.error(f"更新通知规则失败: {e}")
        return {"status": "error", "message": f"更新通知规则失败: {str(e)}"}


@router.delete("/notification/rules/{rule_id}")
async def delete_notification_rule(
    rule_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除通知规则"""
    try:
        section_name = f"notify-rule-{rule_id}"
        config = config_manager.get_config_parser()

        if not config.has_section(section_name):
            return {"status": "error", "message": f"通知规则不存在: ID={rule_id}"}

        config.remove_section(section_name)

        # 保留其他段的原始 ID，避免引用错位
        await asyncio.to_thread(config_manager._save_config, config)
        _reload_notification_channels()

        logger.info(f"删除通知规则成功: ID={rule_id}")

        return {"status": "success", "message": "通知规则删除成功"}
    except Exception as e:
        logger.error(f"删除通知规则失败: {e}")
        return {"status": "error", "message": f"删除通知规则失败: {str(e)}"}


@router.get("/notification/channels")
async def get_notification_channels(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """获取所有通知渠道（用于规则编辑时选择）"""
    try:
        channels = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith("notify-webhook-"):
                cfg = config_manager.get_section(section_name)
                channels.append(
                    {
                        "id": cfg.get("id"),
                        "type": "webhook",
                        "label": f"Webhook #{cfg.get('id', '')}",
                        "enabled": cfg.get("enabled", False),
                        "identifier": section_name,
                    }
                )
            elif section_name.startswith("notify-email-"):
                cfg = config_manager.get_section(section_name)
                channels.append(
                    {
                        "id": cfg.get("id"),
                        "type": "email",
                        "label": f"邮件 #{cfg.get('id', '')}",
                        "enabled": cfg.get("enabled", False),
                        "identifier": section_name,
                    }
                )
            elif section_name.startswith("notify-wecom-"):
                cfg = config_manager.get_section(section_name)
                channels.append(
                    {
                        "id": cfg.get("id"),
                        "type": "wecom",
                        "label": f"企业微信 #{cfg.get('id', '')}",
                        "enabled": cfg.get("enabled", False),
                        "identifier": section_name,
                    }
                )
            elif section_name.startswith("notify-dingtalk-"):
                cfg = config_manager.get_section(section_name)
                channels.append(
                    {
                        "id": cfg.get("id"),
                        "type": "dingtalk",
                        "label": f"钉钉 #{cfg.get('id', '')}",
                        "enabled": cfg.get("enabled", False),
                        "identifier": section_name,
                    }
                )

        return {"status": "success", "data": channels}
    except Exception as e:
        logger.error(f"获取通知渠道失败: {e}")
        return {"status": "error", "message": f"获取通知渠道失败: {str(e)}"}


@router.get("/notification/types")
async def get_notification_types(
    request: Request,
    _: Any = Depends(get_current_user_flexible),
) -> dict:
    """返回所有通知类型元数据，供前端动态加载复选框

    替代原先硬编码在 config.html 中的 13 种类型复选框。
    """
    from ..core.notification_registry import CATEGORIES, ui_visible_types

    types = [
        {
            "id": t.id,
            "display_name": t.display_name,
            "icon": t.icon,
            "color": t.color,
            "description": t.description,
            "is_item_level": t.is_item_level,
            "category": t.category,
        }
        for t in ui_visible_types()
    ]
    # 附加分类定义，前端动态读取，避免前后端重复维护
    categories = [{"id": k, "name": v} for k, v in CATEGORIES.items()]
    return {"status": "success", "data": types, "categories": categories}
