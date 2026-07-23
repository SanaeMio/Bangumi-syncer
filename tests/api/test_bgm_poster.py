"""Bangumi 仪表板封面 API 测试。"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api.bgm_poster import router as bgm_poster_router


@pytest.fixture
def app_bgm_poster():
    app = FastAPI()
    app.include_router(bgm_poster_router)

    async def mock_user():
        return {"username": "admin"}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_user
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_subject_poster_success(app_bgm_poster):
    with patch(
        "app.api.bgm_poster.get_poster_urls",
        new_callable=AsyncMock,
        return_value={123: "https://img-proxy.example.com/pic/cover/s/a/b/c.jpg"},
    ) as mock_get:
        transport = ASGITransport(app=app_bgm_poster)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/bgm/subjects/123/poster")

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["url"] == "https://img-proxy.example.com/pic/cover/s/a/b/c.jpg"
    mock_get.assert_awaited_once_with([123])


@pytest.mark.asyncio
async def test_get_subject_poster_no_image(app_bgm_poster):
    with patch(
        "app.api.bgm_poster.get_poster_urls",
        new_callable=AsyncMock,
        return_value={},
    ):
        transport = ASGITransport(app=app_bgm_poster)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/bgm/subjects/123/poster")

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_subject_poster_service_error(app_bgm_poster):
    with patch(
        "app.api.bgm_poster.get_poster_urls",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        transport = ASGITransport(app=app_bgm_poster)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/bgm/subjects/123/poster")

    assert r.status_code == 502


@pytest.mark.asyncio
async def test_get_subject_poster_invalid_id(app_bgm_poster):
    transport = ASGITransport(app=app_bgm_poster)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/bgm/subjects/0/poster")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_subject_poster_requires_auth():
    app = FastAPI()
    app.include_router(bgm_poster_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/bgm/subjects/123/poster")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_subjects_posters_batch_success(app_bgm_poster):
    with patch(
        "app.api.bgm_poster.get_poster_urls",
        new_callable=AsyncMock,
        return_value={
            123: "https://img-proxy.example.com/pic/cover/s/a/b/c.jpg",
            456: "https://img-proxy.example.com/pic/cover/s/d/e/f.jpg",
        },
    ) as mock_get:
        transport = ASGITransport(app=app_bgm_poster)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get(
                "/api/bgm/subjects/posters",
                params={"subject_ids": [123, 456, 123, 0, -1]},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["posters"] == {
        "123": "https://img-proxy.example.com/pic/cover/s/a/b/c.jpg",
        "456": "https://img-proxy.example.com/pic/cover/s/d/e/f.jpg",
    }
    mock_get.assert_awaited_once_with([123, 456])


@pytest.mark.asyncio
async def test_get_subjects_posters_empty_list(app_bgm_poster):
    with patch(
        "app.api.bgm_poster.get_poster_urls",
        new_callable=AsyncMock,
    ) as mock_get:
        transport = ASGITransport(app=app_bgm_poster)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/bgm/subjects/posters")

    assert r.status_code == 200
    assert r.json() == {"status": "success", "posters": {}}
    mock_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_subjects_posters_service_error(app_bgm_poster):
    with patch(
        "app.api.bgm_poster.get_poster_urls",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        transport = ASGITransport(app=app_bgm_poster)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/bgm/subjects/posters", params={"subject_ids": [1]})

    assert r.status_code == 502


@pytest.mark.asyncio
async def test_get_subjects_posters_requires_auth():
    app = FastAPI()
    app.include_router(bgm_poster_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/bgm/subjects/posters", params={"subject_ids": [1]})
    assert r.status_code == 401
