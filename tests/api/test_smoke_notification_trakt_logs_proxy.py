"""通知 Webhook CRUD、Trakt 只读状态、清空日志、代理主机/网络诊断（日常高频）。"""

import configparser
import socket
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, logs, notification, proxy, trakt
from app.models.trakt import TraktConfig


@pytest.fixture
def app_notif():
    app = FastAPI()
    app.include_router(notification.router)

    async def mock_user():
        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def app_trakt():
    app = FastAPI()
    app.include_router(trakt.router)

    async def mock_user():
        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def app_logs_proxy():
    app = FastAPI()
    app.include_router(logs.router)
    app.include_router(proxy.router)

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
async def test_trakt_get_config_when_not_linked(app_trakt):
    with patch(
        "app.api.trakt.trakt_auth_service.get_user_trakt_config", return_value=None
    ):
        with patch(
            "app.api.trakt.config_manager.get_trakt_config",
            return_value={"client_id": "cid", "client_secret": "", "redirect_uri": ""},
        ):
            transport = ASGITransport(app=app_trakt)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.get("/api/trakt/config")
    assert r.status_code == 200
    body = r.json()
    assert body["is_connected"] is False
    assert body["client_id"] == "cid"


@pytest.mark.asyncio
async def test_trakt_sync_status_with_records(app_trakt):
    tcfg = TraktConfig(
        user_id="admin",
        access_token="tok",
        enabled=True,
        last_sync_time=100,
    )
    with patch(
        "app.api.trakt.trakt_auth_service.get_user_trakt_config", return_value=tcfg
    ):
        with patch(
            "app.api.trakt.trakt_scheduler.get_user_job_status",
            return_value=None,
        ):
            with patch(
                "app.api.trakt.database_manager.get_sync_records",
                return_value={
                    "records": [
                        {"status": "success"},
                        {"status": "error"},
                    ],
                    "total": 2,
                },
            ):
                transport = ASGITransport(app=app_trakt)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.get("/api/trakt/sync/status")
    assert r.status_code == 200
    body = r.json()
    assert body["success_count"] == 1
    assert body["error_count"] == 1
    assert body["total_count"] == 2


@pytest.mark.asyncio
async def test_logs_clear_truncates_file(app_logs_proxy, tmp_path):
    log_f = tmp_path / "run.log"
    log_f.write_text("line1\nline2\n", encoding="utf-8")

    with patch("app.api.logs.config_manager.get_config", return_value=str(log_f)):
        transport = ASGITransport(app=app_logs_proxy)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/logs/clear")
    assert r.status_code == 200
    assert log_f.read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_proxy_test_host(app_logs_proxy):
    with patch(
        "app.utils.docker_helper.docker_helper.test_host_connectivity",
        return_value={"reachable": True, "latency_ms": 1},
    ):
        transport = ASGITransport(app=app_logs_proxy)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/proxy/test-host",
                json={"host": "127.0.0.1", "port": 65534, "timeout": 1},
            )
    assert r.status_code == 200
    assert r.json()["data"]["reachable"] is True


@pytest.mark.asyncio
async def test_network_diagnose_dns_failure(app_logs_proxy):
    with patch(
        "app.api.proxy.socket.getaddrinfo",
        side_effect=socket.gaierror("Name or service not known"),
    ):
        transport = ASGITransport(app=app_logs_proxy)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/network/diagnose",
                json={"url": "https://this-host-should-not-exist-12345.invalid/"},
            )
    assert r.status_code == 200
    diagnosis = r.json()["data"]["diagnosis"]
    dns_step = next(x for x in diagnosis if x.get("test") == "DNS解析")
    assert dns_step["status"] == "failed"


