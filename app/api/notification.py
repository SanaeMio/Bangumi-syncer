"""
通知API
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional

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


# ========== 通用渠道 CRUD 处理器 ==========
# webhook/email/wecom/dingtalk/rules 5 个渠道的 CRUD 逻辑高度同构，
# 差异仅在：section 前缀、字段列表、敏感字段加密、test 实现。
# ChannelHandler 通过字段定义 + 可选 test_fn 参数化这些差异，
# 消除 5×5=25 个路由函数中的重复 try/except/section 遍历/max_id 计算逻辑。


@dataclass
class ChannelField:
    """渠道配置字段定义

    kind: "str" | "bool" | "int" —— 决定 set 时的 str() 转换
    sensitive: True 时 create/update 走 encrypt_if_sensitive，list 返回掩码 "******"
    """

    name: str
    kind: str = "str"
    sensitive: bool = False
    default: Any = ""


class ChannelHandler:
    """通用渠道 CRUD 处理器

    封装 list/create/update/delete/test 五个操作的模板逻辑，
    通过 section_prefix + fields + test_fn 参数化渠道差异。
    """

    def __init__(
        self,
        section_prefix: str,
        display_name: str,
        fields: list[ChannelField],
        create_model: type,
        update_model: type,
        test_fn: Optional[Callable[[int], Any]] = None,
        label: Optional[str] = None,
    ):
        self.section_prefix = section_prefix
        self.display_name = display_name
        self.fields = fields
        self.create_model = create_model
        self.update_model = update_model
        self.test_fn = test_fn
        # label 用于 get_notification_channels 的前端显示名，默认同 display_name
        self.label = label or display_name

    def _section_name(self, item_id: int) -> str:
        return f"{self.section_prefix}{item_id}"

    def _read_config(self, section_config) -> dict[str, Any]:
        """从 section_config 读取字段，构造返回字典（敏感字段掩码）"""
        data: dict[str, Any] = {"id": section_config.get("id")}
        for f in self.fields:
            if f.sensitive:
                data[f.name] = "******" if section_config.get(f.name) else ""
            else:
                data[f.name] = section_config.get(f.name, f.default)
        return data

    def _set_field_create(
        self, config, section_name: str, f: ChannelField, value: Any
    ) -> None:
        """create 时写入字段（value 来自 pydantic model，非 None）"""
        if f.sensitive:
            config.set(
                section_name,
                f.name,
                encrypt_if_sensitive(section_name, f.name, value),
            )
        else:
            config.set(section_name, f.name, str(value) if f.kind != "str" else value)

    async def list_all(self) -> dict[str, Any]:
        try:
            configs = []
            config = config_manager.get_config_parser()
            for section_name in config.sections():
                if section_name.startswith(self.section_prefix):
                    section_config = config_manager.get_section(section_name)
                    configs.append(self._read_config(section_config))
            configs.sort(key=lambda x: int(x["id"]))
            return {"status": "success", "data": configs}
        except Exception as e:
            logger.error(f"获取{self.display_name}配置失败: {e}")
            return {
                "status": "error",
                "message": f"获取{self.display_name}配置失败: {str(e)}",
            }

    async def create(self, data) -> dict[str, Any]:
        try:
            config = config_manager.get_config_parser()

            # 新 ID 取现有最大 ID + 1（避免复用已删除的 ID 导致规则引用错位）
            max_id = 0
            for section_name in config.sections():
                if section_name.startswith(self.section_prefix):
                    section_config = config_manager.get_section(section_name)
                    try:
                        max_id = max(max_id, int(section_config.get("id", 0)))
                    except (TypeError, ValueError):
                        continue

            new_id = max_id + 1
            section_name = self._section_name(new_id)

            if not config.has_section(section_name):
                config.add_section(section_name)

            config.set(section_name, "id", str(new_id))
            for f in self.fields:
                self._set_field_create(config, section_name, f, getattr(data, f.name))

            await asyncio.to_thread(config_manager._save_config, config)
            _reload_notification_channels()

            logger.info(f"创建{self.display_name}配置成功: ID={new_id}")

            # 构造 create 返回 data：敏感字段掩码，其他字段原值
            result: dict[str, Any] = {"id": new_id}
            for f in self.fields:
                value = getattr(data, f.name)
                if f.sensitive:
                    result[f.name] = "******" if value else ""
                else:
                    result[f.name] = value

            return {
                "status": "success",
                "message": f"{self.display_name}配置创建成功",
                "data": result,
            }
        except Exception as e:
            logger.error(f"创建{self.display_name}配置失败: {e}")
            return {
                "status": "error",
                "message": f"创建{self.display_name}配置失败: {str(e)}",
            }

    async def update(self, item_id: int, data) -> dict[str, Any]:
        try:
            section_name = self._section_name(item_id)
            config = config_manager.get_config_parser()

            if not config.has_section(section_name):
                return {
                    "status": "error",
                    "message": f"{self.display_name}配置不存在: ID={item_id}",
                }

            for f in self.fields:
                value = getattr(data, f.name)
                if value is None:
                    continue
                if f.sensitive:
                    # 敏感字段：非空且非掩码才更新
                    if value.strip() and value != "******":
                        config.set(
                            section_name,
                            f.name,
                            encrypt_if_sensitive(section_name, f.name, value),
                        )
                else:
                    config.set(
                        section_name, f.name, str(value) if f.kind != "str" else value
                    )

            await asyncio.to_thread(config_manager._save_config, config)
            _reload_notification_channels()

            logger.info(f"更新{self.display_name}配置成功: ID={item_id}")

            section_config = config_manager.get_section(section_name)
            return {
                "status": "success",
                "message": f"{self.display_name}配置更新成功",
                "data": self._read_config(section_config),
            }
        except Exception as e:
            logger.error(f"更新{self.display_name}配置失败: {e}")
            return {
                "status": "error",
                "message": f"更新{self.display_name}配置失败: {str(e)}",
            }

    async def delete(self, item_id: int) -> dict[str, Any]:
        try:
            section_name = self._section_name(item_id)
            config = config_manager.get_config_parser()

            if not config.has_section(section_name):
                return {
                    "status": "error",
                    "message": f"{self.display_name}配置不存在: ID={item_id}",
                }

            # 删除配置段（保留其他段的原始 ID，避免破坏通知规则的渠道引用）
            config.remove_section(section_name)

            await asyncio.to_thread(config_manager._save_config, config)
            _reload_notification_channels()

            logger.info(f"删除{self.display_name}配置成功: ID={item_id}")

            return {
                "status": "success",
                "message": f"{self.display_name}配置删除成功",
            }
        except Exception as e:
            logger.error(f"删除{self.display_name}配置失败: {e}")
            return {
                "status": "error",
                "message": f"删除{self.display_name}配置失败: {str(e)}",
            }

    async def test(self, item_id: int) -> dict[str, Any]:
        if self.test_fn is None:
            return {
                "status": "error",
                "message": f"{self.display_name}不支持测试",
            }
        try:
            return await self.test_fn(item_id)
        except Exception as e:
            logger.error(f"测试{self.display_name}失败: {e}")
            return {
                "status": "error",
                "message": f"测试{self.display_name}失败: {str(e)}",
            }


# ========== test_fn 工厂：webhook/email 走 notifier，wecom/dingtalk 走 Channel.send ==========


async def _test_via_notifier(
    channel_type: str, id_param: str, item_id: int
) -> dict[str, Any]:
    """webhook/email 通过 notifier.test_notification 测试"""
    notifier = get_notifier()
    results = await asyncio.to_thread(
        notifier.test_notification,
        notification_type=channel_type,
        **{id_param: item_id},
    )
    return {"status": "success", "data": results}


def _make_channel_send_test_fn(
    channel_cls_name: str, section_prefix: str, display_name: str
):
    """wecom/dingtalk 通过直接构造 Channel.send 测试"""

    async def test_fn(item_id: int) -> dict[str, Any]:
        import time

        from ..utils.notifier import channels_impl

        channel_cls = getattr(channels_impl, channel_cls_name)
        section_name = f"{section_prefix}{item_id}"
        config = config_manager.get_config_parser()
        if not config.has_section(section_name):
            return {
                "status": "error",
                "message": f"{display_name}配置不存在: ID={item_id}",
            }

        cfg = config_manager.get_section(section_name)
        channel = channel_cls(channel_id=section_name, config=cfg)

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

    return test_fn


# ========== 5 个渠道的 handler 实例 ==========
# 字段顺序与 create_model 一致；sensitive 字段会自动加密/掩码

_WEBHOOK_FIELDS = [
    ChannelField("enabled", kind="bool", default=False),
    ChannelField("url"),
    ChannelField("method", default="POST"),
    ChannelField("headers"),
    ChannelField("template"),
    ChannelField("types", default="all"),
]

_EMAIL_FIELDS = [
    ChannelField("enabled", kind="bool", default=False),
    ChannelField("smtp_server"),
    ChannelField("smtp_port", kind="int", default=587),
    ChannelField("smtp_username"),
    ChannelField("smtp_password", sensitive=True),
    ChannelField("smtp_use_tls", kind="bool", default=True),
    ChannelField("email_from"),
    ChannelField("email_to"),
    ChannelField("email_subject"),
    ChannelField("template"),
    ChannelField("types", default="mark_failed"),
]

_WECOM_FIELDS = [
    ChannelField("enabled", kind="bool", default=False),
    ChannelField("key", sensitive=True),
    ChannelField("msg_type", default="text"),
    ChannelField("template"),
    ChannelField("types", default="all"),
]

_DINGTALK_FIELDS = [
    ChannelField("enabled", kind="bool", default=False),
    ChannelField("access_token", sensitive=True),
    ChannelField("secret", sensitive=True),
    ChannelField("msg_type", default="text"),
    ChannelField("template"),
    ChannelField("types", default="all"),
]

_RULE_FIELDS = [
    ChannelField("name"),
    ChannelField("enabled", kind="bool", default=False),
    ChannelField("types", default="all"),
    ChannelField("channels"),
    ChannelField("template"),
]

# handler 字典：key 同时用作 get_notification_status 的返回键
_CHANNEL_HANDLERS: dict[str, ChannelHandler] = {
    "webhook": ChannelHandler(
        section_prefix="notify-webhook-",
        display_name="webhook",
        fields=_WEBHOOK_FIELDS,
        create_model=WebhookConfigCreate,
        update_model=WebhookConfigUpdate,
        test_fn=lambda id: _test_via_notifier("webhook", "webhook_id", id),
        label="Webhook",
    ),
    "email": ChannelHandler(
        section_prefix="notify-email-",
        display_name="邮件",
        fields=_EMAIL_FIELDS,
        create_model=EmailConfigCreate,
        update_model=EmailConfigUpdate,
        test_fn=lambda id: _test_via_notifier("email", "email_id", id),
        label="邮件",
    ),
    "wecom": ChannelHandler(
        section_prefix="notify-wecom-",
        display_name="企业微信",
        fields=_WECOM_FIELDS,
        create_model=WeComConfigCreate,
        update_model=WeComConfigUpdate,
        test_fn=_make_channel_send_test_fn(
            "WeChatWorkChannel", "notify-wecom-", "企业微信"
        ),
        label="企业微信",
    ),
    "dingtalk": ChannelHandler(
        section_prefix="notify-dingtalk-",
        display_name="钉钉",
        fields=_DINGTALK_FIELDS,
        create_model=DingTalkConfigCreate,
        update_model=DingTalkConfigUpdate,
        test_fn=_make_channel_send_test_fn(
            "DingTalkChannel", "notify-dingtalk-", "钉钉"
        ),
        label="钉钉",
    ),
    "rule": ChannelHandler(
        section_prefix="notify-rule-",
        display_name="通知规则",
        fields=_RULE_FIELDS,
        create_model=NotificationRuleCreate,
        update_model=NotificationRuleUpdate,
        test_fn=None,  # 通知规则不支持测试
        label=None,
    ),
}


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
        config = config_manager.get_config_parser()
        sections = config.sections()
        stats: dict[str, Any] = {}
        for key, handler in _CHANNEL_HANDLERS.items():
            total = 0
            enabled = 0
            for section_name in sections:
                if section_name.startswith(handler.section_prefix):
                    total += 1
                    if config_manager.get_section(section_name).get("enabled", False):
                        enabled += 1
            stats[key] = {
                "total": total,
                "enabled": enabled,
                "configured": total > 0,
            }
        return {"status": "success", "data": stats}
    except Exception as e:
        logger.error(f"获取通知状态失败: {e}")
        return {"status": "error", "message": f"获取通知状态失败: {str(e)}"}


# ========== Webhook配置CRUD接口 ==========


@router.get("/notification/webhooks")
async def get_webhooks(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有webhook配置"""
    return await _CHANNEL_HANDLERS["webhook"].list_all()


