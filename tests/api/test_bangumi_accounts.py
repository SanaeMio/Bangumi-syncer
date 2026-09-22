"""Bangumi 账号管理 API 测试。

覆盖 AccountInfo 响应模型字段（bangumi_user_id、expires_at、enabled）、
账号启用/停用接口，以及配置页账号条目的启用/停用入口，确保 OAuth 信息与
启用状态正确暴露给前端账号列表，且后端能力在前端可用。
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api import bangumi_accounts, deps, pages


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
        "is_primary": True,
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
        "is_primary": False,
        "access_token": "manual_token",
    }
    with (
        patch("app.api.bangumi_accounts.list_bangumi_accounts") as mock_list,
        patch("app.api.bangumi_accounts.get_primary_bangumi_account") as mock_primary,
    ):
        mock_list.return_value = [oauth_account, manual_account]
        mock_primary.return_value = oauth_account
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi/accounts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["primary"] == "bangumi-alice"
    accounts = data["data"]
    assert len(accounts) == 2

    # OAuth 账号应暴露 bangumi_user_id 和 expires_at
    oauth_info = next(a for a in accounts if a["section_name"] == "bangumi-alice")
    assert oauth_info["auth_method"] == "oauth"
    assert oauth_info["bangumi_user_id"] == "12345"
    assert oauth_info["expires_at"] == 9999999999
    assert oauth_info["has_token"] is True
    assert oauth_info["is_primary"] is True

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
        patch("app.api.bangumi_accounts.get_primary_bangumi_account") as mock_primary,
    ):
        mock_list.return_value = []
        mock_primary.return_value = None
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi/accounts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"] == []
    assert data["primary"] is None


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
        "is_primary": True,
        "access_token": "SECRET_TOKEN_SHOULD_NOT_LEAK",
    }
    with (
        patch("app.api.bangumi_accounts.list_bangumi_accounts") as mock_list,
        patch("app.api.bangumi_accounts.get_primary_bangumi_account") as mock_primary,
    ):
        mock_list.return_value = [account]
        mock_primary.return_value = account
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


# ---------------------------------------------------------------------------
# 账号启用状态（enabled）：列表回传与启用/停用接口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_reports_enabled_state(app_with_auth):
    """列表回传 enabled：未带该字段的账号行视为启用，显式停用的账号如实返回"""
    account_without_enabled_field = {
        "section_name": "bangumi-plain",
        "username": "plain",
        "media_server_usernames": ["plex_plain"],
        "auth_method": "manual",
        "access_token": "AT",
    }
    disabled_account = {
        "section_name": "bangumi-idle",
        "username": "idle",
        "media_server_usernames": ["plex_idle"],
        "auth_method": "manual",
        "access_token": "AT",
        "enabled": False,
    }
    with (
        patch("app.api.bangumi_accounts.list_bangumi_accounts") as mock_list,
        patch("app.api.bangumi_accounts.get_primary_bangumi_account") as mock_primary,
    ):
        mock_list.return_value = [account_without_enabled_field, disabled_account]
        mock_primary.return_value = account_without_enabled_field
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/bangumi/accounts")

    assert resp.status_code == 200
    accounts = {a["section_name"]: a for a in resp.json()["data"]}
    assert accounts["bangumi-plain"]["enabled"] is True
    assert accounts["bangumi-idle"]["enabled"] is False


@pytest.mark.asyncio
async def test_disable_account_writes_enabled_false(app_with_auth):
    """停用账号：以 enabled=False 落库，并回传停用提示"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account") as mock_get,
        patch("app.api.bangumi_accounts.set_enabled_bangumi_account") as mock_set,
    ):
        mock_get.return_value = {"section_name": "bangumi-alice", "enabled": True}
        mock_set.return_value = True
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bangumi-alice/enabled",
                json={"enabled": False},
            )

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "message": "账号已停用"}
    mock_set.assert_called_once_with("bangumi-alice", False)