@pytest.mark.asyncio
async def test_get_notification_status_webhook_and_email(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("webhook-1")
    cfg.set("webhook-1", "id", "1")
    cfg.set("webhook-1", "enabled", "true")
    cfg.set("webhook-1", "url", "https://w.example/hook")
    cfg.add_section("email-1")
    cfg.set("email-1", "id", "1")
    cfg.set("email-1", "enabled", "false")
    cfg.set("email-1", "smtp_server", "smtp.example")

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
        sec = f"webhook-{wid}"
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
                        "email_template_file": "",
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
        "email_template_file": "",
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
    cfg.add_section("email-1")
    cfg.set("email-1", "id", "1")
    cfg.set("email-1", "enabled", "true")
    cfg.set("email-1", "smtp_server", "smtp.x")
    cfg.set("email-1", "smtp_port", "587")
    cfg.set("email-1", "smtp_username", "u")
    cfg.set("email-1", "smtp_password", "")
    cfg.set("email-1", "smtp_use_tls", "true")
    cfg.set("email-1", "email_from", "")
    cfg.set("email-1", "email_to", "t@x")
    cfg.set("email-1", "email_subject", "")
    cfg.set("email-1", "email_template_file", "")
    cfg.set("email-1", "types", "mark_failed")

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
async def test_delete_webhook_reindexes_remaining(app_notif):
    cfg = configparser.ConfigParser()
    for wid, url in ((1, "http://first"), (2, "http://second")):
        sec = f"webhook-{wid}"
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
    assert "webhook-2" not in cfg.sections()
    assert cfg.has_section("webhook-1")
    assert cfg.get("webhook-1", "url") == "http://second"
    assert cfg.get("webhook-1", "id") == "1"


@pytest.mark.asyncio
async def test_network_diagnose_dns_non_gaierror(app_logs_proxy):
    with patch(
        "app.api.proxy.socket.getaddrinfo",
        side_effect=RuntimeError("resolver boom"),
    ):
        with patch(
            "app.utils.docker_helper.docker_helper.get_environment_info",
            return_value={"is_docker": False},
        ):
            with patch("app.core.config.config_manager.get", return_value=""):
                transport = ASGITransport(app=app_logs_proxy)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post(
                        "/api/network/diagnose",
                        json={"url": "https://example.com/"},
                    )
    assert r.status_code == 200
    dns_step = next(x for x in r.json()["data"]["diagnosis"] if x["test"] == "DNS解析")
    assert dns_step["status"] == "error"


@pytest.mark.asyncio
async def test_network_diagnose_tcp_and_http_success(app_logs_proxy):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0
    mock_resp = MagicMock()
    mock_resp.status_code = 204

    def fake_get(section, key, fallback=None):
        if key == "ssl_verify":
            return True
        if key == "script_proxy":
            return ""
        return fallback

    with patch(
        "app.api.proxy.socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
        ],
    ):
        with patch("app.api.proxy.socket.socket", return_value=mock_sock):
            with patch("requests.get", return_value=mock_resp):
                with patch(
                    "app.utils.docker_helper.docker_helper.get_environment_info",
                    return_value={"is_docker": False},
                ):
                    with patch(
                        "app.core.config.config_manager.get", side_effect=fake_get
                    ):
                        transport = ASGITransport(app=app_logs_proxy)
                        async with AsyncClient(
                            transport=transport, base_url="http://test"
                        ) as ac:
                            r = await ac.post(
                                "/api/network/diagnose",
                                json={"url": "https://127.0.0.1:443/"},
                            )

    assert r.status_code == 200
    diagnosis = r.json()["data"]["diagnosis"]
    tests = {d["test"]: d["status"] for d in diagnosis}
    assert tests.get("DNS解析") == "success"
    assert tests.get("TCP直连") == "success"
    assert tests.get("HTTP连接") == "success"
    mock_sock.close.assert_called_once()


@pytest.mark.asyncio
async def test_delete_email_reindexes_remaining(app_notif):
    cfg = configparser.ConfigParser()
    for eid, server in ((1, "smtp-first"), (2, "smtp-second")):
        sec = f"email-{eid}"
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
        cfg.set(sec, "email_template_file", "")
        cfg.set(sec, "types", "mark_failed")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch("app.core.config.config_manager._save_config"):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.delete("/api/notification/emails/1")

    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert "email-2" not in cfg.sections()
    assert cfg.has_section("email-1")
    assert cfg.get("email-1", "smtp_server") == "smtp-second"
    assert cfg.get("email-1", "id") == "1"


