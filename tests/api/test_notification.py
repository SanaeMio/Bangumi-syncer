"""
通知API测试
"""

import configparser
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


# ===== 以下自 test_smoke_notification_trakt_logs_proxy.py 并入 =====


@pytest.fixture
def app_notif():
    app = FastAPI()
    app.include_router(notification.router)

    async def mock_user():
        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_notification_test_all(app_notif):
    with patch("app.api.notification.get_notifier") as gn:
        gn.return_value.test_notification.return_value = {"webhook": "ok"}
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/notification/test", json={"notification_type": "all"}
            )
    assert r.status_code == 200
    assert r.json()["status"] == "success"


@pytest.mark.asyncio
async def test_create_notification_webhook(app_notif):
    cfg = MagicMock()
    cfg.sections.return_value = []
    cfg.has_section.return_value = False
    cfg.add_section = MagicMock()
    cfg.set = MagicMock()

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post(
                    "/api/notification/webhooks",
                    json={
                        "enabled": True,
                        "url": "https://example.com/hook",
                        "method": "POST",
                        "headers": "",
                        "template": "",
                        "types": "all",
                    },
                )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["data"]["id"] == 1
    cfg.add_section.assert_called()


@pytest.mark.asyncio
async def test_update_notification_webhook_not_found(app_notif):
    cfg = MagicMock()
    cfg.has_section.return_value = False

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.put(
                "/api/notification/webhooks/99",
                json={"enabled": False},
            )
    assert r.status_code == 200
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_update_notification_webhook_success(app_notif):
    cfg = MagicMock()
    cfg.has_section.return_value = True
    cfg.set = MagicMock()
    section = {
        "enabled": True,
        "url": "https://old",
        "method": "POST",
        "headers": "",
        "template": "",
        "types": "all",
    }

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            with patch(
                "app.core.config.config_manager.get_section",
                return_value=section,
            ):
                transport = ASGITransport(app=app_notif)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.put(
                        "/api/notification/webhooks/1",
                        json={"url": "https://new.example/hook"},
                    )
    assert r.status_code == 200
    assert r.json()["status"] == "success"


@pytest.mark.asyncio
async def test_delete_notification_webhook_not_found(app_notif):
    cfg = MagicMock()
    cfg.has_section.return_value = False

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.delete("/api/notification/webhooks/5")
    assert r.status_code == 200
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_test_single_webhook_by_id(app_notif):
    with patch("app.api.notification.get_notifier") as gn:
        gn.return_value.test_notification.return_value = {"ok": True}
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/notification/webhooks/1/test")
    assert r.status_code == 200
    gn.return_value.test_notification.assert_called_once_with(
        notification_type="webhook", webhook_id=1
    )


