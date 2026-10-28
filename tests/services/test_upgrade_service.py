"""升级服务单元测试"""

import os
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.upgrade_service import UpgradeService, UpgradeStage


@pytest.fixture
def upgrade_service():
    """创建独立的升级服务实例"""
    return UpgradeService()


@pytest.fixture
def project_dir(tmp_path):
    """创建模拟的项目目录结构"""
    # 创建 app/ 目录
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("# main")

    # 创建 templates/ 目录
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "base.html").write_text("<html></html>")

    # 创建 static/ 目录
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "js" / "app.js").parent.mkdir(parents=True)
    (static_dir / "js" / "app.js").write_text("// app")

    # 创建 config.ini
    (tmp_path / "config.ini").write_text("[bangumi]\nusername = test")

    # 创建 bangumi_mapping.json
    (tmp_path / "bangumi_mapping.json").write_text("{}")

    # 创建 release_manifest.json
    (tmp_path / "release_manifest.json").write_text('{"version": "1.0.0"}')

    # 创建 data/ 目录
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # 创建数据库
    db_path = data_dir / "sync_records.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE sync_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT
        )
    """)
    conn.execute("INSERT INTO sync_records (title, status) VALUES ('test', 'success')")
    conn.commit()
    conn.close()

    return tmp_path


@pytest.fixture
def sample_zip(tmp_path):
    """创建示例 zip 文件"""
    zip_path = tmp_path / "test.zip"

    # 创建临时目录结构
    content_dir = tmp_path / "zip_content"
    content_dir.mkdir()

    # app/ 目录
    app_dir = content_dir / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("# new main")
    (app_dir / "new_module.py").write_text("# new module")

    # templates/ 目录
    templates_dir = content_dir / "templates"
    templates_dir.mkdir()
    (templates_dir / "base.html").write_text("<html>new</html>")

    # static/ 目录
    static_dir = content_dir / "static"
    static_dir.mkdir()
    (static_dir / "js").mkdir()
    (static_dir / "js" / "app.js").write_text("// new app")

    # release_manifest.json
    (content_dir / "release_manifest.json").write_text('{"version": "2.0.0"}')

    # requirements.txt
    (content_dir / "requirements.txt").write_text("fastapi\nuvicorn")

    # config.ini (should be skipped)
    (content_dir / "config.ini").write_text("[bangumi]\nusername = new")

    # bangumi_mapping.json (should be skipped)
    (content_dir / "bangumi_mapping.json").write_text('{"new": true}')

    # 创建 zip
    with zipfile.ZipFile(zip_path, "w") as zf:
        for root, _dirs, files in os.walk(content_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(content_dir)
                zf.write(file_path, arcname)

    return zip_path


class TestUpgradeServiceEnvironment:
    """环境检测测试"""

    def test_is_upgrade_capable_direct_install(self, upgrade_service):
        """直装模式下应支持升级"""
        with patch("app.services.upgrade_service.docker_helper") as mock_docker:
            mock_docker.is_docker = False
            with patch("app.services.upgrade_service.Path") as mock_path:
                mock_app = MagicMock()
                mock_app.is_dir.return_value = True
                mock_path.return_value = mock_app
                with patch("os.access", return_value=True):
                    assert upgrade_service.is_upgrade_capable() is True

    def test_is_upgrade_capable_docker(self, upgrade_service):
        """Docker 模式下不应支持升级"""
        with patch("app.services.upgrade_service.docker_helper") as mock_docker:
            mock_docker.is_docker = True
            assert upgrade_service.is_upgrade_capable() is False

    def test_is_upgrade_capable_no_write_permission(self, upgrade_service):
        """无写入权限时不应支持升级"""
        with patch("app.services.upgrade_service.docker_helper") as mock_docker:
            mock_docker.is_docker = False
            with patch("app.services.upgrade_service.Path") as mock_path:
                mock_app = MagicMock()
                mock_app.is_dir.return_value = True
                mock_path.return_value = mock_app
                with patch("os.access", return_value=False):
                    assert upgrade_service.is_upgrade_capable() is False


class TestUpgradeServiceBackup:
    """数据库备份测试"""

    def test_backup_all(self, upgrade_service, project_dir, monkeypatch):
        """应正确备份数据库和应用文件到同一目录"""
        monkeypatch.chdir(project_dir)

        backup_dir = project_dir / "backups"

        with patch("app.services.upgrade_service.database_manager") as mock_db:
            mock_db.db_path = project_dir / "data" / "sync_records.db"
            service = UpgradeService()
            service._backup_all(backup_dir)

        # 找到备份目录
        backups = list(backup_dir.glob("backup_*"))
        assert len(backups) == 1

        dest = backups[0]
        # 验证数据库备份
        db_backup = dest / "sync_records.db"
        assert db_backup.exists()
        conn = sqlite3.connect(str(db_backup))
        cursor = conn.execute("SELECT COUNT(*) FROM sync_records")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 1

        # 验证应用文件备份
        assert (dest / "release_manifest.json").exists()
        assert (dest / "app" / "main.py").exists()

    def test_backup_all_missing_db(self, upgrade_service, tmp_path, monkeypatch):
        """数据库不存在时应跳过，不影响文件备份"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "release_manifest.json").write_text('{"version": "1.0.0"}')

        backup_dir = tmp_path / "backups"

        with patch("app.services.upgrade_service.database_manager") as mock_db:
            mock_db.db_path = tmp_path / "nonexistent.db"
            service = UpgradeService()
            service._backup_all(backup_dir)

        backups = list(backup_dir.glob("backup_*"))
        assert len(backups) == 1
        assert not (backups[0] / "sync_records.db").exists()
        assert (backups[0] / "release_manifest.json").exists()