@pytest.mark.asyncio
async def test_update_notification_email_new_password(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("email-1")
    cfg.set("email-1", "id", "1")
    cfg.set("email-1", "enabled", "true")
    cfg.set("email-1", "smtp_server", "smtp.x")
    cfg.set("email-1", "smtp_port", "587")
    cfg.set("email-1", "smtp_username", "u")
    cfg.set("email-1", "smtp_password", "old-plain")
    cfg.set("email-1", "smtp_use_tls", "true")
    cfg.set("email-1", "email_from", "")
    cfg.set("email-1", "email_to", "t@x")
    cfg.set("email-1", "email_subject", "")
    cfg.set("email-1", "email_template_file", "")
    cfg.set("email-1", "types", "mark_failed")

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
    assert cfg.get("email-1", "smtp_password") != "old-plain"


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
    cfg.add_section("webhook-1")
    cfg.set("webhook-1", "id", "1")
    cfg.set("webhook-1", "enabled", "true")
    cfg.set("webhook-1", "url", "https://old")
    cfg.set("webhook-1", "method", "POST")
    cfg.set("webhook-1", "headers", "")
    cfg.set("webhook-1", "template", "")
    cfg.set("webhook-1", "types", "all")

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
    assert cfg.get("webhook-1", "method") == "GET"
    assert cfg.get("webhook-1", "types") == "sync"


@pytest.mark.asyncio
async def test_delete_webhook_save_failure(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("webhook-1")
    cfg.set("webhook-1", "id", "1")
    cfg.set("webhook-1", "enabled", "true")
    cfg.set("webhook-1", "url", "https://only")
    cfg.set("webhook-1", "method", "POST")
    cfg.set("webhook-1", "headers", "")
    cfg.set("webhook-1", "template", "")
    cfg.set("webhook-1", "types", "all")

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
    """删除非首条时，若剩余段名已是 webhook-1，只更新 id（走 else 分支）。"""
    cfg = configparser.ConfigParser()
    for wid, url in ((1, "http://a"), (2, "http://b")):
        sec = f"webhook-{wid}"
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
    assert list(cfg.sections()) == ["webhook-1"]
    assert cfg.get("webhook-1", "url") == "http://a"
    assert cfg.get("webhook-1", "id") == "1"


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
    cfg.add_section("email-1")
    cfg.set("email-1", "id", "1")
    cfg.set("email-1", "enabled", "true")
    cfg.set("email-1", "smtp_server", "smtp.x")

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
                        "email_template_file": "",
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
        "email_template_file": "tpl.txt",
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
                            "email_template_file": "other.tpl",
                            "types": "all",
                        },
                    )
    assert r.status_code == 200
    assert r.json()["status"] == "success"


@pytest.mark.asyncio
async def test_delete_email_save_failure(app_notif):
    cfg = configparser.ConfigParser()
    cfg.add_section("email-1")
    cfg.set("email-1", "id", "1")
    cfg.set("email-1", "enabled", "true")
    cfg.set("email-1", "smtp_server", "smtp.x")
    cfg.set("email-1", "smtp_port", "587")
    cfg.set("email-1", "smtp_username", "u")
    cfg.set("email-1", "smtp_password", "")
    cfg.set("email-1", "smtp_use_tls", "true")
    cfg.set("email-1", "email_from", "")
    cfg.set("email-1", "email_to", "t@x")
    cfg.set("email-1", "email_subject", "")
    cfg.set("email-1", "email_template_file", "")
    cfg.set("email-1", "types", "mark_failed")

    with patch("app.core.config.config_manager.get_config_parser", return_value=cfg):
        with patch(
            "app.core.config.config_manager._save_config",
            side_effect=OSError("persist error"),
        ):
            transport = ASGITransport(app=app_notif)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.delete("/api/notification/emails/1")
    assert r.json()["status"] == "error"