@router.post("/notification/webhooks")
async def create_webhook(
    webhook_data: WebhookConfigCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的webhook配置"""
    return await _CHANNEL_HANDLERS["webhook"].create(webhook_data)


@router.put("/notification/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int,
    webhook_data: WebhookConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新webhook配置"""
    return await _CHANNEL_HANDLERS["webhook"].update(webhook_id, webhook_data)


@router.delete("/notification/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除webhook配置"""
    return await _CHANNEL_HANDLERS["webhook"].delete(webhook_id)


@router.post("/notification/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """测试指定的webhook配置"""
    return await _CHANNEL_HANDLERS["webhook"].test(webhook_id)


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
    return await _CHANNEL_HANDLERS["email"].list_all()


@router.post("/notification/emails")
async def create_email(
    email_data: EmailConfigCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的邮件配置"""
    return await _CHANNEL_HANDLERS["email"].create(email_data)


@router.put("/notification/emails/{email_id}")
async def update_email(
    email_id: int,
    email_data: EmailConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新邮件配置"""
    return await _CHANNEL_HANDLERS["email"].update(email_id, email_data)


@router.delete("/notification/emails/{email_id}")
async def delete_email(
    email_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除邮件配置"""
    return await _CHANNEL_HANDLERS["email"].delete(email_id)


@router.post("/notification/emails/{email_id}/test")
async def test_email(
    email_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """测试指定的邮件配置"""
    return await _CHANNEL_HANDLERS["email"].test(email_id)


# ========== 企业微信配置CRUD接口 ==========


@router.get("/notification/wecoms")
async def get_wecoms(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有企业微信配置"""
    return await _CHANNEL_HANDLERS["wecom"].list_all()


@router.post("/notification/wecoms")
async def create_wecom(
    wecom_data: WeComConfigCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的企业微信配置"""
    return await _CHANNEL_HANDLERS["wecom"].create(wecom_data)


@router.put("/notification/wecoms/{wecom_id}")
async def update_wecom(
    wecom_id: int,
    wecom_data: WeComConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新企业微信配置"""
    return await _CHANNEL_HANDLERS["wecom"].update(wecom_id, wecom_data)


@router.delete("/notification/wecoms/{wecom_id}")
async def delete_wecom(
    wecom_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除企业微信配置"""
    return await _CHANNEL_HANDLERS["wecom"].delete(wecom_id)


@router.post("/notification/wecoms/{wecom_id}/test")
async def test_wecom(
    wecom_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """测试指定的企业微信配置"""
    return await _CHANNEL_HANDLERS["wecom"].test(wecom_id)


# ========== 钉钉配置CRUD接口 ==========


@router.get("/notification/dingtalks")
async def get_dingtalks(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有钉钉配置"""
    return await _CHANNEL_HANDLERS["dingtalk"].list_all()


@router.post("/notification/dingtalks")
async def create_dingtalk(
    dingtalk_data: DingTalkConfigCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的钉钉配置"""
    return await _CHANNEL_HANDLERS["dingtalk"].create(dingtalk_data)


@router.put("/notification/dingtalks/{dingtalk_id}")
async def update_dingtalk(
    dingtalk_id: int,
    dingtalk_data: DingTalkConfigUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新钉钉配置"""
    return await _CHANNEL_HANDLERS["dingtalk"].update(dingtalk_id, dingtalk_data)


@router.delete("/notification/dingtalks/{dingtalk_id}")
async def delete_dingtalk(
    dingtalk_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除钉钉配置"""
    return await _CHANNEL_HANDLERS["dingtalk"].delete(dingtalk_id)


@router.post("/notification/dingtalks/{dingtalk_id}/test")
async def test_dingtalk(
    dingtalk_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """测试指定的钉钉配置"""
    return await _CHANNEL_HANDLERS["dingtalk"].test(dingtalk_id)


# ========== 通知规则CRUD接口 ==========


@router.get("/notification/rules")
async def get_notification_rules(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """获取所有通知规则"""
    return await _CHANNEL_HANDLERS["rule"].list_all()


@router.post("/notification/rules")
async def create_notification_rule(
    rule_data: NotificationRuleCreate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """创建新的通知规则"""
    return await _CHANNEL_HANDLERS["rule"].create(rule_data)


@router.put("/notification/rules/{rule_id}")
async def update_notification_rule(
    rule_id: int,
    rule_data: NotificationRuleUpdate,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """更新通知规则"""
    return await _CHANNEL_HANDLERS["rule"].update(rule_id, rule_data)


@router.delete("/notification/rules/{rule_id}")
async def delete_notification_rule(
    rule_id: int, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """删除通知规则"""
    return await _CHANNEL_HANDLERS["rule"].delete(rule_id)


@router.get("/notification/channels")
async def get_notification_channels(
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """获取所有通知渠道（用于规则编辑时选择）

    “rule” handler 不参与渠道选择，仅枚举 4 个真实通知渠道。
    """
    try:
        channels = []
        config = config_manager.get_config_parser()

        for key, handler in _CHANNEL_HANDLERS.items():
            if key == "rule":
                continue
            for section_name in config.sections():
                if section_name.startswith(handler.section_prefix):
                    cfg = config_manager.get_section(section_name)
                    channels.append(
                        {
                            "id": cfg.get("id"),
                            "type": key,
                            "label": f"{handler.label} #{cfg.get('id', '')}",
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
    from ..core.notification_registry import (
        CATEGORIES,
        WATCHING_SUMMARY_PREFIX,
        get_type_meta,
        ui_visible_types,
    )

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
    # 动态附加追番总结任务的 watching_summary_{name} 类型（每个任务一个可勾选项）
    summary_meta = get_type_meta(WATCHING_SUMMARY_PREFIX)
    for job in config_manager.get_summary_configs():
        name = job.get("name")
        if not name:
            continue
        types.append(
            {
                "id": f"{WATCHING_SUMMARY_PREFIX}_{name}",
                "display_name": f"{summary_meta.display_name} · {name}",
                "icon": summary_meta.icon,
                "color": summary_meta.color,
                "description": summary_meta.description,
                "is_item_level": summary_meta.is_item_level,
                "category": summary_meta.category,
            }
        )
    # 附加分类定义，前端动态读取，避免前后端重复维护
    categories = [{"id": k, "name": v} for k, v in CATEGORIES.items()]
    return {"status": "success", "data": types, "categories": categories}
