"""
通知API
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..core.logging import logger
from ..utils.notifier import get_notifier
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api")


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
    email_template_file: str = ""
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
    email_template_file: Optional[str] = None
    types: Optional[str] = None


@router.post("/notification/test")
async def test_notification(
    request: NotificationTestRequest,
    current_user: dict = Depends(get_current_user_flexible),
):
    """测试通知功能"""
    try:
        notifier = get_notifier()
        notification_type = request.notification_type or "all"

        # 根据类型测试特定的通知方式
        if notification_type == "all":
            results = notifier.test_notification()
        else:
            results = notifier.test_notification(
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
):
    """获取通知配置状态"""
    try:
        from ..core.config import config_manager

        # 获取webhook配置数量
        webhook_count = 0
        webhook_enabled_count = 0
        for section_name in config_manager.get_config_parser().sections():
            if section_name.startswith("webhook-"):
                webhook_count += 1
                section_config = config_manager.get_section(section_name)
                if section_config.get("enabled", False):
                    webhook_enabled_count += 1

        # 获取邮件配置数量
        email_count = 0
        email_enabled_count = 0
        for section_name in config_manager.get_config_parser().sections():
            if section_name.startswith("email-"):
                email_count += 1
                section_config = config_manager.get_section(section_name)
                if section_config.get("enabled", False):
                    email_enabled_count += 1

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
        from ..core.config import config_manager

        webhook_configs = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith("webhook-"):
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
):
    """创建新的webhook配置"""
    try:
        from ..core.config import config_manager

        config = config_manager.get_config_parser()

        # 计算当前webhook配置的数量
        webhook_count = 0
        for section_name in config.sections():
            if section_name.startswith("webhook-"):
                webhook_count += 1

        # 新ID为当前数量+1
        new_id = webhook_count + 1
        section_name = f"webhook-{new_id}"

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
        config_manager._save_config(config)

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
):
    """更新webhook配置"""
    try:
        from ..core.config import config_manager

        section_name = f"webhook-{webhook_id}"
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
        config_manager._save_config(config)

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
):
    """删除webhook配置"""
    try:
        from ..core.config import config_manager

        section_name = f"webhook-{webhook_id}"
        config = config_manager.get_config_parser()

        # 检查配置段是否存在
        if not config.has_section(section_name):
            return {"status": "error", "message": f"Webhook配置不存在: ID={webhook_id}"}

        # 删除配置段
        config.remove_section(section_name)

        # 重新索引剩余的webhook配置
        webhook_sections = []
        for section_name in config.sections():
            if section_name.startswith("webhook-"):
                section_config = config_manager.get_section(section_name)
                webhook_sections.append(
                    {"section_name": section_name, "config": section_config}
                )

        # 按原始ID排序
        webhook_sections.sort(key=lambda x: int(x["config"].get("id", 0)))

        # 重新分配ID（从1开始）
        for new_id, webhook in enumerate(webhook_sections, 1):
            old_section_name = webhook["section_name"]
            new_section_name = f"webhook-{new_id}"

            # 如果配置段名称需要更改
            if old_section_name != new_section_name:
                # 创建新的配置段
                config.add_section(new_section_name)
                # 复制所有配置项
                for key, value in webhook["config"].items():
                    config.set(new_section_name, key, str(value))
                # 更新ID
                config.set(new_section_name, "id", str(new_id))
                # 删除旧的配置段
                config.remove_section(old_section_name)
            else:
                # 只需要更新ID
                config.set(old_section_name, "id", str(new_id))

        # 保存配置
        config_manager._save_config(config)

        logger.info(f"删除webhook配置成功: ID={webhook_id}，已重新索引")

        return {"status": "success", "message": "Webhook配置删除成功"}
    except Exception as e:
        logger.error(f"删除webhook配置失败: {e}")
        return {"status": "error", "message": f"删除webhook配置失败: {str(e)}"}


@router.post("/notification/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: int, current_user: dict = Depends(get_current_user_flexible)
):
    """测试指定的webhook配置"""
    try:
        notifier = get_notifier()
        results = notifier.test_notification(
            notification_type="webhook", webhook_id=webhook_id
        )

        return {"status": "success", "data": results}
    except Exception as e:
        logger.error(f"测试webhook失败: {e}")
        return {"status": "error", "message": f"测试webhook失败: {str(e)}"}


# ========== 邮件配置CRUD接口 ==========


@router.get("/notification/emails")
async def get_emails(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有邮件配置"""
    try:
        from ..core.config import config_manager

        email_configs = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith("email-"):
                section_config = config_manager.get_section(section_name)
                # 不返回密码字段（或返回掩码）
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
                    "email_template_file": section_config.get(
                        "email_template_file", ""
                    ),
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
):
    """创建新的邮件配置"""
    try:
        from ..core.config import config_manager

        config = config_manager.get_config_parser()

        # 计算当前邮件配置的数量
        email_count = 0
        for section_name in config.sections():
            if section_name.startswith("email-"):
                email_count += 1

        # 新ID为当前数量+1
        new_id = email_count + 1
        section_name = f"email-{new_id}"

        # 创建新的配置段
        if not config.has_section(section_name):
            config.add_section(section_name)

        config.set(section_name, "id", str(new_id))
        config.set(section_name, "enabled", str(email_data.enabled))
        config.set(section_name, "smtp_server", email_data.smtp_server)
        config.set(section_name, "smtp_port", str(email_data.smtp_port))
        config.set(section_name, "smtp_username", email_data.smtp_username)
        config.set(section_name, "smtp_password", email_data.smtp_password)
        config.set(section_name, "smtp_use_tls", str(email_data.smtp_use_tls))
        config.set(section_name, "email_from", email_data.email_from)
        config.set(section_name, "email_to", email_data.email_to)
        config.set(section_name, "email_subject", email_data.email_subject)
        config.set(section_name, "email_template_file", email_data.email_template_file)
        config.set(section_name, "types", email_data.types)

        # 保存配置
        config_manager._save_config(config)

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
                "smtp_password": "******",
                "smtp_use_tls": email_data.smtp_use_tls,
                "email_from": email_data.email_from,
                "email_to": email_data.email_to,
                "email_subject": email_data.email_subject,
                "email_template_file": email_data.email_template_file,
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
):
    """更新邮件配置"""
    try:
        from ..core.config import config_manager

        section_name = f"email-{email_id}"
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
            config.set(section_name, "smtp_password", email_data.smtp_password)
        if email_data.smtp_use_tls is not None:
            config.set(section_name, "smtp_use_tls", str(email_data.smtp_use_tls))
        if email_data.email_from is not None:
            config.set(section_name, "email_from", email_data.email_from)
        if email_data.email_to is not None:
            config.set(section_name, "email_to", email_data.email_to)
        if email_data.email_subject is not None:
            config.set(section_name, "email_subject", email_data.email_subject)
        if email_data.email_template_file is not None:
            config.set(
                section_name, "email_template_file", email_data.email_template_file
            )
        if email_data.types is not None:
            config.set(section_name, "types", email_data.types)

        # 保存配置
        config_manager._save_config(config)

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
                "smtp_password": "******",
                "smtp_use_tls": section_config.get("smtp_use_tls", True),
                "email_from": section_config.get("email_from", ""),
                "email_to": section_config.get("email_to", ""),
                "email_subject": section_config.get("email_subject", ""),
                "email_template_file": section_config.get("email_template_file", ""),
                "types": section_config.get("types", "mark_failed"),
            },
        }
    except Exception as e:
        logger.error(f"更新邮件配置失败: {e}")
        return {"status": "error", "message": f"更新邮件配置失败: {str(e)}"}


