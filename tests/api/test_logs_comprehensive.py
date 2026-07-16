"""
日志 API 完整测试
"""

from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, logs


@pytest.fixture
def app_with_auth():
    """创建带有认证的测试应用"""
    app = FastAPI()
    app.include_router(logs.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app

    app.dependency_overrides.clear()


class TestLogsAPIComprehensive:
    """日志 API 综合测试"""

    def test_logs_router_prefix(self):
        """测试日志路由器前缀"""
        assert logs.router.prefix == "/api"

    def test_logs_router_routes(self):
        """测试日志路由有路由"""
        assert len(logs.router.routes) > 0


@pytest.mark.asyncio
async def test_get_logs_when_file_log_disabled(app_with_auth):
    """[dev] log_file 留空禁用时 API 返回空内容"""
    with patch("app.api.logs.resolved_dev_log_file_path", return_value=None):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/logs")
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["content"] == ""
    assert data["data"]["stats"]["size"] == 0


@pytest.mark.asyncio
async def test_get_logs_file_not_found(app_with_auth):
    """测试获取日志文件不存在"""
    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/nonexistent/log.txt"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=False):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth), base_url="http://test"
            ) as client:
                response = await client.get("/api/logs")

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert data["data"]["content"] == ""
                assert data["data"]["stats"]["size"] == 0


@pytest.mark.asyncio
async def test_get_logs_with_content(app_with_auth):
    """测试获取日志有内容"""
    log_content = """2024-01-01 10:00:00 INFO Starting application
2024-01-01 10:00:01 ERROR Database connection failed
2024-01-01 10:00:02 WARNING Retry attempt 1
"""

    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("app.api.logs.os.stat") as mock_stat:
                mock_stat.return_value = MagicMock(
                    st_size=len(log_content), st_mtime=1234567890.0
                )

                with patch("builtins.open", mock_open(read_data=log_content)):
                    async with AsyncClient(
                        transport=ASGITransport(app=app_with_auth),
                        base_url="http://test",
                    ) as client:
                        response = await client.get("/api/logs")

                        assert response.status_code == 200
                        data = response.json()
                        assert data["status"] == "success"
                        assert "content" in data["data"]
                        assert data["data"]["stats"]["errors"] == 1  # 1 ERROR line


@pytest.mark.asyncio
async def test_get_logs_with_level_filter(app_with_auth):
    """测试按级别筛选日志"""
    log_content = """2024-01-01 10:00:00 INFO Starting application
2024-01-01 10:00:01 ERROR Database connection failed
2024-01-01 10:00:02 WARNING Retry attempt 1
"""

    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("app.api.logs.os.stat") as mock_stat:
                mock_stat.return_value = MagicMock(st_size=100, st_mtime=1234567890.0)

                with patch("builtins.open", mock_open(read_data=log_content)):
                    async with AsyncClient(
                        transport=ASGITransport(app=app_with_auth),
                        base_url="http://test",
                    ) as client:
                        response = await client.get("/api/logs?level=ERROR")

                        assert response.status_code == 200
                        data = response.json()
                        assert "ERROR" in data["data"]["content"]


@pytest.mark.asyncio
async def test_get_logs_with_search_filter(app_with_auth):
    """测试按关键词搜索日志"""
    log_content = """2024-01-01 10:00:00 INFO Starting application
2024-01-01 10:00:01 ERROR Database connection failed
2024-01-01 10:00:02 WARNING Retry attempt 1
"""

    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("app.api.logs.os.stat") as mock_stat:
                mock_stat.return_value = MagicMock(st_size=100, st_mtime=1234567890.0)

                with patch("builtins.open", mock_open(read_data=log_content)):
                    async with AsyncClient(
                        transport=ASGITransport(app=app_with_auth),
                        base_url="http://test",
                    ) as client:
                        response = await client.get("/api/logs?search=Database")

                        assert response.status_code == 200
                        data = response.json()
                        assert "Database" in data["data"]["content"]


@pytest.mark.asyncio
async def test_get_logs_with_limit(app_with_auth):
    """测试限制日志行数"""
    log_content = """Line 1
Line 2
Line 3
Line 4
Line 5
"""

    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("app.api.logs.os.stat") as mock_stat:
                mock_stat.return_value = MagicMock(st_size=100, st_mtime=1234567890.0)

                with patch("builtins.open", mock_open(read_data=log_content)):
                    async with AsyncClient(
                        transport=ASGITransport(app=app_with_auth),
                        base_url="http://test",
                    ) as client:
                        response = await client.get("/api/logs?limit=2")

                        assert response.status_code == 200
                        data = response.json()
                        # 限制后应该只返回最后2行
                        lines = data["data"]["content"].split("\n")
                        assert len([line for line in lines if line]) <= 2


