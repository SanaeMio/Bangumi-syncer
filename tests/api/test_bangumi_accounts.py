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


def test_config_page_account_actions_wrap_on_mobile():
    """账号条目的操作按钮使用可换行工具条，窄屏不被 btn-group 挤压。

    btn-group 不支持换行，多账号操作（设为首选 / 停用 / 断开 OAuth / 删除）
    在移动端会横向溢出。条目头部故拆成两个 flex 组：标题与徽标一组、按钮一组，
    两组内部靠 ``align-items-center`` 纵向居中，放不下时按钮整组折到下一行并靠右。
    """
    html = _fetch_config_html()
    start = html.index(
        '<div class="d-flex flex-wrap align-items-center justify-content-between'
    )
    end = html.index('<div class="bangumi-account-fields">', start)
    block = html[start:end]
    assert "account-title-row d-flex flex-wrap align-items-center gap-2" in block, (
        "账号标题与徽标应在同一行内纵向居中"
    )
    assert "flex-wrap align-items-center gap-2 ms-auto" in block, (
        "操作按钮应可换行并在折行后靠右对齐"
    )
    assert "btn-group" not in block, "btn-group 不支持换行，不应用于账号操作条"
    assert "btn btn-sm" in block, "操作按钮应保持小尺寸"


def test_config_page_account_toggle_shares_field_row():
    """「观看记录仅自己可见」开关与三个输入框同排，且和其它配置项一样占两行。

    字段行是网格：三个输入 + 开关。开关自身也是「标签 + 控件」两行，
    控件高度与输入框一致，右侧文字随勾选在开启/关闭之间切换。
    """
    html = _fetch_config_html()
    assert '<div class="bangumi-account-fields">' in html, "字段行应使用网格容器"
    assert "bangumi-account-field--switch" in html, "开关应作为字段行的一员"
    assert ">观看记录仅自己可见</label>" in html, "开关上方应有字段标签"
    assert "js-private-state" in html, "开关右侧应显示开启/关闭状态文字"
    assert "this.checked ? '开启' : '关闭'" in html, "勾选后状态文字应即时切换"


def test_config_page_account_labels_stay_single_line():
    """账号条目的字段标签保持单行，避免窄屏折行把同排输入框顶到不同高度。

    「访问令牌」原带「（由 OAuth 管理）」后缀，在 992px 卡片宽度下会折成两行，
    把同排第三个输入框往下挤；OAuth 身份已由头部徽标表达，标签保持单行即可。
    """
    html = _fetch_config_html()
    assert "（由 OAuth 管理）" not in html, "访问令牌标签不再附带 OAuth 后缀说明"
    assert 'title="OAuth 授权账号的访问令牌由系统自动管理"' in html, (
        "OAuth 账号的令牌标签应通过 title 保留说明"
    )


