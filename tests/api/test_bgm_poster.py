"""Bangumi 仪表板封面 API 测试。"""

from unittest.mock import MagicMock, patch

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
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {
        "id": 123,
        "images": {"large": "https://lain.bgm.tv/pic/cover/l/a/b/c.jpg"},
    }

    def fake_get(section, key, fallback=""):
        if section == "dev" and key == "bgm_image_proxy":
            return "https://img-proxy.example.com"
        return fallback

    with patch("app.api.bgm_poster._get_public_bangumi_api", return_value=mock_bgm):
        with patch("app.api.bgm_poster.config_manager.get", side_effect=fake_get):
            transport = ASGITransport(app=app_bgm_poster)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.get("/api/bgm/subjects/123/poster")

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["url"] == "https://img-proxy.example.com/pic/cover/l/a/b/c.jpg"
    mock_bgm.get_subject.assert_called_once_with(123)


@pytest.mark.asyncio
async def test_get_subject_poster_no_image(app_bgm_poster):
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {"id": 123, "images": {}}

    with patch("app.api.bgm_poster._get_public_bangumi_api", return_value=mock_bgm):
        transport = ASGITransport(app=app_bgm_poster)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/bgm/subjects/123/poster")

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_subject_poster_not_found(app_bgm_poster):
    mock_bgm = MagicMock()
    mock_bgm.get_subject.return_value = {}

    with patch("app.api.bgm_poster._get_public_bangumi_api", return_value=mock_bgm):
        transport = ASGITransport(app=app_bgm_poster)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/bgm/subjects/123/poster")

    assert r.status_code == 404


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
