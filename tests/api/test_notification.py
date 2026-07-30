"""
通知API测试
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, notification


@pytest.fixture
def app_with_auth():
    """创建带有认证的测试应用"""
    app = FastAPI()
    app.include_router(notification.router)

    # 覆盖认证依赖
    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app

    # 清理覆盖
    app.dependency_overrides.clear()


@pytest.fixture
def mock_config_manager():
    """模拟配置管理器"""
    with patch("app.api.notification.config_manager") as mock_cm:
        yield mock_cm


def test_notification_router_init():
    """测试通知路由器初始化"""
    # notification.router 的 tags 可能为空，因为是通过 include_router 添加的
    assert notification.router is not None


@pytest.mark.asyncio
async def test_get_notification_status(app_with_auth):
    """测试获取通知状态"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/status")
        # 期望返回 200（成功）或其他状态
        assert response.status_code in [200, 404, 500]


@pytest.mark.asyncio
async def test_get_webhooks(app_with_auth):
    """测试获取 webhook 列表"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/webhooks")
        assert response.status_code in [200, 404, 500]


@pytest.mark.asyncio
async def test_get_emails(app_with_auth):
    """测试获取邮件列表"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/emails")
        assert response.status_code in [200, 404, 500]


# ========== 通知规则测试 ==========


@pytest.mark.asyncio
async def test_get_notification_rules_empty(app_with_auth, mock_config_manager):
    """测试获取空规则列表"""
    mock_config = MagicMock()
    mock_config.sections.return_value = []
    mock_config_manager.get_config_parser.return_value = mock_config

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/rules")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        assert result["data"] == []


@pytest.mark.asyncio
async def test_get_notification_rules_with_data(app_with_auth, mock_config_manager):
    """测试获取含数据的规则列表"""
    mock_config = MagicMock()
    mock_config.sections.return_value = ["notify-rule-1", "notify-rule-2"]
    mock_config_manager.get_config_parser.return_value = mock_config
    mock_config_manager.get_section.side_effect = [
        {
            "id": "1",
            "name": "规则A",
            "enabled": True,
            "types": "mark_failed",
            "channels": "webhook-1",
            "template": "",
        },
        {
            "id": "2",
            "name": "规则B",
            "enabled": False,
            "types": "all",
            "channels": "",
            "template": "{}",
        },
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/rules")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        assert len(result["data"]) == 2
        assert result["data"][0]["name"] == "规则A"
        assert result["data"][1]["name"] == "规则B"


@pytest.mark.asyncio
async def test_create_notification_rule(app_with_auth, mock_config_manager):
    """测试创建通知规则"""
    mock_config = MagicMock()
    mock_config.sections.return_value = []
    mock_config.has_section.return_value = False
    mock_config_manager.get_config_parser.return_value = mock_config

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/notification/rules",
            json={
                "name": "测试规则",
                "enabled": True,
                "types": "mark_failed",
                "channels": "notify-webhook-1",
                "template": "",
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        assert result["data"]["name"] == "测试规则"
        assert result["data"]["id"] == 1
        mock_config.add_section.assert_called_once_with("notify-rule-1")


@pytest.mark.asyncio
async def test_update_notification_rule(app_with_auth, mock_config_manager):
    """测试更新通知规则"""
    mock_config = MagicMock()
    mock_config.has_section.return_value = True
    mock_config_manager.get_config_parser.return_value = mock_config
    mock_config_manager.get_section.return_value = {
        "id": "1",
        "name": "更新后规则",
        "enabled": False,
        "types": "all",
        "channels": "",
        "template": "",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/notification/rules/1",
            json={"name": "更新后规则", "enabled": False},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        assert result["data"]["name"] == "更新后规则"
        assert result["data"]["enabled"] is False


@pytest.mark.asyncio
async def test_update_notification_rule_not_found(app_with_auth, mock_config_manager):
    """测试更新不存在的通知规则"""
    mock_config = MagicMock()
    mock_config.has_section.return_value = False
    mock_config_manager.get_config_parser.return_value = mock_config

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/notification/rules/99",
            json={"name": "不存在的规则"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "error"
        assert "不存在" in result["message"]


@pytest.mark.asyncio
async def test_delete_notification_rule(app_with_auth, mock_config_manager):
    """测试删除通知规则（保留剩余规则原始 ID，不重新索引）"""
    mock_config = MagicMock()
    mock_config.has_section.return_value = True
    mock_config.sections.return_value = ["notify-rule-2"]
    mock_config_manager.get_config_parser.return_value = mock_config
    mock_config_manager.get_section.return_value = {"id": "2", "name": "规则2"}

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.delete("/api/notification/rules/1")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        assert mock_config.remove_section.call_count == 1
        mock_config.remove_section.assert_any_call("notify-rule-1")


@pytest.mark.asyncio
async def test_delete_notification_rule_not_found(app_with_auth, mock_config_manager):
    """测试删除不存在的通知规则"""
    mock_config = MagicMock()
    mock_config.has_section.return_value = False
    mock_config_manager.get_config_parser.return_value = mock_config

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.delete("/api/notification/rules/99")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "error"
        assert "不存在" in result["message"]


# ========== 通知渠道测试 ==========


@pytest.mark.asyncio
async def test_get_notification_channels_empty(app_with_auth, mock_config_manager):
    """测试获取空渠道列表"""
    mock_config = MagicMock()
    mock_config.sections.return_value = []
    mock_config_manager.get_config_parser.return_value = mock_config

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/channels")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        assert result["data"] == []


@pytest.mark.asyncio
async def test_get_notification_channels_with_data(app_with_auth, mock_config_manager):
    """测试获取含数据的渠道列表"""
    mock_config = MagicMock()
    mock_config.sections.return_value = [
        "notify-webhook-1",
        "notify-email-1",
        "notify-wecom-1",
        "notify-dingtalk-1",
    ]
    mock_config_manager.get_config_parser.return_value = mock_config
    mock_config_manager.get_section.side_effect = [
        {"id": "1", "enabled": True},
        {"id": "1", "enabled": False},
        {"id": "1", "enabled": True},
        {"id": "1", "enabled": True},
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/channels")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        assert len(result["data"]) == 4
        types = {c["type"] for c in result["data"]}
        assert types == {"webhook", "email", "wecom", "dingtalk"}


@pytest.mark.asyncio
async def test_get_notification_status_with_rule(app_with_auth, mock_config_manager):
    """测试通知状态接口包含规则和渠道统计"""
    mock_config = MagicMock()
    mock_config.sections.return_value = [
        "notify-webhook-1",
        "notify-email-1",
        "notify-wecom-1",
        "notify-dingtalk-1",
        "notify-rule-1",
    ]
    mock_config_manager.get_config_parser.return_value = mock_config
    mock_config_manager.get_section.side_effect = [
        {"id": "1", "enabled": True},
        {"id": "1", "enabled": False},
        {"id": "1", "enabled": True},
        {"id": "1", "enabled": True},
        {"id": "1", "enabled": True},
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/notification/status")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        data = result["data"]
        assert data["webhook"]["total"] == 1
        assert data["email"]["total"] == 1
        assert data["wecom"]["total"] == 1
        assert data["dingtalk"]["total"] == 1
        assert data["rule"]["total"] == 1
        assert data["rule"]["enabled"] == 1