# ---------------------------------------------------------------------------
# 批量保存：配置页「保存配置」一次性提交全部账号
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_upsert_creates_and_updates_in_one_request(app_with_auth):
    """批量保存：新增自动生成段名，更新保留原段名，留空令牌沿用原令牌"""
    existing = {
        "section_name": "bangumi-alice",
        "username": "alice",
        "access_token": "old_token",
    }
    saved: list[dict] = []

    def fake_get(section):
        return existing if section == "bangumi-alice" else None

    with (
        patch("app.api.bangumi_accounts.get_bangumi_account", side_effect=fake_get),
        patch(
            "app.api.bangumi_accounts._generate_section_name",
            side_effect=lambda name: f"bangumi-{name}",
        ),
        patch(
            "app.api.bangumi_accounts.save_bangumi_account",
            side_effect=lambda acc: saved.append(acc) or True,
        ),
        patch("app.api.bangumi_accounts.count_bangumi_accounts", return_value=2),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bulk",
                json={
                    "accounts": [
                        {
                            "section_name": "bangumi-alice",
                            "username": "alice",
                            "access_token": "",
                            "media_server_usernames": ["plex_alice"],
                            "private": True,
                        },
                        {
                            "section_name": None,
                            "username": "bob",
                            "access_token": "bob_token",
                            "media_server_usernames": [],
                            "private": False,
                        },
                    ]
                },
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert [item["section_name"] for item in body["data"]] == [
        "bangumi-alice",
        "bangumi-bob",
    ]
    assert [item["created"] for item in body["data"]] == [False, True]
    assert body["changed"] == 2
    assert "新增 1 个、更新 1 个" in body["message"]

    assert len(saved) == 2
    # 令牌留空表示「沿用原值」，不能被清空
    assert saved[0]["access_token"] == "old_token"
    assert saved[0]["private"] is True
    assert saved[0]["media_server_usernames"] == ["plex_alice"]
    assert saved[1]["access_token"] == "bob_token"


@pytest.mark.asyncio
async def test_bulk_upsert_skips_unchanged_accounts(app_with_auth):
    """页面没改动账号时直接保存配置：不重复写库，也不谎报「更新」"""
    existing = {
        "section_name": "bangumi-alice",
        "username": "alice",
        "access_token": "old_token",
        "media_server_usernames": ["plex_alice"],
        "private": True,
    }

    with (
        patch(
            "app.api.bangumi_accounts.get_bangumi_account",
            side_effect=lambda section: (
                existing if section == "bangumi-alice" else None
            ),
        ),
        patch("app.api.bangumi_accounts.save_bangumi_account") as mock_save,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bulk",
                json={
                    "accounts": [
                        {
                            "section_name": "bangumi-alice",
                            "username": "alice",
                            "access_token": "",
                            "media_server_usernames": ["plex_alice"],
                            "private": True,
                        }
                    ]
                },
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["changed"] == 0
    assert body["message"] == "账号信息没有变化"
    assert body["data"][0]["changed"] is False
    mock_save.assert_not_called()


def test_config_page_account_token_input_avoids_password_autofill():
    """令牌框不再让浏览器把登录密码自动填进来

    令牌框是 type="password"，密码管理器会当成密码输入框自动填充；
    存进去的密码换来的是 Bangumi 401「令牌已过期」。仓库约定密码框统一
    autocomplete="off" + 各密码管理器忽略属性，因此保留约定，
    改为在提交前按令牌形态挡一道，给出看得懂的提示。
    """
    html = _fetch_config_html()
    assert 'class="form-control pe-5 bangumi-account-token"' in html
    assert 'autocomplete="off"' in html, "令牌框仍按仓库约定声明 off"
    assert "访问令牌格式不对" in html, "提交前应校验令牌形态，拦住被自动填进来的密码"


@pytest.mark.asyncio
async def test_bulk_upsert_manual_token_drops_stale_oauth_metadata(app_with_auth):
    """OAuth 账号手动粘贴新令牌后按手动模式保存，不再显示「令牌已过期」

    旧实现无条件沿用 existing 的 auth_method/refresh_token/expires_at，
    于是新令牌配着上一份 OAuth 的过期时间落库，配置页一直标「已过期」，
    同步层还会反复用旧 refresh_token 去刷新。
    """
    existing = {
        "section_name": "bangumi-alice",
        "username": "alice",
        "media_server_usernames": ["plex_alice"],
        "auth_method": "oauth",
        "access_token": "expired_oauth_token",
        "refresh_token": "stale_refresh_token",
        "token_type": "Bearer",
        "expires_at": 1,  # 1970 年，早就过期
        "bangumi_user_id": "12345",
        "nickname": "Alice",
        "avatar": "https://example.com/a.png",
        "private": False,
        "is_primary": True,
        "enabled": True,
    }
    saved: list[dict] = []

    with (
        patch(
            "app.api.bangumi_accounts.get_bangumi_account",
            side_effect=lambda section: (
                existing if section == "bangumi-alice" else None
            ),
        ),
        patch(
            "app.api.bangumi_accounts.save_bangumi_account",
            side_effect=lambda acc: saved.append(acc) or True,
        ),
        patch("app.api.bangumi_accounts.count_bangumi_accounts", return_value=2),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bulk",
                json={
                    "accounts": [
                        {
                            "section_name": "bangumi-alice",
                            "username": "alice",
                            "access_token": "brand_new_token",
                            "media_server_usernames": ["plex_alice"],
                            "private": False,
                        }
                    ]
                },
            )

    assert resp.status_code == 200
    assert resp.json()["changed"] == 1
    assert len(saved) == 1
    account = saved[0]
    assert account["access_token"] == "brand_new_token"
    assert account["auth_method"] == "manual"
    assert account["refresh_token"] == ""
    assert account["expires_at"] is None
    # 身份信息（用户名/昵称/头像/媒体服务器名）与状态位不受影响
    assert account["bangumi_user_id"] == "12345"
    assert account["nickname"] == "Alice"
    assert account["avatar"] == "https://example.com/a.png"
    assert account["is_primary"] is True
    assert account["enabled"] is True


@pytest.mark.asyncio
async def test_bulk_upsert_keeps_oauth_metadata_when_token_left_blank(app_with_auth):
    """令牌留空（未粘贴新令牌）时仍保留 OAuth 元数据，不会误降级为手动模式"""
    existing = {
        "section_name": "bangumi-alice",
        "username": "alice",
        "media_server_usernames": ["plex_alice"],
        "auth_method": "oauth",
        "access_token": "oauth_token",
        "refresh_token": "refresh_token",
        "token_type": "Bearer",
        "expires_at": 1893456000,
        "private": False,
    }
    saved: list[dict] = []

    with (
        patch(
            "app.api.bangumi_accounts.get_bangumi_account",
            side_effect=lambda section: (
                existing if section == "bangumi-alice" else None
            ),
        ),
        patch(
            "app.api.bangumi_accounts.save_bangumi_account",
            side_effect=lambda acc: saved.append(acc) or True,
        ),
        patch("app.api.bangumi_accounts.count_bangumi_accounts", return_value=2),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bulk",
                json={
                    "accounts": [
                        {
                            "section_name": "bangumi-alice",
                            "username": "alice",
                            "access_token": "",
                            "media_server_usernames": ["plex_alice", "emby_alice"],
                            "private": False,
                        }
                    ]
                },
            )

    assert resp.status_code == 200
    assert len(saved) == 1
    account = saved[0]
    assert account["access_token"] == "oauth_token"
    assert account["auth_method"] == "oauth"
    assert account["refresh_token"] == "refresh_token"
    assert account["expires_at"] == 1893456000


@pytest.mark.asyncio
async def test_bulk_upsert_rejects_whole_batch_without_writing(app_with_auth):
    """任一账号校验失败则整批拒绝，不留下部分写入"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account", return_value=None),
        patch("app.api.bangumi_accounts.save_bangumi_account") as mock_save,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bulk",
                json={
                    "accounts": [
                        {"username": "alice", "access_token": "a_token"},
                        {"username": "  ", "access_token": "b_token"},
                    ]
                },
            )

    assert resp.status_code == 400
    assert "第 2 个账号" in resp.json()["detail"]
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_upsert_unknown_section_returns_404(app_with_auth):
    """更新一个已删除的账号段时返回 404，而不是静默新建"""
    with (
        patch("app.api.bangumi_accounts.get_bangumi_account", return_value=None),
        patch("app.api.bangumi_accounts.save_bangumi_account") as mock_save,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/bangumi/accounts/bulk",
                json={
                    "accounts": [
                        {
                            "section_name": "bangumi-gone",
                            "username": "gone",
                            "access_token": "t",
                        }
                    ]
                },
            )

    assert resp.status_code == 404
    mock_save.assert_not_called()


def test_config_page_saves_accounts_with_config():
    """配置页「保存配置」一并提交账号，账号条目不再有独立保存按钮

    账号存数据库、常规配置存 INI，界面上的「保存配置」需要同时收尾这两处，
    否则用户填完账号后会以为已经保存。
    """
    html = _fetch_config_html()
    assert "collectBangumiAccounts(" in html, "保存配置应先收集页面上的账号条目"
    assert "saveBangumiAccounts(" in html, "保存配置应调用批量保存接口"
    assert "/api/bangumi/accounts/bulk" in html, "账号批量保存应请求 bulk 接口"
    assert "saveBangumiAccountRow" not in html, "账号条目不应再有独立的保存逻辑"
