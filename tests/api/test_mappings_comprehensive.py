"""
映射 API 测试
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, mappings


@pytest.fixture
def app_with_auth():
    """创建带有认证的测试应用"""
    app = FastAPI()
    app.include_router(mappings.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_custom_mappings(app_with_auth):
    """测试获取自定义映射"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.return_value = {"test": "value"}

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/mappings")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_get_custom_mappings_exception(app_with_auth):
    """测试获取自定义映射异常"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.side_effect = Exception("Error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/mappings")

            assert response.status_code == 500


@pytest.mark.asyncio
async def test_update_custom_mappings(app_with_auth):
    """测试更新自定义映射"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"test": "value"}}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_update_custom_mappings_exception(app_with_auth):
    """测试更新自定义映射异常"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.update_mappings.side_effect = Exception("Error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"test": "value"}}
            )

            assert response.status_code == 500


@pytest.mark.asyncio
async def test_delete_custom_mapping(app_with_auth):
    """测试删除自定义映射"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.return_value = {"test": "value"}

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.delete("/api/mappings/test")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_delete_custom_mapping_not_found(app_with_auth):
    """测试删除不存在的映射"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.return_value = {}

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.delete("/api/mappings/nonexistent")

            assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_custom_mapping_exception(app_with_auth):
    """测试删除映射异常"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.return_value = {"test": "value"}
        mock_service.update_mappings.side_effect = Exception("Error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.delete("/api/mappings/test")

            assert response.status_code == 500
