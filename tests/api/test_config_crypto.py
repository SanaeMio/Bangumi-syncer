"""
配置 API 与敏感字段加解密相关的补充测试。
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import config, deps


@pytest.fixture
def app_with_auth():
    app = FastAPI()
    app.include_router(config.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def mock_config_manager():
    with patch("app.api.config.config_manager") as mock_cm:
        mock_cm.get_all_config.return_value = {
            "auth": {"username": "testuser", "password": ""},
            "bangumi": {"username": "bgmuser", "access_token": "BGS1:fakecipher"},
        }
        mock_cm.get_config_parser.return_value = MagicMock()
        mock_cm.active_config_path = "/tmp/test_config.ini"
        mock_cm.save_config.return_value = None
        mock_cm.reload_config.return_value = None
        yield mock_cm


@pytest.fixture
def mock_security_manager():
    with patch("app.api.config.security_manager") as mock_sm:
        mock_sm.get_auth_config.return_value = {"secret_key": "test_secret_key"}
        mock_sm.hash_password.return_value = "a" * 64
        mock_sm._init_auth_config.return_value = None
        yield mock_sm


@pytest.mark.asyncio
async def test_get_config_calls_decrypt_api_config_payload(
    app_with_auth, mock_config_manager, mock_security_manager
):
    """GET /api/config 应对返回体执行 decrypt_api_config_payload。"""
    with patch("app.api.config.decrypt_api_config_payload") as mock_decrypt:
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/config")

        assert response.status_code == 200
        mock_decrypt.assert_called_once()
        passed = mock_decrypt.call_args[0][0]
        assert "bangumi" in passed
        assert passed["bangumi"]["access_token"] == "BGS1:fakecipher"


@pytest.mark.asyncio
async def test_update_config_skips_empty_bangumi_access_token(
    app_with_auth, mock_config_manager, mock_security_manager
):
    """敏感字段为空字符串时不应调用 set_config 覆盖磁盘上的 token。"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/config",
            json={
                "bangumi": {
                    "username": "user1",
                    "access_token": "",
                    "private": False,
                }
            },
        )

    assert response.status_code == 200
    for call in mock_config_manager.set_config.call_args_list:
        args = call[0]
        if len(args) >= 2 and args[0] == "bangumi" and args[1] == "access_token":
            pytest.fail("不应在 access_token 为空时写入")


@pytest.mark.asyncio
async def test_update_config_writes_non_empty_bangumi_access_token(
    app_with_auth, mock_config_manager, mock_security_manager
):
    """非空 access_token 应触发 set_config。"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/config",
            json={
                "bangumi": {
                    "username": "user1",
                    "access_token": "new-token-value",
                    "private": True,
                }
            },
        )

    assert response.status_code == 200
    found = False
    for call in mock_config_manager.set_config.call_args_list:
        args = call[0]
        if len(args) >= 2 and args[0] == "bangumi" and args[1] == "access_token":
            assert args[2] == "new-token-value"
            found = True
    assert found
