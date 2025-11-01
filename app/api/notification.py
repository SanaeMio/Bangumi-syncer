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
            results = notifier.test_notification(notification_type=notification_type)
        
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


