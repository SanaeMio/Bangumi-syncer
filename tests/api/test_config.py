"""
配置API测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api import config, deps


@pytest.fixture
def app_with_auth():
    """创建带有认证禁用的测试应用"""
    app = FastAPI()
    app.include_router(config.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def mock_config_manager():
    """模拟配置管理器"""
    with patch("app.api.config.config_manager") as mock_cm:
        mock_cm.get_all_config.return_value = {
            "auth": {"username": "testuser", "password": ""},
            "bangumi": {"username": "bgmuser"},
            "sync": {"enabled": True},
        }
        mock_cm.get_config_parser.return_value = MagicMock()
        mock_cm.active_config_path = "/tmp/test_config.ini"
        mock_cm.save_config.return_value = None
        mock_cm.reload_config.return_value = None

        yield mock_cm


@pytest.fixture
def mock_security_manager():
    """模拟安全管理器"""
    with patch("app.api.config.security_manager") as mock_sm:
        mock_sm.get_auth_config.return_value = {"secret_key": "test_secret_key"}
        mock_sm.hash_password.return_value = (
            "hashed_password_123456789012345678901234567890123456789012345678901234"
        )
        mock_sm._init_auth_config.return_value = None

        yield mock_sm


@pytest.fixture
def backup_dir(tmp_path):
    """创建备份目录"""
    backup_path = tmp_path / "config_backups"
    backup_path.mkdir()
    return backup_path


# ========== 基础功能测试 ==========


@pytest.mark.asyncio
async def test_get_config(app_with_auth, mock_config_manager):
    """测试获取配置"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


@pytest.mark.asyncio
async def test_get_config_with_encrypted_password(app_with_auth, mock_config_manager):
    """测试获取配置时隐藏加密密码"""
    mock_config_manager.get_all_config.return_value = {
        "auth": {
            "username": "testuser",
            "password": "a" * 64,
        }
    }

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["auth"]["password"] == ""


