"""Bangumi 账号管理 API 测试。

覆盖 AccountInfo 响应模型的新字段（bangumi_user_id、expires_at），
确保 OAuth 账号的 OAuth 相关信息正确暴露给前端账号列表。
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import bangumi_accounts, deps


@pytest.fixture
def app_with_auth():
    app = FastAPI()
    app.include_router(bangumi_accounts.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "admin", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_accounts_includes_oauth_fields(app_with_auth):
    """账号列表应包含 bangumi_user_id 和 expires_at 字段"""
    oauth_account = {
        "section_name": "bangumi-alice",
        "username": "alice",
        "media_server_usernames": ["plex_alice"],
        "auth_method": "oauth",
        "nickname": "Alice",
        "avatar": "https://bgm.tv/img/avatar/alice.png",
        "bangumi_user_id": "12345",
        "expires_at": 9999999999,
        "private": False,
        "is_active": True,
        "access_token": "AT",
    }
    manual_account = {
        "section_name": "bangumi-bob",
        "username": "bob",
        "media_server_usernames": ["plex_bob"],
        "auth_method": "manual",
        "nickname": "",
        "avatar": "",
        "bangumi_user_id": "",
        "expires_at": None,
        "private": True,
        "is_active": False,
        "access_token": "manual_token",
    }
    with (
        patch("app.api.bangumi_accounts.list_bangumi_accounts") as mock_list,
        patch("app.api.bangumi_accounts.get_active_bangumi_account") as mock_active,
    ):
        mock_list.return_value = [oauth_account, manual_account]
        mock_active.return_value = oauth_account
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi/accounts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["active"] == "bangumi-alice"
    accounts = data["data"]
    assert len(accounts) == 2

    # OAuth 账号应暴露 bangumi_user_id 和 expires_at
    oauth_info = next(a for a in accounts if a["section_name"] == "bangumi-alice")
    assert oauth_info["auth_method"] == "oauth"
    assert oauth_info["bangumi_user_id"] == "12345"
    assert oauth_info["expires_at"] == 9999999999
    assert oauth_info["has_token"] is True
    assert oauth_info["is_active"] is True

    # 手动账号 bangumi_user_id 为空，expires_at 为 null
    manual_info = next(a for a in accounts if a["section_name"] == "bangumi-bob")
    assert manual_info["auth_method"] == "manual"
    assert manual_info["bangumi_user_id"] == ""
    assert manual_info["expires_at"] is None
    assert manual_info["has_token"] is True
    assert manual_info["private"] is True


@pytest.mark.asyncio
async def test_list_accounts_empty(app_with_auth):
    """无账号时返回空列表"""
    with (
        patch("app.api.bangumi_accounts.list_bangumi_accounts") as mock_list,
        patch("app.api.bangumi_accounts.get_active_bangumi_account") as mock_active,
    ):
        mock_list.return_value = []
        mock_active.return_value = None
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi/accounts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"] == []
    assert data["active"] is None


@pytest.mark.asyncio
async def test_account_info_does_not_leak_access_token(app_with_auth):
    """AccountInfo 响应不应暴露 access_token 明文"""
    account = {
        "section_name": "bangumi-secret",
        "username": "u",
        "media_server_usernames": [],
        "auth_method": "oauth",
        "nickname": "",
        "avatar": "",
        "bangumi_user_id": "1",
        "expires_at": 9999999999,
        "private": False,
        "is_active": True,
        "access_token": "SECRET_TOKEN_SHOULD_NOT_LEAK",
    }
    with (
        patch("app.api.bangumi_accounts.list_bangumi_accounts") as mock_list,
        patch("app.api.bangumi_accounts.get_active_bangumi_account") as mock_active,
    ):
        mock_list.return_value = [account]
        mock_active.return_value = account
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi/accounts")

    assert resp.status_code == 200
    body = resp.text
    # access_token 明文绝不应出现在响应中
    assert "SECRET_TOKEN_SHOULD_NOT_LEAK" not in body
    # 仅以 has_token 布尔标记表达
    assert resp.json()["data"][0]["has_token"] is True