@pytest.mark.asyncio
async def test_enable_account_writes_enabled_true(app_with_auth):
    """启用账号：以 enabled=True 落库，并回传启用提示"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account") as mock_get,
        patch("app.api.bangumi_accounts.set_enabled_bangumi_account") as mock_set,
    ):
        mock_get.return_value = {"section_name": "bangumi-alice", "enabled": False}
        mock_set.return_value = True
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bangumi-alice/enabled",
                json={"enabled": True},
            )

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "message": "账号已启用"}
    mock_set.assert_called_once_with("bangumi-alice", True)


@pytest.mark.asyncio
async def test_set_account_enabled_unknown_account_returns_404(app_with_auth):
    """账号不存在时返回 404，且不写入启用状态"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account") as mock_get,
        patch("app.api.bangumi_accounts.set_enabled_bangumi_account") as mock_set,
    ):
        mock_get.return_value = None
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bangumi-missing/enabled",
                json={"enabled": False},
            )

    assert resp.status_code == 404
    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_set_account_enabled_write_failure_returns_500(app_with_auth):
    """落库失败时返回 500，不误报成功"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account") as mock_get,
        patch("app.api.bangumi_accounts.set_enabled_bangumi_account") as mock_set,
    ):
        mock_get.return_value = {"section_name": "bangumi-alice"}
        mock_set.return_value = False
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bangumi-alice/enabled",
                json={"enabled": False},
            )

    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# 配置页入口：账号条目提供「停用 / 启用」
# ---------------------------------------------------------------------------


def _fetch_config_html() -> str:
    """以已认证用户访问 /config，返回渲染后的 HTML。"""
    app = FastAPI()
    app.include_router(pages.router)
    with patch.object(
        pages, "get_current_user_from_cookie", return_value={"username": "u"}
    ):
        client = TestClient(app)
        response = client.get("/config", follow_redirects=False)
    assert response.status_code == 200, f"期望 200，实际 {response.status_code}"
    return response.text


def test_config_page_wires_account_enabled_toggle():
    """配置页账号条目提供「停用 / 启用」入口并请求启用状态接口

    账号列表由前端脚本渲染，故校验页面脚本中的入口、请求路径与停用标记
    均存在，避免出现「后端有接口、前端无入口」的断链。
    """
    html = _fetch_config_html()
    assert "setBangumiAccountEnabled(" in html, (
        "账号条目应绑定 setBangumiAccountEnabled"
    )
    assert "accounts/${encodeURIComponent(sectionName)}/enabled" in html, (
        "停用/启用应请求账号启用状态接口"
    )
    assert "bi-pause-circle" in html and "bi-play-circle" in html, (
        "账号条目应同时提供停用与启用按钮"
    )
    assert "已停用" in html, "停用的账号应在账号条目上标记为『已停用』"


def test_config_page_keeps_other_badges_when_marking_primary_account():
    """标记首选账号时仅更新首选徽章，避免覆盖 OAuth / 已停用徽章

    首选徽章带 ``js-primary-badge`` 标记，停用徽章带 ``js-enabled-badge``；
    首选状态刷新只应移除前者的旧节点。
    """
    html = _fetch_config_html()
    assert "js-primary-badge" in html, "首选徽章应带 js-primary-badge 标记"
    assert "querySelectorAll('.js-primary-badge')" in html, (
        "刷新首选徽章时应只移除首选徽章节点，保留其余徽章"
    )
    assert "js-enabled-badge" in html, "停用徽章应带 js-enabled-badge 标记"


@pytest.mark.asyncio
async def test_set_primary_account_sets_section_primary(app_with_auth):
    """设为首选：以目标段名落库并回传首选提示"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account") as mock_get,
        patch("app.api.bangumi_accounts.set_primary_bangumi_account") as mock_set,
    ):
        mock_get.return_value = {"section_name": "bangumi-alice"}
        mock_set.return_value = True
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/bangumi/accounts/bangumi-alice/primary")

    assert resp.status_code == 200
    assert resp.json()["message"] == "已设为首选账号"
    mock_set.assert_called_once_with("bangumi-alice")


@pytest.mark.asyncio
async def test_set_primary_account_unknown_account_returns_404(app_with_auth):
    """账号不存在时返回 404，且不切换首选"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account") as mock_get,
        patch("app.api.bangumi_accounts.set_primary_bangumi_account") as mock_set,
    ):
        mock_get.return_value = None
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/bangumi/accounts/bangumi-missing/primary")

    assert resp.status_code == 404
    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_set_primary_account_write_failure_returns_500(app_with_auth):
    """落库失败时返回 500，不误报成功"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account") as mock_get,
        patch("app.api.bangumi_accounts.set_primary_bangumi_account") as mock_set,
    ):
        mock_get.return_value = {"section_name": "bangumi-alice"}
        mock_set.return_value = False
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/bangumi/accounts/bangumi-alice/primary")

    assert resp.status_code == 500