@pytest.mark.asyncio
async def test_get_config_schema(app_with_auth):
    """/api/config/schema 返回 SectionMeta 序列化结构"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/config/schema")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    schema = data["data"]
    # 顶层结构
    assert "sections" in schema
    assert "config_defaults" in schema
    assert "default_true_fields" in schema
    assert "loose_true_fields" in schema
    # 段按 order 排序
    orders = [s["order"] for s in schema["sections"]]
    assert orders == sorted(orders)
    # 段同时提供连字符 name 与下划线 name_key
    for s in schema["sections"]:
        assert "name" in s
        assert "name_key" in s
        assert "-" not in s["name_key"]
    # 默认值示例
    assert schema["config_defaults"]["llm"]["max_tokens"] == 2000
    # default_true / loose_true 字段使用下划线路径
    assert "bangumi_data.enabled" in schema["default_true_fields"]
    assert "feiniu.enabled" in schema["loose_true_fields"]


@pytest.mark.asyncio
async def test_get_scheduler_status(app_with_auth):
    """/api/scheduler/status 返回调度器状态列表"""
    mock_status_list = [
        {
            "scheduler_id": "feiniu",
            "display_name": "飞牛影视",
            "jobs": [
                {
                    "job_id": "feiniu_sync",
                    "name": "飞牛同步",
                    "next_run_time": 1700000000.0,
                    "trigger": "*/15 * * * *",
                }
            ],
        },
        {
            "scheduler_id": "trakt",
            "display_name": "Trakt 同步",
            "jobs": [],
        },
    ]
    with patch(
        "app.core.scheduler_registry.scheduler_registry.get_status_list",
        return_value=mock_status_list,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/scheduler/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 2
    assert data["data"][0]["scheduler_id"] == "feiniu"
    assert data["data"][0]["display_name"] == "飞牛影视"
    assert len(data["data"][0]["jobs"]) == 1
    assert data["data"][0]["jobs"][0]["trigger"] == "*/15 * * * *"
    assert data["data"][1]["scheduler_id"] == "trakt"
    assert data["data"][1]["jobs"] == []


@pytest.mark.asyncio
async def test_get_scheduler_status_empty(app_with_auth):
    """/api/scheduler/status 无调度器注册时返回空列表"""
    with patch(
        "app.core.scheduler_registry.scheduler_registry.get_status_list",
        return_value=[],
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/scheduler/status")
    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_update_config(app_with_auth, mock_config_manager, mock_security_manager):
    """测试更新配置"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/config",
            json={"auth": {"username": "newuser", "password": "newpass123"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


@pytest.mark.asyncio
async def test_update_config_with_empty_password(app_with_auth, mock_config_manager):
    """测试更新配置时空密码不更新"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/config",
            json={"auth": {"username": "newuser", "password": ""}},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_config_ignores_bangumi_section(
    app_with_auth, mock_config_manager, mock_security_manager
):
    """POST /api/config 忽略遗留 [bangumi] 段回写（账号已迁移到 DB）。"""
    mock_config_manager.get_feiniu_config.return_value = {"enabled": False}

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        with patch(
            "app.services.feiniu.scheduler.feiniu_scheduler.apply_config_after_save",
            new_callable=AsyncMock,
        ):
            response = await client.post(
                "/api/config",
                json={
                    "bangumi": {
                        "username": "bgm_u",
                        "access_token": "tok",
                        "private": False,
                        "media_server_username": "plex_a,jelly_b",
                    }
                },
            )

    assert response.status_code == 200
    # bangumi 段已由 /api/bangumi/accounts 管理，set_config 不应以 "bangumi" 为 section
    for call in mock_config_manager.set_config.call_args_list:
        args, _ = call
        assert args[0] != "bangumi"


# ========== 异常路径测试 ==========


@pytest.mark.asyncio
async def test_get_config_exception(app_with_auth, mock_config_manager):
    """测试获取配置时抛出异常"""
    mock_config_manager.get_all_config.side_effect = RuntimeError("read error")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/config")
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_update_config_exception(app_with_auth, mock_config_manager):
    """测试更新配置时抛出异常"""
    mock_config_manager.save_config.side_effect = RuntimeError("save error")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/config",
            json={"auth": {"username": "user"}},
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_update_config_already_encrypted_password(
    app_with_auth, mock_config_manager, mock_security_manager
):
    """测试更新已加密的密码（长度>=64）直接保存"""
    mock_config_manager.get_feiniu_config.return_value = {"enabled": False}

    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        with patch(
            "app.services.feiniu.scheduler.feiniu_scheduler.apply_config_after_save",
            new_callable=AsyncMock,
        ):
            response = await client.post(
                "/api/config",
                json={"auth": {"password": "a" * 64}},
            )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_config_sensitive_field_skipped(
    app_with_auth, mock_config_manager
):
    """测试空敏感字段跳过更新"""
    with patch("app.api.config.is_sensitive_ini_field", return_value=True):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config",
                json={"trakt": {"client_secret": ""}},
            )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_config_feiniu_enabled_toggle(
    app_with_auth, mock_config_manager, mock_database_manager
):
    """测试飞牛启用时设置水位"""
    mock_config_manager.get_feiniu_config.side_effect = [
        {"enabled": False},  # old
        {"enabled": True},  # new
    ]

    with (
        patch("app.services.feiniu.sync_service.database_manager") as mock_db,
        patch(
            "app.services.feiniu.scheduler.feiniu_scheduler.apply_config_after_save",
            new_callable=AsyncMock,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config",
                json={"feiniu": {"enabled": "True"}},
            )
    assert response.status_code == 200
    mock_db.set_feiniu_min_update_watermark_now.assert_called_once()


@pytest.mark.asyncio
async def test_update_config_feiniu_disabled_toggle(app_with_auth, mock_config_manager):
    """测试飞牛关闭时清除水位"""
    mock_config_manager.get_feiniu_config.side_effect = [
        {"enabled": True},  # old
        {"enabled": False},  # new
    ]

    with (
        patch("app.services.feiniu.sync_service.database_manager") as mock_db,
        patch(
            "app.services.feiniu.scheduler.feiniu_scheduler.apply_config_after_save",
            new_callable=AsyncMock,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config",
                json={"feiniu": {"enabled": "False"}},
            )
    assert response.status_code == 200
    mock_db.clear_feiniu_min_update_watermark.assert_called_once()


@pytest.mark.asyncio
async def test_update_config_feiniu_scheduler_exception(
    app_with_auth, mock_config_manager
):
    """测试飞牛调度器异常被捕获"""
    mock_config_manager.get_feiniu_config.return_value = {"enabled": False}

    with patch(
        "app.services.feiniu.scheduler.feiniu_scheduler.apply_config_after_save",
        new_callable=AsyncMock,
        side_effect=RuntimeError("scheduler error"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config",
                json={"sync": {"enabled": True}},
            )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_config_password_update_reloads_auth(
    app_with_auth, mock_config_manager, mock_security_manager
):
    """测试密码更新后重新加载认证配置"""
    mock_config_manager.get_feiniu_config.return_value = {"enabled": False}

    with patch(
        "app.services.feiniu.scheduler.feiniu_scheduler.apply_config_after_save",
        new_callable=AsyncMock,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config",
                json={"auth": {"password": "newpass123"}},
            )
    assert response.status_code == 200
    mock_security_manager._init_auth_config.assert_called()


# ========== 备份路径安全测试 ==========


@pytest.fixture
def backup_layout(tmp_path, monkeypatch):
    """在隔离目录下创建 config_backups 布局"""
    monkeypatch.chdir(tmp_path)
    backup_dir = tmp_path / "config_backups"
    backup_dir.mkdir()
    return backup_dir


def test_safe_backup_path_valid_names(backup_layout):
    """合法备份文件名应解析到 config_backups 内"""
    for name in ("config_backup_20260101_000000.ini", "test_backup.ini"):
        (backup_layout / name).write_text("content", encoding="utf-8")
        path = config._safe_backup_path(name)
        assert path.is_file()
        assert path.name == name


def test_safe_backup_path_rejects_invalid_names(backup_layout):
    """危险或非法文件名应统一返回 404"""
    invalid_names = [
        "..\\secret.ini",
        "../secret.ini",
        "C:\\Windows\\win.ini",
        "..ini",
        "",
        "no_ini_suffix",
        "bad/name.ini",
    ]
    for name in invalid_names:
        with pytest.raises(HTTPException) as exc_info:
            config._safe_backup_path(name)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "备份文件不存在"


def test_safe_backup_path_rejects_symlink_escape(backup_layout, tmp_path):
    """符号链接指向备份目录外时应被拒绝"""
    secret = tmp_path / "secret.ini"
    secret.write_text("SECRET", encoding="utf-8")
    (backup_layout / "escape.ini").symlink_to(secret)
    with pytest.raises(HTTPException) as exc_info:
        config._safe_backup_path("escape.ini")
    assert exc_info.value.status_code == 404
    assert secret.is_file()


def test_safe_backup_path_traversal_does_not_touch_outside_file(
    backup_layout, tmp_path
):
    """穿越文件名不应读取或影响目录外文件"""
    secret = tmp_path / "secret.ini"
    secret.write_text("SECRET", encoding="utf-8")
    with pytest.raises(HTTPException):
        config._safe_backup_path("..\\secret.ini")
    assert secret.is_file()
    assert secret.read_text(encoding="utf-8") == "SECRET"


# ========== 备份功能测试 ==========


@pytest.mark.asyncio
async def test_get_config_backups_empty(backup_dir):
    """测试获取空备份列表"""
    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/config/backups")
            assert response.status_code == 200
            data = response.json()
            assert data["data"]["backups"] == []

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_config_backups_no_dir():
    """测试备份目录不存在时返回空列表"""
    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path_obj = MagicMock()
        mock_path_obj.exists.return_value = False
        mock_path.return_value = mock_path_obj

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/config/backups")
            assert response.status_code == 200
            data = response.json()
            assert data["data"]["backups"] == []

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_config_backups_exception(app_with_auth, mock_config_manager):
    """测试获取备份列表时抛出异常"""
    with patch("app.api.config.Path") as mock_path:
        mock_path.side_effect = RuntimeError("path error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/config/backups")
            assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_config_backups(backup_dir):
    """测试获取备份列表"""
    backup_file = backup_dir / "test_backup.ini"
    backup_file.write_text("[auth]\nusername=test")

    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/config/backups")
            assert response.status_code == 200
            data = response.json()
            assert len(data["data"]["backups"]) > 0

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_config_backup(backup_dir):
    """测试获取配置备份内容"""
    backup_file = backup_dir / "test_backup.ini"
    backup_file.write_text("[auth]\nusername=test")

    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/config/backup/test_backup.ini")
            assert response.status_code == 200
            data = response.json()
            assert data["data"]["filename"] == "test_backup.ini"
            assert "username=test" in data["data"]["content"]

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_config_backup_not_found(backup_dir):
    """测试获取不存在的备份"""
    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/config/backup/nonexistent.ini")
            assert response.status_code == 404

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_config_backup_read_exception(backup_dir):
    """测试读取备份文件时抛出异常"""
    backup_file = backup_dir / "bad_backup.ini"
    backup_file.write_text("content")

    app = FastAPI()
    app.include_router(config.router)

    with (
        patch("app.api.config.Path") as mock_path,
        patch("builtins.open", side_effect=OSError("permission denied")),
    ):
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/config/backup/bad_backup.ini")
            assert response.status_code == 500

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delete_config_backup(backup_dir):
    """测试删除配置备份"""
    backup_file = backup_dir / "test_backup.ini"
    backup_file.write_text("[auth]\nusername=test")

    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete("/api/config/backup/test_backup.ini")
            assert response.status_code == 200

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delete_config_backup_not_found(backup_dir):
    """测试删除不存在的备份"""
    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete("/api/config/backup/nonexistent.ini")
            assert response.status_code == 404

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delete_config_backup_unlink_exception(backup_dir):
    """测试删除备份文件时抛出异常"""
    backup_file = backup_dir / "locked.ini"
    backup_file.write_text("content")

    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        with patch("pathlib.Path.unlink", side_effect=OSError("locked")):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete("/api/config/backup/locked.ini")
                assert response.status_code == 500

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_config_backup(app_with_auth, mock_config_manager, backup_dir):
    """测试创建配置备份"""
    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir
        with patch("shutil.copy2"):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth), base_url="http://test"
            ) as client:
                response = await client.post("/api/config/backup")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert "filename" in data["data"]


@pytest.mark.asyncio
async def test_create_config_backup_exception(
    app_with_auth, mock_config_manager, backup_dir
):
    """测试创建备份时抛出异常"""
    with (
        patch("app.api.config.Path") as mock_path,
        patch("shutil.copy2", side_effect=OSError("disk full")),
    ):
        mock_path.return_value = backup_dir
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post("/api/config/backup")
            assert response.status_code == 500


@pytest.mark.asyncio
async def test_restore_config_backup(app_with_auth, mock_config_manager, backup_dir):
    """测试恢复配置备份"""
    backup_file = backup_dir / "test_backup.ini"
    backup_file.write_text("[auth]\nusername=test")

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir
        with patch("shutil.copy2"):
            async with AsyncClient(
                transport=ASGITransport(app=app_with_auth), base_url="http://test"
            ) as client:
                response = await client.post("/api/config/restore/test_backup.ini")
                assert response.status_code == 200


@pytest.mark.asyncio
async def test_restore_config_backup_not_found(
    app_with_auth, mock_config_manager, backup_dir
):
    """测试恢复不存在的备份"""
    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/config/restore/nonexistent.ini")
            assert response.status_code == 404

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_restore_config_backup_exception(
    app_with_auth, mock_config_manager, backup_dir
):
    """测试恢复备份时抛出异常"""
    backup_file = backup_dir / "test.ini"
    backup_file.write_text("[auth]")

    with (
        patch("app.api.config.Path") as mock_path,
        patch("shutil.copy2", side_effect=OSError("copy error")),
    ):
        mock_path.return_value = backup_dir
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post("/api/config/restore/test.ini")
            assert response.status_code == 500


# ========== 清理备份测试 ==========


@pytest.mark.asyncio
async def test_cleanup_config_backups_all(backup_dir):
    """测试清理所有备份"""
    for i in range(5):
        backup_file = backup_dir / f"backup_{i}.ini"
        backup_file.write_text(f"[auth]\nusername=user{i}")

    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config/backups/cleanup", json={"strategy": "all"}
            )
            assert response.status_code == 200

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cleanup_config_backups_recent(backup_dir):
    """测试保留最近的备份"""
    for i in range(5):
        backup_file = backup_dir / f"backup_{i}.ini"
        backup_file.write_text(f"[auth]\nusername=user{i}")

    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config/backups/cleanup",
                json={"strategy": "recent", "keep_count": 2},
            )
            assert response.status_code == 200

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cleanup_config_backups_date(backup_dir):
    """测试按日期清理备份"""
    for i in range(3):
        backup_file = backup_dir / f"backup_{i}.ini"
        backup_file.write_text(f"[auth]\nusername=user{i}")

    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config/backups/cleanup",
                json={"strategy": "date", "keep_days": 30},
            )
            assert response.status_code == 200

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cleanup_config_backups_no_backups(backup_dir):
    """测试清理时没有备份"""
    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config/backups/cleanup", json={"strategy": "all"}
            )
            assert response.status_code == 200

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cleanup_config_backups_no_dir():
    """测试清理时备份目录不存在"""
    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path_obj = MagicMock()
        mock_path_obj.exists.return_value = False
        mock_path.return_value = mock_path_obj

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/config/backups/cleanup", json={"strategy": "all"}
            )
            assert response.status_code == 200
            assert "没有备份" in response.json()["message"]

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cleanup_config_backups_exception(backup_dir):
    """测试清理备份时抛出异常"""
    backup_file = backup_dir / "backup.ini"
    backup_file.write_text("[auth]")

    app = FastAPI()
    app.include_router(config.router)

    with patch("app.api.config.Path") as mock_path:
        mock_path.return_value = backup_dir

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        with patch("pathlib.Path.glob", side_effect=RuntimeError("glob error")):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/config/backups/cleanup", json={"strategy": "all"}
                )
                assert response.status_code == 500

        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_refresh_webhook_key(app_with_auth, mock_config_manager):
    """测试刷新webhook密钥"""
    with patch("app.api.config.security_manager") as mock_sm:
        mock_sm.refresh_webhook_key.return_value = "new_key_123"

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post("/api/config/auth/refresh-webhook-key")
            assert response.status_code == 200
            data = response.json()
            assert data["data"]["webhook_key"] == "new_key_123"


@pytest.mark.asyncio
async def test_refresh_webhook_key_exception(app_with_auth, mock_config_manager):
    """测试刷新webhook密钥时抛出异常"""
    with patch("app.api.config.security_manager") as mock_sm:
        mock_sm.refresh_webhook_key.side_effect = RuntimeError("key error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post("/api/config/auth/refresh-webhook-key")
            assert response.status_code == 500