def _patch_network_diagnose_dns_ok_no_proxy():
    """单次调用返回一组 patch：DNS 成功、docker 环境、无 script_proxy。"""
    return (
        patch(
            "app.api.proxy.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
            ],
        ),
        patch(
            "app.utils.docker_helper.docker_helper.get_environment_info",
            return_value={"is_docker": False},
        ),
        patch("app.core.config.config_manager.get", return_value=""),
    )


@pytest.mark.asyncio
async def test_network_diagnose_tcp_connect_ex_failed(app_logs_proxy):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 61
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    p_gai, p_docker, p_cfg = _patch_network_diagnose_dns_ok_no_proxy()
    with p_gai, p_docker, p_cfg:
        with patch("app.api.proxy.socket.socket", return_value=mock_sock):
            with patch("requests.get", return_value=mock_resp):
                transport = ASGITransport(app=app_logs_proxy)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post(
                        "/api/network/diagnose",
                        json={"url": "https://127.0.0.1:443/"},
                    )

    steps = {d["test"]: d for d in r.json()["data"]["diagnosis"]}
    assert steps["TCP直连"]["status"] == "failed"
    assert steps["HTTP连接"]["status"] == "success"


@pytest.mark.asyncio
async def test_network_diagnose_tcp_socket_raises(app_logs_proxy):
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    p_gai, p_docker, p_cfg = _patch_network_diagnose_dns_ok_no_proxy()
    with p_gai, p_docker, p_cfg:
        with patch(
            "app.api.proxy.socket.socket",
            side_effect=OSError("socket factory"),
        ):
            with patch("requests.get", return_value=mock_resp):
                transport = ASGITransport(app=app_logs_proxy)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    r = await ac.post(
                        "/api/network/diagnose",
                        json={"url": "https://127.0.0.1:443/"},
                    )

    steps = {d["test"]: d for d in r.json()["data"]["diagnosis"]}
    assert steps["TCP直连"]["status"] == "error"
    assert "socket factory" in steps["TCP直连"]["message"]


@pytest.mark.asyncio
async def test_network_diagnose_http_request_exception(app_logs_proxy):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0

    def fake_get(section, key, fallback=None):
        if key == "ssl_verify":
            return True
        if key == "script_proxy":
            return ""
        return fallback

    p_gai, p_docker, _ = _patch_network_diagnose_dns_ok_no_proxy()
    with p_gai, p_docker:
        with patch("app.core.config.config_manager.get", side_effect=fake_get):
            with patch("app.api.proxy.socket.socket", return_value=mock_sock):
                with patch(
                    "requests.get",
                    side_effect=requests.exceptions.ConnectionError("refused"),
                ):
                    transport = ASGITransport(app=app_logs_proxy)
                    async with AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as ac:
                        r = await ac.post(
                            "/api/network/diagnose",
                            json={"url": "https://127.0.0.1:443/"},
                        )

    steps = {d["test"]: d for d in r.json()["data"]["diagnosis"]}
    assert steps["HTTP连接"]["status"] == "failed"
    assert "refused" in steps["HTTP连接"]["message"]


@pytest.mark.asyncio
async def test_network_diagnose_http_non_request_exception(app_logs_proxy):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0

    def fake_get(section, key, fallback=None):
        if key == "ssl_verify":
            return True
        if key == "script_proxy":
            return ""
        return fallback

    p_gai, p_docker, _ = _patch_network_diagnose_dns_ok_no_proxy()
    with p_gai, p_docker:
        with patch("app.core.config.config_manager.get", side_effect=fake_get):
            with patch("app.api.proxy.socket.socket", return_value=mock_sock):
                with patch("requests.get", side_effect=ValueError("unexpected")):
                    transport = ASGITransport(app=app_logs_proxy)
                    async with AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as ac:
                        r = await ac.post(
                            "/api/network/diagnose",
                            json={"url": "https://127.0.0.1:443/"},
                        )

    steps = {d["test"]: d for d in r.json()["data"]["diagnosis"]}
    assert steps["HTTP连接"]["status"] == "error"
    assert "unexpected" in steps["HTTP连接"]["message"]
