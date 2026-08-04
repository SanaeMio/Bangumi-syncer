"""番剧放送日历 API 测试

验证：
1. Archive 未启用时返回 archive_disabled
2. Archive 启用但 db 不存在时返回 archive_not_imported
3. 正常查询返回按日期分组的结果
4. 无 Bangumi 配置时返回 watching_unavailable（不降级为全部放送）
5. 有配置时调用 get_watching_subject_ids 过滤
6. days 参数规范化
7. 连续日期序列含空日期
8. 响应含 today 字段（前端以此作为时区基准）
9. account 参数传递给 _build_bangumi_api（多用户切换）
10. /accounts 端点：单用户模式
11. /accounts 端点：多用户模式
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import airing_calendar, deps


@pytest.fixture
def app_with_auth():
    app = FastAPI()
    app.include_router(airing_calendar.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def mock_archive_enabled():
    """mock bangumi_archive 为已启用且 db 存在"""
    # 重置 reload_config 缓存时间戳，避免测试间状态泄漏
    airing_calendar._last_reload_config_ts = 0.0
    with (
        patch.object(airing_calendar.bangumi_archive, "enabled", True),
        patch.object(airing_calendar.bangumi_archive, "reload_config", MagicMock()),
        patch(
            "app.api.airing_calendar.bangumi_archive.get_active_db_path"
        ) as mock_path,
    ):
        mock_path.return_value.exists.return_value = True
        yield mock_path


@pytest.fixture
def mock_no_bangumi_config():
    """mock 无 Bangumi 账号配置（_build_bangumi_api 返回 None）"""
    with patch(
        "app.api.airing_calendar._build_bangumi_api",
        return_value=None,
    ):
        yield


@pytest.fixture
def mock_bangumi_config_with_watching():
    """mock 有 Bangumi 账号配置，且在看列表返回 {1, 2}"""
    mock_api = MagicMock()
    mock_api.username = "testuser"
    with (
        patch(
            "app.api.airing_calendar._build_bangumi_api",
            return_value=mock_api,
        ),
        patch(
            "app.api.airing_calendar.get_watching_subject_ids",
            return_value={1, 2},
        ),
    ):
        yield mock_api


class TestAiringCalendarEndpoint:
    """GET /api/airing-calendar"""

    @pytest.mark.asyncio
    async def test_archive_disabled(self, app_with_auth):
        """Archive 未启用时返回 archive_disabled"""
        archive = airing_calendar.bangumi_archive
        with (
            patch.object(archive, "enabled", False),
            patch.object(archive, "reload_config", MagicMock()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "archive_disabled"
        assert data["archive_enabled"] is False
        assert data["days"] == []
        assert data["total_episodes"] == 0
        # 即使 Archive 未启用，也应返回 today 字段
        assert "today" in data

    @pytest.mark.asyncio
    async def test_archive_not_imported(self, app_with_auth):
        """Archive 启用但 db 不存在"""
        with (
            patch.object(airing_calendar.bangumi_archive, "enabled", True),
            patch.object(airing_calendar.bangumi_archive, "reload_config", MagicMock()),
            patch(
                "app.api.airing_calendar.bangumi_archive.get_active_db_path"
            ) as mock_path,
        ):
            mock_path.return_value.exists.return_value = False
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "archive_not_imported"
        assert data["archive_enabled"] is True
        assert data["days"] == []

    @pytest.mark.asyncio
    async def test_normal_query(
        self, app_with_auth, mock_archive_enabled, mock_bangumi_config_with_watching
    ):
        """正常查询返回按日期分组的结果（有 Bangumi 配置 + 在看列表）"""
        from datetime import date, timedelta

        today = date.today()
        today_str = today.isoformat()
        tomorrow_str = (today + timedelta(days=1)).isoformat()

        mock_rows = [
            {
                "episode_id": 101,
                "subject_id": 1,
                "subject_name": "Anime A",
                "subject_name_cn": "动画A",
                "subject_type": 2,
                "ep_name": "EP01",
                "ep_name_cn": "第一集",
                "ep_sort": 1,
                "airdate": today_str,
            },
            {
                "episode_id": 201,
                "subject_id": 2,
                "subject_name": "Anime B",
                "subject_name_cn": "动画B",
                "subject_type": 2,
                "ep_name": "EP01",
                "ep_name_cn": "第一集",
                "ep_sort": 1,
                "airdate": tomorrow_str,
            },
        ]
        with patch.object(
            airing_calendar.archive_store,
            "get_episodes_by_airdate",
            return_value=mock_rows,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar", params={"days": 7})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["archive_enabled"] is True
        assert data["total_episodes"] == 2
        assert len(data["days"]) == 7  # 连续 7 天
        # today 字段与第一天日期一致
        assert data["today"] == today_str
        # 第一天有 1 集
        assert data["days"][0]["date"] == today_str
        assert len(data["days"][0]["episodes"]) == 1
        assert data["days"][0]["episodes"][0]["subject_name"] == "Anime A"
        # 第二天有 1 集
        assert data["days"][1]["date"] == tomorrow_str
        assert len(data["days"][1]["episodes"]) == 1
        # 临时 api 实例应被关闭（资源释放）
        mock_bangumi_config_with_watching.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_config_returns_unavailable(
        self, app_with_auth, mock_archive_enabled, mock_no_bangumi_config
    ):
        """无 Bangumi 配置时返回 watching_unavailable

        "我的追番"语义下不降级为全部放送。
        """
        with patch.object(
            airing_calendar.archive_store,
            "get_episodes_by_airdate",
            return_value=[],
        ) as mock_query:
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar")

        assert resp.status_code == 200
        data = resp.json()
        # 不降级为全部放送，返回 watching_unavailable
        assert data["status"] == "watching_unavailable"
        assert data["days"] == []
        # 不应查询 Archive（避免无意义 IO）
        mock_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_fails_returns_unavailable(
        self, app_with_auth, mock_archive_enabled
    ):
        """获取在看列表失败时返回 watching_unavailable"""
        mock_api = MagicMock()
        mock_api.username = "testuser"
        with (
            patch(
                "app.api.airing_calendar._build_bangumi_api",
                return_value=mock_api,
            ),
            patch(
                "app.api.airing_calendar.get_watching_subject_ids",
                side_effect=RuntimeError("API 不可达"),
            ),
            patch.object(
                airing_calendar.archive_store,
                "get_episodes_by_airdate",
                return_value=[],
            ) as mock_query,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "watching_unavailable"
        assert data["days"] == []
        # 不应查询 Archive
        mock_query.assert_not_called()
        # 临时 api 实例仍应被关闭
        mock_api.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_config_filters_subject_ids(
        self, app_with_auth, mock_archive_enabled, mock_bangumi_config_with_watching
    ):
        """有配置时调用 get_watching_subject_ids 过滤"""
        with patch.object(
            airing_calendar.archive_store,
            "get_episodes_by_airdate",
            return_value=[],
        ) as mock_query:
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        # subject_ids 应为 {1, 2}
        call_kwargs = mock_query.call_args.kwargs
        assert call_kwargs["subject_ids"] == {1, 2}
        # 临时 api 实例应被关闭（资源释放）
        mock_bangumi_config_with_watching.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_account_param_passed_to_build_api(
        self, app_with_auth, mock_archive_enabled
    ):
        """account 参数应传递给 _build_bangumi_api（多用户切换）"""
        mock_api = MagicMock()
        mock_api.username = "multiuser"
        with (
            patch(
                "app.api.airing_calendar._build_bangumi_api",
                return_value=mock_api,
            ) as mock_build,
            patch(
                "app.api.airing_calendar.get_watching_subject_ids",
                return_value={10, 20},
            ),
            patch.object(
                airing_calendar.archive_store,
                "get_episodes_by_airdate",
                return_value=[],
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/airing-calendar",
                    params={"account": "bangumi-alice"},
                )

        assert resp.status_code == 200
        # account 参数应传给 _build_bangumi_api
        mock_build.assert_called_once_with("bangumi-alice")
        mock_api.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_days_normalized(
        self, app_with_auth, mock_archive_enabled, mock_bangumi_config_with_watching
    ):
        """days 非 7/14/30 时规范化为 30"""
        with patch.object(
            airing_calendar.archive_store,
            "get_episodes_by_airdate",
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar", params={"days": 100})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["days"]) == 30  # 规范化为 30

    @pytest.mark.asyncio
    async def test_empty_days_have_correct_weekday(
        self, app_with_auth, mock_archive_enabled, mock_bangumi_config_with_watching
    ):
        """空日期格子也应有正确的 weekday"""
        from datetime import date

        today = date.today()
        with patch.object(
            airing_calendar.archive_store,
            "get_episodes_by_airdate",
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar", params={"days": 7})

        data = resp.json()
        # 第一天 weekday 应与今天一致
        assert data["days"][0]["weekday"] == today.weekday()
        # 所有日期 episodes 为空
        assert all(d["episodes"] == [] for d in data["days"])


class TestBangumiAccountsEndpoint:
    """GET /api/airing-calendar/accounts

    DB 为唯一真相源：端点从 ``app.core.accounts`` 读取账号列表，
    ``mode`` 由账号数量推导（>1 为 multi，否则 single）。
    """

    @pytest.mark.asyncio
    async def test_single_mode_with_config(self, app_with_auth):
        """单用户模式且已配置账号：返回 1 个账号，active='bangumi'"""
        with (
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[{"section_name": "bangumi", "username": "alice"}],
            ),
            patch(
                "app.core.accounts.get_active_bangumi_account",
                return_value={"section_name": "bangumi", "username": "alice"},
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar/accounts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "single"
        assert len(data["accounts"]) == 1
        assert data["accounts"][0]["section_name"] == "bangumi"
        assert data["accounts"][0]["username"] == "alice"
        assert data["active"] == "bangumi"

    @pytest.mark.asyncio
    async def test_single_mode_no_config(self, app_with_auth):
        """无账号配置：返回空列表，mode 由账号数量推导为 single"""
        with (
            patch("app.core.accounts.list_bangumi_accounts", return_value=[]),
            patch("app.core.accounts.get_active_bangumi_account", return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar/accounts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "single"
        assert data["accounts"] == []
        assert data["active"] is None

    @pytest.mark.asyncio
    async def test_multi_mode_returns_all_accounts(self, app_with_auth):
        """多用户模式：返回所有 Bangumi 账号段（>1 个账号推导为 multi）"""
        with (
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[
                    {"section_name": "bangumi-alice", "username": "alice"},
                    {"section_name": "bangumi-bob", "username": "bob"},
                ],
            ),
            patch(
                "app.core.accounts.get_active_bangumi_account",
                return_value={"section_name": "bangumi-alice", "username": "alice"},
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar/accounts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "multi"
        assert len(data["accounts"]) == 2
        usernames = {a["username"] for a in data["accounts"]}
        assert usernames == {"alice", "bob"}
        # active 应为 alice 对应段
        assert data["active"] == "bangumi-alice"

    @pytest.mark.asyncio
    async def test_multi_mode_no_accounts(self, app_with_auth):
        """无账号配置：返回空列表，mode 由账号数量推导为 single"""
        with (
            patch("app.core.accounts.list_bangumi_accounts", return_value=[]),
            patch("app.core.accounts.get_active_bangumi_account", return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar/accounts")

        assert resp.status_code == 200
        data = resp.json()
        # 0 个账号 → mode="single"（端点按账号数量推导）
        assert data["mode"] == "single"
        assert data["accounts"] == []
        assert data["active"] is None


class TestAiringCalendarAccessControl:
    """多用户模式下 airing-calendar 的越权防护

    认证开启且账号数 > 1 时，媒体用户（应用登录名绑定到某账号的
    media_server_usernames）只能访问自己绑定的账号，不能通过 account
    参数越权查看他人"在看"列表；管理员/非媒体用户可见全部。
    """

    @pytest.fixture
    def app_with_media_user(self):
        """模拟认证开启的媒体用户 alice（绑定到 bangumi-alice）"""
        app = FastAPI()
        app.include_router(airing_calendar.router)

        async def mock_get_current_user(request=None, credentials=None):
            # 无 auth_disabled → 认证开启
            return {"username": "alice", "id": 2}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user
        yield app
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_media_user_cannot_access_others_account(
        self, app_with_media_user, mock_archive_enabled
    ):
        """媒体用户越权指定他人账号段名：返回 watching_unavailable，不构造 api"""
        with (
            patch("app.core.accounts.count_bangumi_accounts", return_value=2),
            patch(
                "app.core.accounts.get_user_mappings",
                return_value={"alice": "bangumi-alice", "bob": "bangumi-bob"},
            ),
            patch("app.api.airing_calendar._build_bangumi_api") as mock_build,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_media_user),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/airing-calendar",
                    params={"account": "bangumi-bob"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "watching_unavailable"
        # 越权请求不应构造 BangumiApi（不调用 API、不泄露他人数据）
        mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_media_user_can_access_own_account(
        self, app_with_media_user, mock_archive_enabled
    ):
        """媒体用户访问自己绑定的账号：正常构造 api（用绑定段名）"""
        mock_api = MagicMock()
        mock_api.username = "alice"
        with (
            patch("app.core.accounts.count_bangumi_accounts", return_value=2),
            patch(
                "app.core.accounts.get_user_mappings",
                return_value={"alice": "bangumi-alice", "bob": "bangumi-bob"},
            ),
            patch(
                "app.api.airing_calendar._build_bangumi_api",
                return_value=mock_api,
            ) as mock_build,
            patch(
                "app.api.airing_calendar.get_watching_subject_ids",
                return_value={1},
            ),
            patch.object(
                airing_calendar.archive_store,
                "get_episodes_by_airdate",
                return_value=[],
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_media_user),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/airing-calendar",
                    params={"account": "bangumi-alice"},
                )

        assert resp.status_code == 200
        # 应使用用户自己绑定的段名构造 api
        mock_build.assert_called_once_with("bangumi-alice")
        mock_api.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_media_user_account_param_force_bound(
        self, app_with_media_user, mock_archive_enabled
    ):
        """媒体用户不传 account：自动使用自己绑定的账号"""
        mock_api = MagicMock()
        mock_api.username = "alice"
        with (
            patch("app.core.accounts.count_bangumi_accounts", return_value=2),
            patch(
                "app.core.accounts.get_user_mappings",
                return_value={"alice": "bangumi-alice"},
            ),
            patch(
                "app.api.airing_calendar._build_bangumi_api",
                return_value=mock_api,
            ) as mock_build,
            patch(
                "app.api.airing_calendar.get_watching_subject_ids",
                return_value={1},
            ),
            patch.object(
                airing_calendar.archive_store,
                "get_episodes_by_airdate",
                return_value=[],
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_media_user),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar")

        assert resp.status_code == 200
        # 未传 account 时强制使用绑定段名（而非激活账号）
        mock_build.assert_called_once_with("bangumi-alice")

    @pytest.mark.asyncio
    async def test_admin_access_any_account(self, mock_archive_enabled):
        """管理员（登录名未绑定到任何账号）可访问任意账号段名"""
        app = FastAPI()
        app.include_router(airing_calendar.router)

        async def mock_admin_user(request=None, credentials=None):
            # admin 不在 user_mappings 中 → 非媒体用户，可访问任意账号
            return {"username": "admin", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_admin_user

        mock_api = MagicMock()
        mock_api.username = "bob"
        with (
            patch("app.core.accounts.count_bangumi_accounts", return_value=2),
            patch(
                "app.core.accounts.get_user_mappings",
                return_value={"alice": "bangumi-alice", "bob": "bangumi-bob"},
            ),
            patch(
                "app.api.airing_calendar._build_bangumi_api",
                return_value=mock_api,
            ) as mock_build,
            patch(
                "app.api.airing_calendar.get_watching_subject_ids",
                return_value={1},
            ),
            patch.object(
                airing_calendar.archive_store,
                "get_episodes_by_airdate",
                return_value=[],
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/airing-calendar",
                    params={"account": "bangumi-bob"},
                )

        assert resp.status_code == 200
        # admin 可访问任意账号，account 参数原样传递
        mock_build.assert_called_once_with("bangumi-bob")
        mock_api.close.assert_called_once()


class TestAccountsEndpointAccessControl:
    """/accounts 端点的多用户隔离：媒体用户只看到自己绑定的账号"""

    @pytest.mark.asyncio
    async def test_media_user_only_sees_own_account(self):
        """认证开启 + 多用户：媒体用户只返回自己绑定的账号"""
        app = FastAPI()
        app.include_router(airing_calendar.router)

        async def mock_alice(request=None, credentials=None):
            return {"username": "alice", "id": 2}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_alice

        with (
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[
                    {
                        "section_name": "bangumi-alice",
                        "username": "alice",
                        "media_server_usernames": ["alice"],
                    },
                    {
                        "section_name": "bangumi-bob",
                        "username": "bob",
                        "media_server_usernames": ["bob"],
                    },
                ],
            ),
            patch(
                "app.core.accounts.get_active_bangumi_account",
                return_value={
                    "section_name": "bangumi-alice",
                    "username": "alice",
                },
            ),
            patch("app.core.accounts.count_bangumi_accounts", return_value=2),
            patch(
                "app.core.accounts.get_user_mappings",
                return_value={"alice": "bangumi-alice", "bob": "bangumi-bob"},
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar/accounts")

        assert resp.status_code == 200
        data = resp.json()
        # alice 只看到自己绑定的账号，不泄露 bob
        assert len(data["accounts"]) == 1
        assert data["accounts"][0]["section_name"] == "bangumi-alice"

    @pytest.mark.asyncio
    async def test_admin_sees_all_accounts(self):
        """管理员（未绑定到任何账号）可见全部账号"""
        app = FastAPI()
        app.include_router(airing_calendar.router)

        async def mock_admin(request=None, credentials=None):
            return {"username": "admin", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_admin

        with (
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[
                    {
                        "section_name": "bangumi-alice",
                        "username": "alice",
                        "media_server_usernames": ["alice"],
                    },
                    {
                        "section_name": "bangumi-bob",
                        "username": "bob",
                        "media_server_usernames": ["bob"],
                    },
                ],
            ),
            patch(
                "app.core.accounts.get_active_bangumi_account",
                return_value={
                    "section_name": "bangumi-alice",
                    "username": "alice",
                },
            ),
            patch("app.core.accounts.count_bangumi_accounts", return_value=2),
            patch(
                "app.core.accounts.get_user_mappings",
                return_value={"alice": "bangumi-alice", "bob": "bangumi-bob"},
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/airing-calendar/accounts")

        assert resp.status_code == 200
        data = resp.json()
        # admin 可见全部账号
        assert len(data["accounts"]) == 2