@pytest.mark.asyncio
async def test_get_logs_with_limit_all(app_with_auth):
    """测试获取所有日志"""
    log_content = """Line 1
Line 2
Line 3
"""

    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("app.api.logs.os.stat") as mock_stat:
                mock_stat.return_value = MagicMock(st_size=100, st_mtime=1234567890.0)

                with patch("builtins.open", mock_open(read_data=log_content)):
                    async with AsyncClient(
                        transport=ASGITransport(app=app_with_auth),
                        base_url="http://test",
                    ) as client:
                        response = await client.get("/api/logs?limit=all")

                        assert response.status_code == 200
                        data = response.json()
                        assert "Line" in data["data"]["content"]


@pytest.mark.asyncio
async def test_get_logs_invalid_limit(app_with_auth):
    """测试无效的limit参数"""
    log_content = """Line 1
Line 2
"""

    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("app.api.logs.os.stat") as mock_stat:
                mock_stat.return_value = MagicMock(st_size=100, st_mtime=1234567890.0)

                with patch("builtins.open", mock_open(read_data=log_content)):
                    async with AsyncClient(
                        transport=ASGITransport(app=app_with_auth),
                        base_url="http://test",
                    ) as client:
                        response = await client.get("/api/logs?limit=invalid")

                        # 无效的limit应该被忽略，返回全部
                        assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_logs_exception(app_with_auth):
    """测试获取日志异常"""
    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", side_effect=Exception("Test error")):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth), base_url="http://test"
            ) as client:
                response = await client.get("/api/logs")

                assert response.status_code == 500


@pytest.mark.asyncio
async def test_clear_logs_success(app_with_auth):
    """测试清空日志成功"""
    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("builtins.open", mock_open()) as _mock_file:
                async with AsyncClient(
                    transport=ASGITransport(app=app_with_auth), base_url="http://test"
                ) as client:
                    response = await client.post("/api/logs/clear")

                    assert response.status_code == 200
                    data = response.json()
                    assert data["status"] == "success"
                    assert "日志清空成功" in data["message"]


@pytest.mark.asyncio
async def test_clear_logs_file_not_exists(app_with_auth):
    """测试日志文件不存在时清空"""
    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/missing.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=False):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth), base_url="http://test"
            ) as client:
                response = await client.post("/api/logs/clear")

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"


@pytest.mark.asyncio
async def test_clear_logs_exception(app_with_auth):
    """测试清空日志异常"""
    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", side_effect=Exception("Test error")):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth), base_url="http://test"
            ) as client:
                response = await client.post("/api/logs/clear")

                assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_logs_grouped_response(app_with_auth):
    """测试 grouped=true 返回结构化分组数据"""
    log_content = (
        "[2026/07/16 09:00:01.123] [INFO] [run:sync_1_100] 同步开始: 测试番剧 S01E01 (test)\n"
        "[2026/07/16 09:00:02.456] [INFO] [run:sync_1_100] bgm: 测试番剧 已标记为看过\n"
        "[2026/07/16 09:00:03.789] [INFO] [run:sync_1_100] 同步结束: status=success\n"
        "[2026/07/16 09:00:04.000] [INFO] 用户 admin 登录成功\n"
    )

    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("app.api.logs.os.stat") as mock_stat:
                mock_stat.return_value = MagicMock(
                    st_size=len(log_content), st_mtime=1234567890.0
                )
                with patch("builtins.open", mock_open(read_data=log_content)):
                    with patch("app.api.logs.config_manager") as mock_cm:
                        mock_cm.get.return_value = True
                        async with AsyncClient(
                            transport=ASGITransport(app=app_with_auth),
                            base_url="http://test",
                        ) as client:
                            response = await client.get("/api/logs?grouped=true")

                            assert response.status_code == 200
                            data = response.json()
                            assert data["status"] == "success"
                            assert "content" not in data["data"]
                            assert "groups" in data["data"]
                            assert "orphans" in data["data"]
                            assert "debug_mode" in data["data"]
                            assert len(data["data"]["groups"]) == 1
                            assert data["data"]["groups"][0]["run_id"] == "sync_1_100"
                            assert data["data"]["groups"][0]["status"] == "success"
                            assert len(data["data"]["orphans"]) == 1


@pytest.mark.asyncio
async def test_get_logs_grouped_disabled(app_with_auth):
    """测试 grouped=false 仍返回扁平 content"""
    log_content = "Line 1\nLine 2\n"

    with patch(
        "app.api.logs.resolved_dev_log_file_path",
        return_value=Path("/fake/app.log"),
    ):
        with patch("app.api.logs.os.path.exists", return_value=True):
            with patch("app.api.logs.os.stat") as mock_stat:
                mock_stat.return_value = MagicMock(st_size=100, st_mtime=1234567890.0)
                with patch("builtins.open", mock_open(read_data=log_content)):
                    async with AsyncClient(
                        transport=ASGITransport(app=app_with_auth),
                        base_url="http://test",
                    ) as client:
                        response = await client.get("/api/logs?grouped=false")

                        assert response.status_code == 200
                        data = response.json()
                        assert "content" in data["data"]
                        assert "groups" not in data["data"]