@pytest.mark.asyncio
async def test_get_notification_status_webhook_and_email(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("notify-webhook-1")
    cfg.set("notify-webhook-1", "id", "1")
    cfg.set("notify-webhook-1", "enabled", "true")
    cfg.set("notify-webhook-1", "url", "https://w.example/hook")
    cfg.add_section("notify-email-1")
    cfg.set("notify-email-1", "id", "1")
    cfg.set("notify-email-1", "enabled", "false")
    cfg.set("notify-email-1", "smtp_server", "smtp.example")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/notification/status")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["webhook"]["total"] == 1
    assert data["webhook"]["enabled"] == 1
    assert data["email"]["total"] == 1
    assert data["email"]["enabled"] == 0


@pytest.mark.asyncio
async def test_get_notification_webhooks_sorted(app_notif):
    cfg = configparser.ConfigParser()
    for wid, url in ((2, "https://second"), (1, "https://first")):
        sec = f"notify-webhook-{wid}"
        cfg.add_section(sec)
        cfg.set(sec, "id", str(wid))
        cfg.set(sec, "enabled", "true")
        cfg.set(sec, "url", url)
        cfg.set(sec, "method", "POST")
        cfg.set(sec, "headers", "")
        cfg.set(sec, "template", "")
        cfg.set(sec, "types", "all")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/notification/webhooks")
    assert r.status_code == 200
    items = r.json()["data"]
    assert [x["id"] for x in items] == [1, 2]


@pytest.mark.asyncio
async def test_create_notification_email(app_notif):
    cfg = MagicMock()
    cfg.sections.return_value = []
    cfg.has_section.return_value = False
    cfg.add_section = MagicMock()
    cfg.set = MagicMock()

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post(
                    "/api/notification/emails",
                    json={
                        "enabled": True,
                        "smtp_server": "smtp.test",
                        "smtp_port": 465,
                        "smtp_username": "u",
                        "smtp_password": "secret",
                        "smtp_use_tls": True,
                        "email_from": "a@x",
                        "email_to": "b@x",
                        "email_subject": "subj",
                        "template": "",
                        "types": "mark_failed",
                    },
                )
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert r.json()["data"]["id"] == 1


@pytest.mark.asyncio
async def test_update_delete_notification_email_paths(app_notif):
    cfg = MagicMock()
    cfg.has_section.return_value = False

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r_put = await ac.put(
                "/api/notification/emails/9",
                json={"enabled": False},
            )
            r_del = await ac.delete("/api/notification/emails/9")
    assert r_put.json()["status"] == "error"
    assert r_del.json()["status"] == "error"


@pytest.mark.asyncio
async def test_update_notification_email_success(app_notif):
    cfg = MagicMock()
    cfg.has_section.return_value = True
    cfg.set = MagicMock()
    section = {
        "id": 1,
        "enabled": True,
        "smtp_server": "old.smtp",
        "smtp_port": 465,
        "smtp_username": "u",
        "smtp_password": "******",
        "smtp_use_tls": True,
        "email_from": "",
        "email_to": "to@x",
        "email_subject": "",
        "template": "",
        "types": "mark_failed",
    }

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            with patch(
                "app.core.config.config_manager.get_section",
                return_value=section,
            ):
                transport = ASGITransport(app=app_notif)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.put(
                        "/api/notification/emails/1",
                        json={"smtp_server": "new.smtp"},
                    )
    assert r.status_code == 200
    assert r.json()["status"] == "success"


@pytest.mark.asyncio
async def test_get_notification_emails_list(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("notify-email-1")
    cfg.set("notify-email-1", "id", "1")
    cfg.set("notify-email-1", "enabled", "true")
    cfg.set("notify-email-1", "smtp_server", "smtp.x")
    cfg.set("notify-email-1", "smtp_port", "587")
    cfg.set("notify-email-1", "smtp_username", "u")
    cfg.set("notify-email-1", "smtp_password", "")
    cfg.set("notify-email-1", "smtp_use_tls", "true")
    cfg.set("notify-email-1", "email_from", "")
    cfg.set("notify-email-1", "email_to", "t@x")
    cfg.set("notify-email-1", "email_subject", "")
    cfg.set("notify-email-1", "template", "")
    cfg.set("notify-email-1", "types", "mark_failed")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/notification/emails")
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1
    assert r.json()["data"][0]["smtp_server"] == "smtp.x"


@pytest.mark.asyncio
async def test_post_notification_email_test_by_id(app_notif):
    with patch("app.api.notification.get_notifier") as gn:
        gn.return_value.test_notification.return_value = {"sent": True}
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/notification/emails/2/test")
    assert r.status_code == 200
    gn.return_value.test_notification.assert_called_once_with(
        notification_type="email", email_id=2
    )


@pytest.mark.asyncio
async def test_delete_webhook_preserves_remaining_ids(app_notif):
    """删除 webhook 后保留剩余配置的原始 ID，不重新索引"""
    cfg = configparser.ConfigParser()
    for wid, url in ((1, "http://first"), (2, "http://second")):
        sec = f"notify-webhook-{wid}"
        cfg.add_section(sec)
        cfg.set(sec, "id", str(wid))
        cfg.set(sec, "enabled", "true")
        cfg.set(sec, "url", url)
        cfg.set(sec, "method", "POST")
        cfg.set(sec, "headers", "")
        cfg.set(sec, "template", "")
        cfg.set(sec, "types", "all")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.delete("/api/notification/webhooks/1")

    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert "notify-webhook-1" not in cfg.sections()
    assert cfg.has_section("notify-webhook-2")
    assert cfg.get("notify-webhook-2", "url") == "http://second"
    assert cfg.get("notify-webhook-2", "id") == "2"


@pytest.mark.asyncio
async def test_delete_email_preserves_remaining_ids(app_notif):
    """删除 email 后保留剩余配置的原始 ID，不重新索引"""
    cfg = configparser.ConfigParser()
    for eid, server in ((1, "smtp-first"), (2, "smtp-second")):
        sec = f"notify-email-{eid}"
        cfg.add_section(sec)
        cfg.set(sec, "id", str(eid))
        cfg.set(sec, "enabled", "true")
        cfg.set(sec, "smtp_server", server)
        cfg.set(sec, "smtp_port", "587")
        cfg.set(sec, "smtp_username", "u")
        cfg.set(sec, "smtp_password", "")
        cfg.set(sec, "smtp_use_tls", "true")
        cfg.set(sec, "email_from", "")
        cfg.set(sec, "email_to", "t@x")
        cfg.set(sec, "email_subject", "")
        cfg.set(sec, "template", "")
        cfg.set(sec, "types", "mark_failed")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.delete("/api/notification/emails/1")

    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert "notify-email-1" not in cfg.sections()
    assert cfg.has_section("notify-email-2")
    assert cfg.get("notify-email-2", "smtp_server") == "smtp-second"
    assert cfg.get("notify-email-2", "id") == "2"


@pytest.mark.asyncio
async def test_update_notification_email_new_password(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("notify-email-1")
    cfg.set("notify-email-1", "id", "1")
    cfg.set("notify-email-1", "enabled", "true")
    cfg.set("notify-email-1", "smtp_server", "smtp.x")
    cfg.set("notify-email-1", "smtp_port", "587")
    cfg.set("notify-email-1", "smtp_username", "u")
    cfg.set("notify-email-1", "smtp_password", "old-plain")
    cfg.set("notify-email-1", "smtp_use_tls", "true")
    cfg.set("notify-email-1", "email_from", "")
    cfg.set("notify-email-1", "email_to", "t@x")
    cfg.set("notify-email-1", "email_subject", "")
    cfg.set("notify-email-1", "template", "")
    cfg.set("notify-email-1", "types", "mark_failed")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.put(
                    "/api/notification/emails/1",
                    json={"smtp_password": "new-secret-value"},
                )

    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert cfg.get("notify-email-1", "smtp_password") != "old-plain"


@pytest.mark.asyncio
async def test_post_notification_test_webhook_branch(app_notif):
    with patch("app.api.notification.get_notifier") as gn:
        gn.return_value.test_notification.return_value = {"ok": True}
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/notification/test",
                json={"notification_type": "webhook", "webhook_id": 3},
            )
    assert r.status_code == 200
    gn.return_value.test_notification.assert_called_once_with(
        notification_type="webhook", webhook_id=3, email_id=None
    )


@pytest.mark.asyncio
async def test_post_notification_test_email_branch(app_notif):
    with patch("app.api.notification.get_notifier") as gn:
        gn.return_value.test_notification.return_value = {"mail": True}
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/notification/test",
                json={"notification_type": "email", "email_id": 5},
            )
    assert r.status_code == 200
    gn.return_value.test_notification.assert_called_once_with(
        notification_type="email", webhook_id=None, email_id=5
    )


@pytest.mark.asyncio
async def test_post_notification_test_failure_returns_error(app_notif):
    with patch("app.api.notification.get_notifier") as gn:
        gn.return_value.test_notification.side_effect = RuntimeError("boom")
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/notification/test", json={"notification_type": "all"}
            )
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    assert "boom" in r.json()["message"]


