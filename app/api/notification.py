"""
通知API
"""
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from typing import Optional
from ..core.logging import logger
from ..utils.notifier import get_notifier
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api")


class NotificationTestRequest(BaseModel):
    """通知测试请求"""
    notification_type: Optional[str] = "webhook"  # webhook, email, all
    webhook_id: Optional[int] = None  # 指定测试的webhook ID


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


@router.post("/notification/test")
async def test_notification(
    request: NotificationTestRequest,
    current_user: dict = Depends(get_current_user_flexible)
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
                webhook_id=request.webhook_id
            )

        return {
            "status": "success",
            "data": results
        }
    except Exception as e:
        logger.error(f"测试通知失败: {e}")
        return {
            "status": "error",
            "message": f"测试通知失败: {str(e)}"
        }


@router.get("/notification/status")
async def get_notification_status(
    request: Request,
    current_user: dict = Depends(get_current_user_flexible)
):
    """获取通知配置状态"""
    try:
        from ..core.config import config_manager

        webhook_enabled = config_manager.get('notification', 'webhook_enabled', fallback=False)
        webhook_url = config_manager.get('notification', 'webhook_url', fallback='')

        email_enabled = config_manager.get('notification', 'email_enabled', fallback=False)
        smtp_server = config_manager.get('notification', 'smtp_server', fallback='')
        email_to = config_manager.get('notification', 'email_to', fallback='')

        return {
            "status": "success",
            "data": {
                "webhook": {
                    "enabled": bool(webhook_enabled),
                    "configured": bool(webhook_url),
                    "url": webhook_url if webhook_url else None
                },
                "email": {
                    "enabled": bool(email_enabled),
                    "configured": bool(smtp_server and email_to),
                    "smtp_server": smtp_server if smtp_server else None,
                    "email_to": email_to if email_to else None
                }
            }
        }
    except Exception as e:
        logger.error(f"获取通知状态失败: {e}")
        return {
            "status": "error",
            "message": f"获取通知状态失败: {str(e)}"
        }


# ========== Webhook配置CRUD接口 ==========

@router.get("/notification/webhooks")
async def get_webhooks(
    current_user: dict = Depends(get_current_user_flexible)
):
    """获取所有webhook配置"""
    try:
        from ..core.config import config_manager

        webhook_configs = []
        config = config_manager.get_config_parser()

        for section_name in config.sections():
            if section_name.startswith('webhook-'):
                section_config = config_manager.get_section(section_name)
                webhook_configs.append({
                    'id': section_config.get('id'),
                    'enabled': section_config.get('enabled', False),
                    'url': section_config.get('url', ''),
                    'method': section_config.get('method', 'POST'),
                    'headers': section_config.get('headers', ''),
                    'template': section_config.get('template', ''),
                    'types': section_config.get('types', 'all')
                })

        # 按ID排序
        webhook_configs.sort(key=lambda x: int(x['id']))

        return {
            "status": "success",
            "data": webhook_configs
        }
    except Exception as e:
        logger.error(f"获取webhook配置失败: {e}")
        return {
            "status": "error",
            "message": f"获取webhook配置失败: {str(e)}"
        }


@router.post("/notification/webhooks")
async def create_webhook(
    webhook_data: WebhookConfigCreate,
    current_user: dict = Depends(get_current_user_flexible)
):
    """创建新的webhook配置"""
    try:
        from ..core.config import config_manager

        config = config_manager.get_config_parser()

        # 计算当前webhook配置的数量
        webhook_count = 0
        for section_name in config.sections():
            if section_name.startswith('webhook-'):
                webhook_count += 1

        # 新ID为当前数量+1
        new_id = webhook_count + 1
        section_name = f'webhook-{new_id}'

        # 创建新的配置段
        if not config.has_section(section_name):
            config.add_section(section_name)

        config.set(section_name, 'id', str(new_id))
        config.set(section_name, 'enabled', str(webhook_data.enabled))
        config.set(section_name, 'url', webhook_data.url)
        config.set(section_name, 'method', webhook_data.method)
        config.set(section_name, 'headers', webhook_data.headers)
        config.set(section_name, 'template', webhook_data.template)
        config.set(section_name, 'types', webhook_data.types)

        # 保存配置
        config_manager._save_config(config)

        logger.info(f'创建webhook配置成功: ID={new_id}')

        return {
            "status": "success",
            "message": "Webhook配置创建成功",
            "data": {
                'id': new_id,
                'enabled': webhook_data.enabled,
                'url': webhook_data.url,
                'method': webhook_data.method,
                'headers': webhook_data.headers,
                'template': webhook_data.template,
                'types': webhook_data.types
            }
        }
    except Exception as e:
        logger.error(f"创建webhook配置失败: {e}")
        return {
            "status": "error",
            "message": f"创建webhook配置失败: {str(e)}"
        }


