"""
通知 API：SMTP 密码加密写入的补充测试。
"""

from configparser import ConfigParser
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, notification


@pytest.fixture
def app_with_auth():
    app = FastAPI()
    app.include_router(notification.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_email_passes_smtp_password_through_encrypt(app_with_auth):
    """创建邮件配置时 smtp_password 应经 encrypt_if_sensitive 再写入 parser。"""
    parser = ConfigParser()

    email_payload = {
        "enabled": True,
        "smtp_server": "smtp.example.com",
        "smtp_port": 465,
        "smtp_username": "u@example.com",
        "smtp_password": "plain-smtp-secret",
        "smtp_use_tls": True,
        "email_from": "",
        "email_to": "to@example.com",
        "email_subject": "",
        "email_template_file": "",
        "types": "mark_failed",
    }

    with (
        patch("app.core.config.config_manager.get_config_parser", return_value=parser),
        patch("app.core.config.config_manager._save_config") as save_mock,
        patch(
            "app.api.notification.encrypt_if_sensitive",
            return_value="BGS1:wrapped-smtp",
        ) as enc_mock,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post("/api/notification/emails", json=email_payload)

    assert response.status_code == 200
    enc_mock.assert_called_once_with("email-1", "smtp_password", "plain-smtp-secret")
    assert parser.get("email-1", "smtp_password") == "BGS1:wrapped-smtp"
    save_mock.assert_called_once()