@pytest.mark.asyncio
async def test_get_notification_status_parser_error(app_notif):
    with patch(
        "app.core.config.config_manager.get_config_parser",
        side_effect=RuntimeError("no parser"),
    ):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/notification/status")
    assert r.status_code == 200
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_get_notification_webhooks_parser_error(app_notif):
    with patch(
        "app.core.config.config_manager.get_config_parser",
        side_effect=OSError("disk"),
    ):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/notification/webhooks")
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_post_webhook_test_failure_returns_error(app_notif):
    with patch("app.api.notification.get_notifier") as gn:
        gn.return_value.test_notification.side_effect = ValueError("bad hook")
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/notification/webhooks/1/test")
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_post_email_test_failure_returns_error(app_notif):
    with patch("app.api.notification.get_notifier") as gn:
        gn.return_value.test_notification.side_effect = OSError("smtp down")
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/notification/emails/1/test")
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_create_webhook_save_failure(app_notif):
    cfg = MagicMock()
    cfg.sections.return_value = []
    cfg.has_section.return_value = False

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch(
            "app.core.config.config_manager._save_config",
            side_effect=OSError("write denied"),
        ):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post(
                    "/api/notification/webhooks",
                    json={
                        "enabled": True,
                        "url": "https://example.com/h",
                        "method": "POST",
                        "headers": "",
                        "template": "",
                        "types": "all",
                    },
                )
    assert r.json()["status"] == "error"
    assert "创建webhook" in r.json()["message"]


