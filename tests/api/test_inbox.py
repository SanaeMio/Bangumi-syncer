"""控制台收件箱 API 测试。"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, inbox
from app.utils.announcements_fetcher import AnnouncementsFetchResult


@pytest.fixture
def app_with_auth():
    app = FastAPI()
    app.include_router(inbox.router)

    async def mock_user():
        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def mock_announcements():
    return AnnouncementsFetchResult(
        ok=True,
        announcements=[
            {
                "id": "ann-1",
                "title": "公告一",
                "level": "important",
                "published_at": "2026-06-08T12:00:00+08:00",
                "body": "## 标题\n内容",
            }
        ],
        remote_loaded=True,
    )


@pytest.mark.asyncio
async def test_inbox_summary_skips_markdown_render(
    app_with_auth, mock_announcements, temp_dir, reset_singletons
):
    from app.core.database import DatabaseManager

    db_path = temp_dir / "inbox_summary_light.db"
    db = DatabaseManager(str(db_path))

    with (
        patch(
            "app.api.inbox.fetch_announcements",
            AsyncMock(return_value=mock_announcements),
        ),
        patch("app.api.inbox.database_manager", db),
        patch("app.api.inbox.markdown_to_safe_html") as mock_md,
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/inbox/summary")

    assert r.status_code == 200
    assert r.json()["data"]["announcements"] == 1
    mock_md.assert_not_called()


@pytest.mark.asyncio
async def test_inbox_summary(
    app_with_auth, mock_announcements, temp_dir, reset_singletons
):
    from app.core.database import DatabaseManager

    db_path = temp_dir / "inbox.db"
    db = DatabaseManager(str(db_path))
    db.log_sync_record(
        user_name="u",
        title="番剧",
        ori_title=None,
        season=1,
        episode=1,
        status="error",
        message="失败原因",
        source="test",
    )

    with (
        patch(
            "app.api.inbox.fetch_announcements",
            AsyncMock(return_value=mock_announcements),
        ),
        patch("app.api.inbox.database_manager", db),
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/inbox/summary")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["announcements"] == 1
    assert data["notifications"] == 1
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_inbox_list_and_read_all(
    app_with_auth, mock_announcements, temp_dir, reset_singletons
):
    from app.core.database import DatabaseManager

    db_path = temp_dir / "inbox2.db"
    db = DatabaseManager(str(db_path))
    db.log_sync_record(
        user_name="u",
        title="番剧",
        ori_title=None,
        season=1,
        episode=2,
        status="error",
        message="err",
        source="test",
    )

    with (
        patch(
            "app.api.inbox.fetch_announcements",
            AsyncMock(return_value=mock_announcements),
        ),
        patch("app.api.inbox.database_manager", db),
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r_list = await ac.get("/api/inbox?category=all")
            assert r_list.status_code == 200
            body = r_list.json()["data"]
            assert len(body["announcements"]) == 1
            assert not body["announcements"][0]["body_html"]
            assert len(body["notifications"]) == 1

            r_detail = await ac.get("/api/inbox/announcements/ann-1")
            assert r_detail.status_code == 200
            assert r_detail.json()["data"]["body_html"]

            r_read = await ac.post("/api/inbox/read-all", json={"category": "all"})
            assert r_read.status_code == 200

            r_sum = await ac.get("/api/inbox/summary")
            assert r_sum.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_mark_single_read(
    app_with_auth, mock_announcements, temp_dir, reset_singletons
):
    from app.core.database import DatabaseManager

    db_path = temp_dir / "inbox3.db"
    db = DatabaseManager(str(db_path))
    record_id = db.log_sync_record(
        user_name="u",
        title="番剧",
        ori_title=None,
        season=1,
        episode=3,
        status="error",
        message="x",
        source="test",
    )
    notifs = db.list_in_app_notifications()
    notif_id = notifs[0]["id"]

    with (
        patch(
            "app.api.inbox.fetch_announcements",
            AsyncMock(return_value=mock_announcements),
        ),
        patch("app.api.inbox.database_manager", db),
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.post(f"/api/inbox/notifications/{notif_id}/read")
            assert r1.status_code == 200

            r2 = await ac.post("/api/inbox/announcements/ann-1/read")
            assert r2.status_code == 200

    assert db.count_unread_notifications() == 0
    assert "ann-1" in db.get_read_announcement_ids()
    assert record_id == 1


@pytest.mark.asyncio
async def test_inbox_remote_error_hint(app_with_auth, temp_dir, reset_singletons):
    from app.core.database import DatabaseManager

    db_path = temp_dir / "inbox_remote_err.db"
    db = DatabaseManager(str(db_path))
    failed = AnnouncementsFetchResult(
        ok=False,
        announcements=[],
        remote_loaded=False,
        error="连接超时",
    )

    with (
        patch(
            "app.api.inbox.fetch_announcements",
            AsyncMock(return_value=failed),
        ),
        patch("app.api.inbox.database_manager", db),
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/inbox?category=announcement")

    assert r.status_code == 200
    assert r.json()["data"]["remote_error"] == "连接超时"


@pytest.mark.asyncio
async def test_inbox_notification_aggregation(
    app_with_auth, mock_announcements, temp_dir, reset_singletons
):
    from app.core.database import DatabaseManager

    db_path = temp_dir / "inbox_agg.db"
    db = DatabaseManager(str(db_path))
    for ep in (1, 2):
        db.log_sync_record(
            user_name="u",
            title="同一番剧",
            ori_title=None,
            season=1,
            episode=ep,
            status="error",
            message=f"err{ep}",
            source="test",
        )

    with (
        patch(
            "app.api.inbox.fetch_announcements",
            AsyncMock(return_value=mock_announcements),
        ),
        patch("app.api.inbox.database_manager", db),
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r_sum = await ac.get("/api/inbox/summary")
            r_list = await ac.get("/api/inbox?category=notification")

    assert r_sum.json()["data"]["notifications"] == 1
    notifs = r_list.json()["data"]["notifications"]
    assert len(notifs) == 1
    assert notifs[0]["count"] == 2
    assert len(notifs[0]["notification_ids"]) == 2
    assert "2 条" in notifs[0]["title"]


@pytest.mark.asyncio
async def test_inbox_read_all_by_category(
    app_with_auth, mock_announcements, temp_dir, reset_singletons
):
    from app.core.database import DatabaseManager

    db_path = temp_dir / "inbox_cat_read.db"
    db = DatabaseManager(str(db_path))
    db.log_sync_record(
        user_name="u",
        title="番剧",
        ori_title=None,
        season=1,
        episode=1,
        status="error",
        message="e",
        source="test",
    )

    with (
        patch(
            "app.api.inbox.fetch_announcements",
            AsyncMock(return_value=mock_announcements),
        ),
        patch("app.api.inbox.database_manager", db),
    ):
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/api/inbox/read-all", json={"category": "announcement"})
            assert r.status_code == 200

            r_sum = await ac.get("/api/inbox/summary")
            data = r_sum.json()["data"]
            assert data["announcements"] == 0
            assert data["notifications"] == 1
