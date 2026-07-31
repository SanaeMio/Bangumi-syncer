"""番剧放送日历 API 测试

验证：
1. Archive 未启用时返回 archive_disabled
2. Archive 启用但 db 不存在时返回 archive_not_imported
3. 正常查询返回按日期分组的结果
4. only_watching 无 Bangumi 配置时降级为全部放送
5. only_watching 有配置时调用 get_watching_subject_ids
6. days 参数规范化
7. 连续日期序列含空日期
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
    """mock 无 Bangumi 账号配置"""
    with patch.object(
        airing_calendar.config_manager,
        "get_active_bangumi_config",
        return_value=None,
    ):
        yield


class TestAiringCalendarEndpoint:
    """GET /api/airing-calendar"""

    @pytest.mark.asyncio
    async def test_archive_disabled(self, app_with_auth):
        """Archive 未启用时返回 archive_disabled"""
        with (
            patch.object(airing_calendar.bangumi_archive, "enabled", False),
            patch.object(airing_calendar.bangumi_archive, "reload_config", MagicMock()),
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
        self, app_with_auth, mock_archive_enabled, mock_no_bangumi_config
    ):
        """正常查询返回按日期分组的结果"""
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
        # 第一天有 1 集
        assert data["days"][0]["date"] == today_str
        assert len(data["days"][0]["episodes"]) == 1
        assert data["days"][0]["episodes"][0]["subject_name"] == "Anime A"
        # 第二天有 1 集
        assert data["days"][1]["date"] == tomorrow_str
        assert len(data["days"][1]["episodes"]) == 1

    @pytest.mark.asyncio
    async def test_only_watching_no_config_degrades(
        self, app_with_auth, mock_archive_enabled
    ):
        """only_watching=True 但无 Bangumi 配置时降级为全部放送"""
        with (
            patch.object(
                airing_calendar.config_manager,
                "get_active_bangumi_config",
                return_value=None,
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
                resp = await client.get(
                    "/api/airing-calendar", params={"only_watching": True}
                )

        assert resp.status_code == 200
        data = resp.json()
        # 降级为全部放送
        assert data["only_watching"] is False
        # subject_ids 应为 None（不过滤）
        call_kwargs = mock_query.call_args.kwargs
        assert call_kwargs["subject_ids"] is None

    @pytest.mark.asyncio
    async def test_only_watching_with_config(self, app_with_auth, mock_archive_enabled):
        """only_watching=True 有配置时调用 get_watching_subject_ids 过滤"""
        mock_cfg = {
            "username": "testuser",
            "access_token": "token",
            "private": False,
        }
        with (
            patch.object(
                airing_calendar.config_manager,
                "get_active_bangumi_config",
                return_value=mock_cfg,
            ),
            patch.object(
                airing_calendar.config_manager, "get_dev_http_snapshot"
            ) as mock_snap,
            patch(
                "app.api.airing_calendar.get_watching_subject_ids",
                return_value={1, 2},
            ) as mock_watching,
            patch.object(
                airing_calendar.archive_store,
                "get_episodes_by_airdate",
                return_value=[],
            ) as mock_query,
            patch("app.api.airing_calendar.BangumiApi"),
        ):
            mock_snap.return_value = {
                "script_proxy": None,
                "ssl_verify": True,
                "bgm_api_proxy": None,
                "bgm_next_proxy": None,
            }
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/airing-calendar", params={"only_watching": True}
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["only_watching"] is True
        mock_watching.assert_called_once()
        # subject_ids 应为 {1, 2}
        call_kwargs = mock_query.call_args.kwargs
        assert call_kwargs["subject_ids"] == {1, 2}

    @pytest.mark.asyncio
    async def test_days_normalized(
        self, app_with_auth, mock_archive_enabled, mock_no_bangumi_config
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
        self, app_with_auth, mock_archive_enabled, mock_no_bangumi_config
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