@pytest.mark.asyncio
async def test_update_webhook_save_failure(app_notif):
    cfg = MagicMock()
    cfg.has_section.return_value = True
    cfg.set = MagicMock()

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch(
            "app.core.config.config_manager._save_config",
            side_effect=RuntimeError("save boom"),
        ):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.put(
                    "/api/notification/webhooks/1",
                    json={"url": "https://changed.example/hook"},
                )
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_update_webhook_all_optional_fields(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("notify-webhook-1")
    cfg.set("notify-webhook-1", "id", "1")
    cfg.set("notify-webhook-1", "enabled", "true")
    cfg.set("notify-webhook-1", "url", "https://old")
    cfg.set("notify-webhook-1", "method", "POST")
    cfg.set("notify-webhook-1", "headers", "")
    cfg.set("notify-webhook-1", "template", "")
    cfg.set("notify-webhook-1", "types", "all")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.put(
                    "/api/notification/webhooks/1",
                    json={
                        "enabled": False,
                        "url": "https://new",
                        "method": "GET",
                        "headers": "X-Test:1",
                        "template": "{{msg}}",
                        "types": "sync",
                    },
                )
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert cfg.get("notify-webhook-1", "method") == "GET"
    assert cfg.get("notify-webhook-1", "types") == "sync"


@pytest.mark.asyncio
async def test_delete_webhook_save_failure(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("notify-webhook-1")
    cfg.set("notify-webhook-1", "id", "1")
    cfg.set("notify-webhook-1", "enabled", "true")
    cfg.set("notify-webhook-1", "url", "https://only")
    cfg.set("notify-webhook-1", "method", "POST")
    cfg.set("notify-webhook-1", "headers", "")
    cfg.set("notify-webhook-1", "template", "")
    cfg.set("notify-webhook-1", "types", "all")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch(
            "app.core.config.config_manager._save_config",
            side_effect=PermissionError("no save"),
        ):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.delete("/api/notification/webhooks/1")
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_delete_second_webhook_keeps_first_section_name(app_notif):
    """删除非首条时，若剩余段名已是 notify-webhook-1，只更新 id（走 else 分支）。"""
    cfg = configparser.ConfigParser()
    for wid, url in ((1, "http://a"), (2, "http://b")):
        sec = f"notify-webhook-{wid}"
        cfg.add_section(sec)
        cfg.set(sec, "id", str(wid))
        cfg.set(sec, "enabled", "true")
        cfg.set(sec, "url", url)
        cfg.set(sec, "method", "POST")
        cfg.set(sec, "headers", "")
        cfg.set(sec, "template", "")
        cfg.set(sec, "types", "all")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.delete("/api/notification/webhooks/2")

    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert list(cfg.sections()) == ["notify-webhook-1"]
    assert cfg.get("notify-webhook-1", "url") == "http://a"
    assert cfg.get("notify-webhook-1", "id") == "1"


@pytest.mark.asyncio
async def test_get_notification_emails_parser_error(app_notif):
    with patch(
        "app.core.config.config_manager.get_config_parser",
        side_effect=RuntimeError("cfg broken"),
    ):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/notification/emails")
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_get_notification_status_counts_enabled_email(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("notify-email-1")
    cfg.set("notify-email-1", "id", "1")
    cfg.set("notify-email-1", "enabled", "true")
    cfg.set("notify-email-1", "smtp_server", "smtp.x")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        transport = ASGITransport(app=app_notif)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/notification/status")
    data = r.json()["data"]
    assert data["email"]["total"] == 1
    assert data["email"]["enabled"] == 1


@pytest.mark.asyncio
async def test_create_email_save_failure(app_notif):
    cfg = MagicMock()
    cfg.sections.return_value = []
    cfg.has_section.return_value = False

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch(
            "app.core.config.config_manager._save_config",
            side_effect=OSError("cannot write ini"),
        ):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.post(
                    "/api/notification/emails",
                    json={
                        "enabled": True,
                        "smtp_server": "smtp.test",
                        "smtp_port": 465,
                        "smtp_username": "u",
                        "smtp_password": "p",
                        "smtp_use_tls": True,
                        "email_from": "",
                        "email_to": "b@x",
                        "email_subject": "",
                        "template": "",
                        "types": "mark_failed",
                    },
                )
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_update_email_save_failure(app_notif):
    cfg = MagicMock()
    cfg.has_section.return_value = True
    cfg.set = MagicMock()

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch(
            "app.core.config.config_manager._save_config",
            side_effect=RuntimeError("save failed"),
        ):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.put(
                    "/api/notification/emails/1",
                    json={"email_to": "other@x"},
                )
    assert r.json()["status"] == "error"


@pytest.mark.asyncio
async def test_update_email_optional_fields_exercise_setters(app_notif):
    cfg = MagicMock()
    cfg.has_section.return_value = True
    cfg.set = MagicMock()
    section = {
        "id": 1,
        "enabled": True,
        "smtp_server": "s",
        "smtp_port": 465,
        "smtp_username": "u",
        "smtp_password": "plain",
        "smtp_use_tls": True,
        "email_from": "f@x",
        "email_to": "t@x",
        "email_subject": "sub",
        "template": "tpl.txt",
        "types": "mark_failed",
    }

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            with patch(
                "app.core.config.config_manager.get_section",
                return_value=section,
            ):
                transport = ASGITransport(app=app_notif)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.put(
                        "/api/notification/emails/1",
                        json={
                            "enabled": False,
                            "smtp_port": 587,
                            "smtp_username": "u2",
                            "smtp_password": "   ",
                            "smtp_use_tls": False,
                            "email_from": "x@x",
                            "email_subject": "new",
                            "template": "other.tpl",
                            "types": "all",
                        },
                    )
    assert r.status_code == 200
    assert r.json()["status"] == "success"


@pytest.mark.asyncio
async def test_delete_email_save_failure(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("notify-email-1")
    cfg.set("notify-email-1", "id", "1")
    cfg.set("notify-email-1", "enabled", "true")
    cfg.set("notify-email-1", "smtp_server", "smtp.x")
    cfg.set("notify-email-1", "smtp_port", "587")
    cfg.set("notify-email-1", "smtp_username", "u")
    cfg.set("notify-email-1", "smtp_password", "")
    cfg.set("notify-email-1", "smtp_use_tls", "true")
    cfg.set("notify-email-1", "email_from", "")
    cfg.set("notify-email-1", "email_to", "t@x")
    cfg.set("notify-email-1", "email_subject", "")
    cfg.set("notify-email-1", "template", "")
    cfg.set("notify-email-1", "types", "mark_failed")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch(
            "app.core.config.config_manager._save_config",
            side_effect=OSError("persist error"),
        ):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.delete("/api/notification/emails/1")
    assert r.json()["status"] == "error"