class TestUpgradeServiceApplyUpdate:
    """文件替换测试"""

    def test_apply_update(self, upgrade_service, project_dir, sample_zip, monkeypatch):
        """应正确替换应用文件"""
        monkeypatch.chdir(project_dir)

        temp_dir = project_dir / "data" / "upgrade_temp"
        temp_dir.mkdir(exist_ok=True)

        upgrade_service._apply_update(sample_zip, temp_dir)

        # 验证 app/ 文件被替换
        assert (project_dir / "app" / "main.py").read_text() == "# new main"
        assert (project_dir / "app" / "new_module.py").read_text() == "# new module"

        # 验证 templates/ 被替换
        assert (
            project_dir / "templates" / "base.html"
        ).read_text() == "<html>new</html>"

        # 验证 release_manifest.json 被替换
        assert '"2.0.0"' in (project_dir / "release_manifest.json").read_text()

        # 验证 config.ini 未被替换（原始值）
        assert "test" in (project_dir / "config.ini").read_text()

        # 验证 bangumi_mapping.json 未被替换
        assert (project_dir / "bangumi_mapping.json").read_text() == "{}"

    def test_apply_update_bad_zip(
        self, upgrade_service, project_dir, tmp_path, monkeypatch
    ):
        """损坏的 zip 文件应抛出异常"""
        monkeypatch.chdir(project_dir)

        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("not a zip file")

        temp_dir = project_dir / "data" / "upgrade_temp"
        temp_dir.mkdir(exist_ok=True)

        with pytest.raises(RuntimeError, match="损坏"):
            upgrade_service._apply_update(bad_zip, temp_dir)


class TestUpgradeServiceCleanup:
    """备份清理测试"""

    def test_cleanup_old_backups(self, upgrade_service, tmp_path):
        """应只保留最近的备份"""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # 创建 5 个备份目录（含数据库和文件）
        for i in range(5):
            d = backup_dir / f"backup_2024010{i + 1}_000000"
            d.mkdir()
            (d / "sync_records.db").write_text(f"db {i}")
            (d / "main.py").write_text(f"v{i}")

        upgrade_service._cleanup_old_backups(backup_dir, keep=3)

        # 应该只剩 3 个备份目录
        remaining = [
            d
            for d in backup_dir.iterdir()
            if d.is_dir() and d.name.startswith("backup_")
        ]
        assert len(remaining) == 3


class TestUpgradeServiceProgress:
    """进度管理测试"""

    def test_initial_progress(self, upgrade_service):
        """初始状态应无进度"""
        assert upgrade_service.get_progress("nonexistent") is None

    def test_update_progress(self, upgrade_service):
        """应正确更新进度"""
        upgrade_service._progress["test"] = MagicMock()
        upgrade_service._update_progress(
            "test", UpgradeStage.DOWNLOADING, 50, "downloading..."
        )

        p = upgrade_service.get_progress("test")
        assert p.stage == UpgradeStage.DOWNLOADING
        assert p.percent == 50
        assert p.message == "downloading..."

    def test_is_upgrade_in_progress(self, upgrade_service):
        """应正确跟踪升级状态"""
        assert upgrade_service.is_upgrade_in_progress is False
        upgrade_service._in_progress = True
        assert upgrade_service.is_upgrade_in_progress is True