@router.put("/notification/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int,
    webhook_data: WebhookConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible)
):
    """更新webhook配置"""
    try:
        from ..core.config import config_manager

        section_name = f'webhook-{webhook_id}'
        config = config_manager.get_config_parser()

        # 检查配置段是否存在
        if not config.has_section(section_name):
            return {
                "status": "error",
                "message": f"Webhook配置不存在: ID={webhook_id}"
            }

        # 更新配置
        if webhook_data.enabled is not None:
            config.set(section_name, 'enabled', str(webhook_data.enabled))
        if webhook_data.url is not None:
            config.set(section_name, 'url', webhook_data.url)
        if webhook_data.method is not None:
            config.set(section_name, 'method', webhook_data.method)
        if webhook_data.headers is not None:
            config.set(section_name, 'headers', webhook_data.headers)
        if webhook_data.template is not None:
            config.set(section_name, 'template', webhook_data.template)
        if webhook_data.types is not None:
            config.set(section_name, 'types', webhook_data.types)

        # 保存配置
        config_manager._save_config(config)

        logger.info(f'更新webhook配置成功: ID={webhook_id}')

        # 返回更新后的配置
        section_config = config_manager.get_section(section_name)
        return {
            "status": "success",
            "message": "Webhook配置更新成功",
            "data": {
                'id': webhook_id,
                'enabled': section_config.get('enabled', False),
                'url': section_config.get('url', ''),
                'method': section_config.get('method', 'POST'),
                'headers': section_config.get('headers', ''),
                'template': section_config.get('template', ''),
                'types': section_config.get('types', 'all')
            }
        }
    except Exception as e:
        logger.error(f"更新webhook配置失败: {e}")
        return {
            "status": "error",
            "message": f"更新webhook配置失败: {str(e)}"
        }


@router.delete("/notification/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    current_user: dict = Depends(get_current_user_flexible)
):
    """删除webhook配置"""
    try:
        from ..core.config import config_manager

        section_name = f'webhook-{webhook_id}'
        config = config_manager.get_config_parser()

        # 检查配置段是否存在
        if not config.has_section(section_name):
            return {
                "status": "error",
                "message": f"Webhook配置不存在: ID={webhook_id}"
            }

        # 删除配置段
        config.remove_section(section_name)

        # 重新索引剩余的webhook配置
        webhook_sections = []
        for section_name in config.sections():
            if section_name.startswith('webhook-'):
                section_config = config_manager.get_section(section_name)
                webhook_sections.append({
                    'section_name': section_name,
                    'config': section_config
                })

        # 按原始ID排序
        webhook_sections.sort(key=lambda x: int(x['config'].get('id', 0)))

        # 重新分配ID（从1开始）
        for new_id, webhook in enumerate(webhook_sections, 1):
            old_section_name = webhook['section_name']
            new_section_name = f'webhook-{new_id}'

            # 如果配置段名称需要更改
            if old_section_name != new_section_name:
                # 创建新的配置段
                config.add_section(new_section_name)
                # 复制所有配置项
                for key, value in webhook['config'].items():
                    config.set(new_section_name, key, str(value))
                # 更新ID
                config.set(new_section_name, 'id', str(new_id))
                # 删除旧的配置段
                config.remove_section(old_section_name)
            else:
                # 只需要更新ID
                config.set(old_section_name, 'id', str(new_id))

        # 保存配置
        config_manager._save_config(config)

        logger.info(f'删除webhook配置成功: ID={webhook_id}，已重新索引')

        return {
            "status": "success",
            "message": "Webhook配置删除成功"
        }
    except Exception as e:
        logger.error(f"删除webhook配置失败: {e}")
        return {
            "status": "error",
            "message": f"删除webhook配置失败: {str(e)}"
        }


@router.post("/notification/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: int,
    current_user: dict = Depends(get_current_user_flexible)
):
    """测试指定的webhook配置"""
    try:
        notifier = get_notifier()
        results = notifier.test_notification(notification_type='webhook', webhook_id=webhook_id)

        return {
            "status": "success",
            "data": results
        }
    except Exception as e:
        logger.error(f"测试webhook失败: {e}")
        return {
            "status": "error",
            "message": f"测试webhook失败: {str(e)}"
        }