@router.delete("/notification/emails/{email_id}")
async def delete_email(
    email_id: int, current_user: dict = Depends(get_current_user_flexible)
):
    """删除邮件配置"""
    try:
        from ..core.config import config_manager

        section_name = f"email-{email_id}"
        config = config_manager.get_config_parser()

        # 检查配置段是否存在
        if not config.has_section(section_name):
            return {"status": "error", "message": f"邮件配置不存在: ID={email_id}"}

        # 删除配置段
        config.remove_section(section_name)

        # 重新索引剩余的邮件配置
        email_sections = []
        for section_name in config.sections():
            if section_name.startswith("email-"):
                section_config = config_manager.get_section(section_name)
                email_sections.append(
                    {"section_name": section_name, "config": section_config}
                )

        # 按原始ID排序
        email_sections.sort(key=lambda x: int(x["config"].get("id", 0)))

        # 重新分配ID（从1开始）
        for new_id, email in enumerate(email_sections, 1):
            old_section_name = email["section_name"]
            new_section_name = f"email-{new_id}"

            # 如果配置段名称需要更改
            if old_section_name != new_section_name:
                # 创建新的配置段
                config.add_section(new_section_name)
                # 复制所有配置项
                for key, value in email["config"].items():
                    config.set(new_section_name, key, str(value))
                # 更新ID
                config.set(new_section_name, "id", str(new_id))
                # 删除旧的配置段
                config.remove_section(old_section_name)
            else:
                # 只需要更新ID
                config.set(old_section_name, "id", str(new_id))

        # 保存配置
        config_manager._save_config(config)

        logger.info(f"删除邮件配置成功: ID={email_id}，已重新索引")

        return {"status": "success", "message": "邮件配置删除成功"}
    except Exception as e:
        logger.error(f"删除邮件配置失败: {e}")
        return {"status": "error", "message": f"删除邮件配置失败: {str(e)}"}


@router.post("/notification/emails/{email_id}/test")
async def test_email(
    email_id: int, current_user: dict = Depends(get_current_user_flexible)
):
    """测试指定的邮件配置"""
    try:
        notifier = get_notifier()
        results = notifier.test_notification(
            notification_type="email", email_id=email_id
        )

        return {"status": "success", "data": results}
    except Exception as e:
        logger.error(f"测试邮件失败: {e}")
        return {"status": "error", "message": f"测试邮件失败: {str(e)}"}