class TestUpgradeServiceProxy:
    """代理配置测试"""

    def test_get_proxy_with_config(self, upgrade_service):
        """应返回配置的代理"""
        with patch("app.services.upgrade_service.config_manager") as mock_config:
            mock_config.get.return_value = "http://proxy:8080"
            proxy = upgrade_service._get_proxy()
            assert proxy == "http://proxy:8080"

    def test_get_proxy_without_config(self, upgrade_service):
        """无代理配置时应返回 None"""
        with patch("app.services.upgrade_service.config_manager") as mock_config:
            mock_config.get.return_value = ""
            proxy = upgrade_service._get_proxy()
            assert proxy is None


class TestUpgradeServiceVerify:
    """升级验证测试"""

    def test_verify_upgrade_success(self, upgrade_service):
        """版本匹配时应返回 True"""
        with patch("app.services.upgrade_service.get_version", return_value="2.0.0"):
            assert upgrade_service.verify_upgrade("2.0.0") is True

    def test_verify_upgrade_failure(self, upgrade_service):
        """版本不匹配时应返回 False"""
        with patch("app.services.upgrade_service.get_version", return_value="1.0.0"):
            assert upgrade_service.verify_upgrade("2.0.0") is False


class TestStartUpgrade:
    """start_upgrade 测试"""

    @pytest.mark.asyncio
    async def test_start_upgrade_returns_id(self, upgrade_service):
        """应返回 upgrade_id 并设置状态"""
        # mock _run_upgrade 避免实际执行
        with patch.object(upgrade_service, "_run_upgrade", new_callable=AsyncMock):
            upgrade_id = await upgrade_service.start_upgrade()

        assert upgrade_id is not None
        assert len(upgrade_id) == 8
        assert upgrade_service.is_upgrade_in_progress is True
        assert upgrade_service.get_progress(upgrade_id) is not None

    @pytest.mark.asyncio
    async def test_start_upgrade_already_in_progress(self, upgrade_service):
        """已有升级进行中时应抛出异常"""
        upgrade_service._in_progress = True

        with pytest.raises(RuntimeError, match="已有升级任务进行中"):
            await upgrade_service.start_upgrade()


class TestRunUpgrade:
    """_run_upgrade 测试"""

    @pytest.mark.asyncio
    async def test_run_upgrade_success(self, upgrade_service, tmp_path, monkeypatch):
        """升级成功流程"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        upgrade_id = "test123"
        upgrade_service._progress[upgrade_id] = MagicMock()
        upgrade_service._in_progress = True

        with (
            patch.object(
                upgrade_service, "_download_zip", new_callable=AsyncMock
            ) as mock_download,
            patch.object(upgrade_service, "_backup_all") as mock_backup,
            patch.object(upgrade_service, "_apply_update") as mock_apply,
            patch.object(upgrade_service, "_install_deps") as mock_deps,
            patch.object(upgrade_service, "_cleanup_old_backups") as mock_cleanup,
        ):
            mock_download.return_value = tmp_path / "test.zip"
            mock_apply.return_value = tmp_path / "app_backup"

            await upgrade_service._run_upgrade(upgrade_id, None)

        assert upgrade_service._in_progress is False
        mock_download.assert_called_once()
        mock_backup.assert_called_once()
        mock_apply.assert_called_once()
        mock_deps.assert_called_once()
        mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_upgrade_failure_rolls_back(
        self, upgrade_service, tmp_path, monkeypatch
    ):
        """升级失败时应回滚文件和数据库"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        upgrade_id = "test456"
        upgrade_service._progress[upgrade_id] = MagicMock()
        upgrade_service._in_progress = True

        app_backup_dir = tmp_path / "app_backup"
        app_backup_dir.mkdir()

        full_backup_dir = tmp_path / "backups" / "backup_20260101_000000"
        full_backup_dir.mkdir(parents=True)

        with (
            patch.object(
                upgrade_service, "_download_zip", new_callable=AsyncMock
            ) as mock_download,
            patch.object(upgrade_service, "_backup_all", return_value=full_backup_dir),
            patch.object(upgrade_service, "_apply_update") as mock_apply,
            patch.object(
                upgrade_service, "_install_deps", side_effect=RuntimeError("dep failed")
            ),
            patch.object(upgrade_service, "_restore_database") as mock_restore_db,
            patch.object(upgrade_service, "_rollback_files") as mock_rollback,
        ):
            mock_download.return_value = tmp_path / "test.zip"
            mock_apply.return_value = app_backup_dir

            await upgrade_service._run_upgrade(upgrade_id, None)

        assert upgrade_service._in_progress is False
        mock_restore_db.assert_called_once_with(full_backup_dir)
        mock_rollback.assert_called_once_with(
            app_backup_dir, ["app", "templates", "static"]
        )


