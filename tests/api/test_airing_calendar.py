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
    """GET /api/airing-calendar/accounts"""

    @pytest.mark.asyncio
    async def test_single_mode_with_config(self, app_with_auth):
        """单用户模式且已配置账号：返回 1 个账号，active='bangumi'"""
        with patch.object(
            airing_calendar.config_manager,
            "get",
            side_effect=lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
                ("bangumi", "username"): "alice",
                ("bangumi", "access_token"): "token123",
            }.get((section, key), fallback),
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
        """单用户模式但未配置账号：返回空列表"""
        with patch.object(
            airing_calendar.config_manager,
            "get",
            side_effect=lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
                ("bangumi", "username"): "",
                ("bangumi", "access_token"): "",
            }.get((section, key), fallback),
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
        """多用户模式：返回所有 [bangumi-*] 账号段"""
        configs = {
            "bangumi-alice": {"username": "alice", "access_token": "t1"},
            "bangumi-bob": {"username": "bob", "access_token": "t2"},
        }
        with (
            patch.object(
                airing_calendar.config_manager,
                "get",
                side_effect=lambda section, key, fallback=None: {
                    ("sync", "mode"): "multi",
                }.get((section, key), fallback),
            ),
            patch.object(
                airing_calendar.config_manager,
                "get_bangumi_configs",
                return_value=configs,
            ),
            patch.object(
                airing_calendar.config_manager,
                "get_active_bangumi_config",
                return_value={"username": "alice", "access_token": "t1"},
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
        """多用户模式但无账号段：返回空列表"""
        with (
            patch.object(
                airing_calendar.config_manager,
                "get",
                side_effect=lambda section, key, fallback=None: {
                    ("sync", "mode"): "multi",
                }.get((section, key), fallback),
            ),
            patch.object(
                airing_calendar.config_manager,
                "get_bangumi_configs",
                return_value={},
            ),
            patch.object(
                airing_calendar.config_manager,
                "get_active_bangumi_config",
                return_value=None,
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
        assert data["accounts"] == []
        assert data["active"] is None