class _AsyncContextManager:
    """辅助类：让 mock 对象支持 async with"""

    def __init__(self, return_value):
        self._return_value = return_value

    async def __aenter__(self):
        return self._return_value

    async def __aexit__(self, *args):
        pass


class TestDownloadZip:
    """_download_zip 测试"""

    @pytest.mark.asyncio
    async def test_download_zip_success(self, upgrade_service, tmp_path):
        """下载成功"""
        tmp_path.mkdir(parents=True, exist_ok=True)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "100"}

        async def mock_aiter_bytes(chunk_size=8192):
            yield b"x" * 50
            yield b"y" * 50

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_client = MagicMock()
        mock_client.stream.return_value = _AsyncContextManager(mock_response)
        mock_client.aclose = AsyncMock()

        with patch(
            "app.services.upgrade_service.httpx.AsyncClient", return_value=mock_client
        ):
            upgrade_id = "dl_test"
            upgrade_service._progress[upgrade_id] = MagicMock()
            zip_path = await upgrade_service._download_zip(upgrade_id, tmp_path)

        assert zip_path.exists()
        assert zip_path.name == "Bangumi-syncer.zip"

    @pytest.mark.asyncio
    async def test_download_zip_http_error(self, upgrade_service, tmp_path):
        """HTTP 错误应抛出异常（重试耗尽后）"""
        tmp_path.mkdir(parents=True, exist_ok=True)

        mock_response = AsyncMock()
        mock_response.status_code = 404

        mock_client = MagicMock()
        mock_client.stream.return_value = _AsyncContextManager(mock_response)
        mock_client.aclose = AsyncMock()

        with patch(
            "app.services.upgrade_service.httpx.AsyncClient", return_value=mock_client
        ):
            upgrade_id = "dl_err"
            upgrade_service._progress[upgrade_id] = MagicMock()
            with pytest.raises(RuntimeError, match="下载失败"):
                await upgrade_service._download_zip(upgrade_id, tmp_path)

    @pytest.mark.asyncio
    async def test_download_zip_retries_on_network_error(
        self, upgrade_service, tmp_path
    ):
        """网络错误应重试，最终成功"""
        tmp_path.mkdir(parents=True, exist_ok=True)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "10"}

        async def mock_aiter_bytes(chunk_size=8192):
            yield b"z" * 10

        mock_response.aiter_bytes = mock_aiter_bytes

        call_count = 0

        def make_client(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            client = MagicMock()
            if call_count < 3:
                client.stream.side_effect = httpx.ConnectError("connection failed")
            else:
                client.stream.return_value = _AsyncContextManager(mock_response)
            client.aclose = AsyncMock()
            return client

        with (
            patch(
                "app.services.upgrade_service.httpx.AsyncClient",
                side_effect=make_client,
            ),
            patch("app.services.upgrade_service.asyncio.sleep", new_callable=AsyncMock),
        ):
            upgrade_id = "dl_retry"
            upgrade_service._progress[upgrade_id] = MagicMock()
            zip_path = await upgrade_service._download_zip(upgrade_id, tmp_path)

        assert zip_path.exists()
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_download_zip_all_retries_exhausted(self, upgrade_service, tmp_path):
        """所有重试耗尽后应抛出含代理提示的异常"""
        tmp_path.mkdir(parents=True, exist_ok=True)

        mock_client = MagicMock()
        mock_client.stream.side_effect = httpx.ConnectError("connection refused")
        mock_client.aclose = AsyncMock()

        with (
            patch(
                "app.services.upgrade_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("app.services.upgrade_service.asyncio.sleep", new_callable=AsyncMock),
            patch("app.services.upgrade_service.config_manager") as mock_config,
        ):
            mock_config.get.return_value = ""
            upgrade_id = "dl_fail"
            upgrade_service._progress[upgrade_id] = MagicMock()
            with pytest.raises(RuntimeError, match="已尝试 3 个源"):
                await upgrade_service._download_zip(upgrade_id, tmp_path)

    @pytest.mark.asyncio
    async def test_download_zip_no_proxy_hint_when_proxy_configured(
        self, upgrade_service, tmp_path
    ):
        """已配置代理时不应提示设置代理"""
        tmp_path.mkdir(parents=True, exist_ok=True)

        mock_client = MagicMock()
        mock_client.stream.side_effect = httpx.ConnectError("timeout")
        mock_client.aclose = AsyncMock()

        with (
            patch(
                "app.services.upgrade_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("app.services.upgrade_service.asyncio.sleep", new_callable=AsyncMock),
            patch("app.services.upgrade_service.config_manager") as mock_config,
        ):
            mock_config.get.return_value = "http://proxy:8080"
            upgrade_id = "dl_nohint"
            upgrade_service._progress[upgrade_id] = MagicMock()
            with pytest.raises(RuntimeError, match="已尝试 3 个源") as exc_info:
                await upgrade_service._download_zip(upgrade_id, tmp_path)
            assert "代理" not in str(exc_info.value)


class TestBackupDatabaseError:
    """数据库备份失败测试"""

    def test_backup_database_failure(self, upgrade_service, tmp_path, monkeypatch):
        """数据库备份失败时应抛出异常"""
        monkeypatch.chdir(tmp_path)
        with patch("app.services.upgrade_service.database_manager") as mock_db:
            mock_db.db_path = tmp_path / "sync_records.db"
            # 创建一个文件但让 sqlite3.connect 失败
            mock_db.db_path.write_text("not a real db")

            backup_dir = tmp_path / "backups"
            with pytest.raises(RuntimeError, match="数据库备份失败"):
                upgrade_service._backup_all(backup_dir)


class TestRestoreDatabase:
    """_restore_database 测试"""

    def test_restore_database(self, upgrade_service, project_dir, monkeypatch):
        """应从备份恢复数据库"""
        monkeypatch.chdir(project_dir)

        # 先备份
        backup_dir = project_dir / "backups"
        with patch("app.services.upgrade_service.database_manager") as mock_db:
            mock_db.db_path = project_dir / "data" / "sync_records.db"
            service = UpgradeService()
            full_backup_dir = service._backup_all(backup_dir)

        # 修改当前数据库内容
        conn = sqlite3.connect(str(project_dir / "data" / "sync_records.db"))
        conn.execute("DELETE FROM sync_records")
        conn.commit()
        cursor = conn.execute("SELECT COUNT(*) FROM sync_records")
        assert cursor.fetchone()[0] == 0
        conn.close()

        # 恢复
        with patch("app.services.upgrade_service.database_manager") as mock_db:
            mock_db.db_path = project_dir / "data" / "sync_records.db"
            service._restore_database(full_backup_dir)

        # 验证数据恢复
        conn = sqlite3.connect(str(project_dir / "data" / "sync_records.db"))
        cursor = conn.execute("SELECT COUNT(*) FROM sync_records")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 1

    def test_restore_database_no_backup(self, upgrade_service, tmp_path, monkeypatch):
        """备份中无数据库文件时应跳过"""
        monkeypatch.chdir(tmp_path)
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        # 不应抛出异常
        upgrade_service._restore_database(backup_dir)


class TestRollbackFiles:
    """_rollback_files 测试"""

    def test_rollback_files(self, upgrade_service, project_dir, monkeypatch):
        """应从备份恢复文件"""
        monkeypatch.chdir(project_dir)

        # 创建备份目录
        backup_dir = project_dir / "backup"
        backup_dir.mkdir()
        app_backup = backup_dir / "app"
        app_backup.mkdir()
        (app_backup / "main.py").write_text("# original")

        # 修改当前文件
        (project_dir / "app" / "main.py").write_text("# modified")

        upgrade_service._rollback_files(backup_dir, ["app"])

        assert (project_dir / "app" / "main.py").read_text() == "# original"


class TestInstallDeps:
    """_install_deps 测试"""

    def test_install_deps_success(self, upgrade_service, tmp_path, monkeypatch):
        """依赖安装成功，并记录运行时 Python 路径"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "requirements.txt").write_text("fastapi")

        with (
            patch("app.services.upgrade_service.subprocess.run") as mock_run,
            patch("app.services.upgrade_service.config_manager") as mock_config,
            patch(
                "app.services.upgrade_service.persist_runtime_python"
            ) as mock_persist,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            mock_config.get.return_value = ""

            upgrade_service._install_deps()

        mock_persist.assert_called_once()

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--upgrade" in cmd
        assert "requirements.txt" in cmd

    def test_install_deps_failure(self, upgrade_service, tmp_path, monkeypatch):
        """依赖安装失败应抛出异常"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "requirements.txt").write_text("fastapi")

        with (
            patch("app.services.upgrade_service.subprocess.run") as mock_run,
            patch("app.services.upgrade_service.config_manager") as mock_config,
        ):
            mock_run.return_value = MagicMock(returncode=1, stderr="some error")
            mock_config.get.return_value = ""

            with pytest.raises(RuntimeError, match="依赖安装失败"):
                upgrade_service._install_deps()

    def test_install_deps_timeout(self, upgrade_service, tmp_path, monkeypatch):
        """依赖安装超时应抛出异常"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "requirements.txt").write_text("fastapi")

        with (
            patch(
                "app.services.upgrade_service.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="", timeout=600),
            ),
            patch("app.services.upgrade_service.config_manager") as mock_config,
        ):
            mock_config.get.return_value = ""

            with pytest.raises(RuntimeError, match="依赖安装超时"):
                upgrade_service._install_deps()

    def test_install_deps_with_proxy(self, upgrade_service, tmp_path, monkeypatch):
        """有代理时应传递 --proxy 参数"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "requirements.txt").write_text("fastapi")

        with (
            patch("app.services.upgrade_service.subprocess.run") as mock_run,
            patch("app.services.upgrade_service.config_manager") as mock_config,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            mock_config.get.return_value = "http://proxy:8080"

            upgrade_service._install_deps()

        cmd = mock_run.call_args[0][0]
        assert "--proxy" in cmd
        assert "http://proxy:8080" in cmd


class TestCleanupOldBackups:
    """备份清理边界测试"""

    def test_cleanup_nonexistent_dir(self, upgrade_service, tmp_path):
        """目录不存在时应跳过"""
        nonexistent = tmp_path / "nonexistent"
        # 不应抛出异常
        upgrade_service._cleanup_old_backups(nonexistent, keep=3)


class TestRollbackVersion:
    """rollback_version 测试"""

    def test_rollback_version_success(self, upgrade_service, project_dir, monkeypatch):
        """应从备份恢复文件和目录"""
        monkeypatch.chdir(project_dir)

        # 创建备份目录，包含文件和目录
        backup_dir = project_dir / "backups"
        app_backup = backup_dir / "backup_20240101_000000"
        app_backup.mkdir(parents=True)
        (app_backup / "release_manifest.json").write_text('{"version": "1.0.0"}')
        (app_backup / "requirements.txt").write_text("fastapi==0.1.0")
        app_dir = app_backup / "app"
        app_dir.mkdir()
        (app_dir / "main.py").write_text("# old version")

        # 修改当前文件
        (project_dir / "release_manifest.json").write_text('{"version": "2.0.0"}')
        (project_dir / "requirements.txt").write_text("fastapi==2.0.0")
        (project_dir / "app" / "main.py").write_text("# new version")

        upgrade_service.rollback_version()

        assert '"1.0.0"' in (project_dir / "release_manifest.json").read_text()
        assert "0.1.0" in (project_dir / "requirements.txt").read_text()
        assert (project_dir / "app" / "main.py").read_text() == "# old version"

    def test_rollback_version_no_backups(self, upgrade_service, tmp_path, monkeypatch):
        """无备份时应跳过"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "backups").mkdir(parents=True)
        # 不应抛出异常
        upgrade_service.rollback_version()

    def test_rollback_version_no_backup_dir(
        self, upgrade_service, tmp_path, monkeypatch
    ):
        """备份目录不存在时应跳过"""
        monkeypatch.chdir(tmp_path)
        # 不应抛出异常
        upgrade_service.rollback_version()


class TestBackupAll:
    """_backup_all 测试"""

    def test_backup_all_files_and_dirs(self, upgrade_service, project_dir, monkeypatch):
        """应备份关键文件和目录"""
        monkeypatch.chdir(project_dir)

        backup_dir = project_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        with patch("app.services.upgrade_service.database_manager") as mock_db:
            mock_db.db_path = project_dir / "data" / "sync_records.db"
            upgrade_service._backup_all(backup_dir)

        backups = list(backup_dir.glob("backup_*"))
        assert len(backups) == 1

        dest = backups[0]
        assert (dest / "sync_records.db").exists()
        assert (dest / "release_manifest.json").exists()
        assert (dest / "app" / "main.py").exists()
        assert (dest / "templates" / "base.html").exists()
        assert (dest / "static" / "js" / "app.js").exists()

    def test_backup_all_missing_files(self, upgrade_service, tmp_path, monkeypatch):
        """部分文件不存在时应跳过，不影响其他文件"""
        monkeypatch.chdir(tmp_path)

        (tmp_path / "release_manifest.json").write_text('{"version": "1.0.0"}')

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("app.services.upgrade_service.database_manager") as mock_db:
            mock_db.db_path = tmp_path / "nonexistent.db"
            upgrade_service._backup_all(backup_dir)

        backups = list(backup_dir.glob("backup_*"))
        assert len(backups) == 1
        assert (backups[0] / "release_manifest.json").exists()
        assert not (backups[0] / "sync_records.db").exists()
        assert not (backups[0] / "requirements.txt").exists()


class TestRestartApplication:
    """restart_application 测试"""

    def test_restart_windows_with_start_bat(
        self, upgrade_service, tmp_path, monkeypatch
    ):
        """Windows 下有 start.bat 时应优先使用"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "start.bat").write_text(
            "python -m uvicorn app.main:app --port 9000\npause"
        )

        with (
            patch("app.services.upgrade_service.database_manager") as mock_db,
            patch("app.services.upgrade_service.sys") as mock_sys,
            patch("app.services.upgrade_service.subprocess.Popen") as mock_popen,
            patch(
                "app.services.upgrade_service.os._exit", side_effect=SystemExit
            ) as mock_exit,
        ):
            mock_sys.platform = "win32"
            mock_sys.executable = "python"
            mock_sys.argv = ["uvicorn", "app.main:app", "--port", "8000"]

            from app.services.upgrade_service import restart_application

            with pytest.raises(SystemExit):
                restart_application()

            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "cmd"
            assert cmd[1] == "/c"
            assert "start.bat" in cmd[2]
            mock_exit.assert_called_once_with(0)
            mock_db.close.assert_called_once()

    def test_restart_windows_fallback_uvicorn(
        self, upgrade_service, tmp_path, monkeypatch
    ):
        """Windows 下无 start.bat 时应回退到 python -m uvicorn"""
        monkeypatch.chdir(tmp_path)
        # 不创建 start.bat

        with (
            patch("app.services.upgrade_service.database_manager") as mock_db,
            patch("app.services.upgrade_service.sys") as mock_sys,
            patch("app.services.upgrade_service.subprocess.Popen") as mock_popen,
            patch(
                "app.services.upgrade_service.os._exit", side_effect=SystemExit
            ) as mock_exit,
        ):
            mock_sys.platform = "win32"
            mock_sys.executable = "C:\\Python\\python.exe"
            mock_sys.argv = [
                "C:\\Python\\Scripts\\uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
            ]

            from app.services.upgrade_service import restart_application

            with pytest.raises(SystemExit):
                restart_application()

            mock_popen.assert_called_once_with(
                [
                    "C:\\Python\\python.exe",
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                ]
            )
            mock_exit.assert_called_once_with(0)
            mock_db.close.assert_called_once()

    def test_restart_unix(self, upgrade_service, tmp_path, monkeypatch):
        """Unix 下应使用 os.execv 替换进程"""
        monkeypatch.chdir(tmp_path)

        with (
            patch("app.services.upgrade_service.database_manager") as mock_db,
            patch("app.services.upgrade_service.sys") as mock_sys,
            patch("app.services.upgrade_service.os.execv") as mock_execv,
        ):
            mock_sys.platform = "linux"
            mock_sys.executable = "python"
            mock_sys.argv = ["app.py"]

            from app.services.upgrade_service import restart_application

            restart_application()

            mock_execv.assert_called_once_with("python", ["python", "app.py"])
            mock_db.close.assert_called_once()
