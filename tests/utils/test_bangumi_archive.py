"""Bangumi Archive 模块单元测试

覆盖：
- ArchiveMeta 序列化/反序列化
- ArchiveImporter 表导入（全量重建 + JSON Lines 解析 + 索引）
- ArchiveDownloader SHA256 校验 + URL 构建
- BangumiArchive 双库切换流程（mock 下载与导入）
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.utils.bangumi_archive._archive import ArchiveMeta, BangumiArchive
from app.utils.bangumi_archive._download import ArchiveDownloader
from app.utils.bangumi_archive._import import ArchiveImporter

# ===== ArchiveMeta 测试 =====


class TestArchiveMeta:
    def test_default_values(self):
        meta = ArchiveMeta()
        assert meta.active == "a"
        assert meta.last_import_at is None
        assert meta.row_counts == {}
        assert meta.last_error is None

    def test_round_trip(self):
        meta = ArchiveMeta(
            active="b",
            last_import_at="2026-07-26T10:00:00+00:00",
            last_import_duration_sec=120.5,
            dump_date="2026-07-20",
            dump_filename="dump.zip",
            dump_size_bytes=400000000,
            row_counts={"subject": 100, "episode": 200},
            last_error=None,
            last_error_at=None,
        )
        data = meta.to_dict()
        assert data["active"] == "b"
        assert data["row_counts"]["subject"] == 100

        restored = ArchiveMeta.from_dict(data)
        assert restored.active == "b"
        assert restored.last_import_duration_sec == 120.5
        assert restored.row_counts == {"subject": 100, "episode": 200}

    def test_from_dict_defaults(self):
        """from_dict 对缺失字段使用默认值"""
        restored = ArchiveMeta.from_dict({})
        assert restored.active == "a"
        assert restored.row_counts == {}
        assert restored.last_import_at is None


# ===== ArchiveImporter 测试 =====


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_archive.db"


@pytest.fixture
def sample_zip_with_jsonl(tmp_path: Path) -> Path:
    """构建一个包含 subject/episode JSON Lines 的测试 zip"""
    zip_path = tmp_path / "dump.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # subject.jsonlines
        subject_lines = [
            json.dumps(
                {
                    "id": 1,
                    "type": 2,
                    "name": "Test Anime",
                    "name_cn": "测试动画",
                    "infobox": "别名: Test",
                    "platform": None,
                    "summary": "test summary",
                    "nsfw": 0,
                    "date": "2026-01-01",
                    "favorite": 10,
                    "series": 1,
                    "tags": ["tag1", "tag2"],
                    "score": 7.5,
                    "score_details": "{}",
                    "rank": 100,
                    "meta_tags": ["meta"],
                }
            ),
            json.dumps(
                {
                    "id": 2,
                    "name": "Second",
                    "type": 2,
                    "name_cn": "",
                    "date": "2026-02-01",
                    "series": 1,
                }
            ),
        ]
        zf.writestr("subject.jsonlines", "\n".join(subject_lines))

        # episode.jsonlines
        episode_lines = [
            json.dumps(
                {
                    "id": 101,
                    "name": "EP01",
                    "name_cn": "第一集",
                    "description": "",
                    "airdate": "2026-01-08",
                    "disc": None,
                    "duration": "24",
                    "subject_id": 1,
                    "sort": 1,
                    "type": 0,
                }
            ),
        ]
        zf.writestr("episode.jsonlines", "\n".join(episode_lines))

        # subject-relations.jsonlines（注意文件名带连字符）
        relation_lines = [
            json.dumps(
                {
                    "subject_id": 1,
                    "relation_type": 1,
                    "related_subject_id": 2,
                    "order": 1,
                }
            ),
        ]
        zf.writestr("subject-relations.jsonlines", "\n".join(relation_lines))

    return zip_path


class TestArchiveImporter:
    @pytest.mark.asyncio
    async def test_import_all_creates_tables(
        self, sample_zip_with_jsonl: Path, temp_db_path: Path
    ):
        """import_all 应建立所有表并导入数据"""
        importer = ArchiveImporter()

        row_counts, duration = await importer.import_all(
            zip_path=sample_zip_with_jsonl,
            target_db=temp_db_path,
            task_id="test",
            progress_cb=None,
        )

        assert duration > 0
        assert row_counts["subject"] == 2
        assert row_counts["episode"] == 1
        assert row_counts["subject_relation"] == 1

    @pytest.mark.asyncio
    async def test_import_creates_indexes(
        self, sample_zip_with_jsonl: Path, temp_db_path: Path
    ):
        """导入后应建立索引"""
        importer = ArchiveImporter()

        await importer.import_all(
            zip_path=sample_zip_with_jsonl,
            target_db=temp_db_path,
            task_id="test",
            progress_cb=None,
        )

        conn = sqlite3.connect(str(temp_db_path))
        try:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
            index_names = {row[0] for row in indexes}
            assert "idx_subject_name" in index_names
            assert "idx_episode_subject" in index_names
            assert "idx_relation_subject" in index_names
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_import_optional_fields_missing(
        self, sample_zip_with_jsonl: Path, temp_db_path: Path
    ):
        """第二条 subject 缺失 score/rank/tags 等字段，应填 NULL"""
        importer = ArchiveImporter()

        await importer.import_all(
            zip_path=sample_zip_with_jsonl,
            target_db=temp_db_path,
            task_id="test",
            progress_cb=None,
        )

        conn = sqlite3.connect(str(temp_db_path))
        try:
            row = conn.execute(
                "SELECT score, rank, tags FROM subject WHERE id = 2"
            ).fetchone()
            assert row[0] is None  # score
            assert row[1] is None  # rank
            assert row[2] is None  # tags
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_import_serializes_list_field(
        self, sample_zip_with_jsonl: Path, temp_db_path: Path
    ):
        """list/dict 类型字段应序列化为 JSON 字符串"""
        importer = ArchiveImporter()

        await importer.import_all(
            zip_path=sample_zip_with_jsonl,
            target_db=temp_db_path,
            task_id="test",
            progress_cb=None,
        )

        conn = sqlite3.connect(str(temp_db_path))
        try:
            row = conn.execute("SELECT tags FROM subject WHERE id = 1").fetchone()
            assert row[0] is not None
            tags = json.loads(row[0])
            assert tags == ["tag1", "tag2"]
        finally:
            conn.close()

    def test_clear_database_removes_file(self, tmp_path: Path):
        """clear_database 应删除库文件"""
        db_path = tmp_path / "to_clean.db"
        db_path.write_text("dummy")
        assert db_path.exists()

        importer = ArchiveImporter()
        importer.clear_database(db_path)
        assert not db_path.exists()

    def test_clear_database_removes_index_cache(self, tmp_path: Path):
        """clear_database 应同时删除对应的 .index 磁盘缓存文件

        双库设计要求：导入完成切换 active 后清空旧库，
        旧库的 .index 缓存（~460MB）也应一并释放，避免磁盘累积。
        """
        # 模拟 bangumi_archive_a.db + bangumi_archive_a.index 双文件
        db_path = tmp_path / "bangumi_archive_a.db"
        index_path = tmp_path / "bangumi_archive_a.index"
        db_path.write_text("dummy db")
        index_path.write_text("dummy index cache ~460MB")
        assert db_path.exists()
        assert index_path.exists()

        importer = ArchiveImporter()
        importer.clear_database(db_path)

        # db 和 .index 都应被清理
        assert not db_path.exists()
        assert not index_path.exists()

    def test_clear_database_removes_wal_shm_sidecars(self, tmp_path: Path):
        """clear_database 应同时删除 WAL/SHM sidecar 文件"""
        db_path = tmp_path / "bangumi_archive_a.db"
        wal_path = tmp_path / "bangumi_archive_a.db-wal"
        shm_path = tmp_path / "bangumi_archive_a.db-shm"
        db_path.write_text("dummy")
        wal_path.write_text("wal")
        shm_path.write_text("shm")

        importer = ArchiveImporter()
        importer.clear_database(db_path)

        assert not db_path.exists()
        assert not wal_path.exists()
        assert not shm_path.exists()

    def test_clear_database_missing_index_is_safe(self, tmp_path: Path):
        """clear_database 在 .index 不存在时应安全跳过（不抛异常）"""
        db_path = tmp_path / "bangumi_archive_b.db"
        db_path.write_text("dummy")
        # 不创建 .index 文件

        importer = ArchiveImporter()
        # 不应抛异常
        importer.clear_database(db_path)
        assert not db_path.exists()


# ===== ArchiveDownloader 测试 =====


class TestArchiveDownloader:
    def test_build_download_urls_with_mirrors(self):
        """镜像源应拼接 GitHub URL"""
        downloader = ArchiveDownloader(
            http_proxy=None,
            ssl_verify=True,
            mirrors=("https://ghfast.top/", "https://gh-proxy.com/"),
        )
        urls = downloader._build_download_urls(
            "https://github.com/bangumi/Archive/releases/download/archive/dump.zip"
        )
        assert len(urls) == 3
        assert urls[0].startswith("https://ghfast.top/")
        assert urls[1].startswith("https://gh-proxy.com/")
        assert (
            urls[2]
            == "https://github.com/bangumi/Archive/releases/download/archive/dump.zip"
        )

    def test_build_download_urls_no_mirrors(self):
        """无镜像时仅返回 GitHub 直连"""
        downloader = ArchiveDownloader(mirrors=())
        urls = downloader._build_download_urls("https://github.com/test.zip")
        assert urls == ["https://github.com/test.zip"]

    def test_build_latest_urls(self):
        """latest.json URL 也支持镜像 fallback"""
        downloader = ArchiveDownloader(mirrors=("https://ghfast.top/",))
        urls = downloader._build_latest_urls(
            "https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json"
        )
        assert len(urls) == 2
        assert "ghfast.top" in urls[0]

    @pytest.mark.asyncio
    async def test_verify_sha256_match(self, tmp_path: Path):
        """SHA256 匹配时通过"""
        import hashlib

        content = b"hello world"
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(content)
        digest = "sha256:" + hashlib.sha256(content).hexdigest()

        downloader = ArchiveDownloader()

        await downloader.verify_sha256(zip_path, digest)  # 不抛异常即通过

    @pytest.mark.asyncio
    async def test_verify_sha256_mismatch(self, tmp_path: Path):
        """SHA256 不匹配时抛 RuntimeError"""
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(b"hello world")

        downloader = ArchiveDownloader()

        with pytest.raises(RuntimeError, match="SHA256 校验失败"):
            await downloader.verify_sha256(zip_path, "sha256:00000000")

    @pytest.mark.asyncio
    async def test_verify_sha256_skip_when_no_digest(self, tmp_path: Path):
        """digest 为空时跳过校验"""
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(b"hello")

        downloader = ArchiveDownloader()

        await downloader.verify_sha256(zip_path, "")  # 不抛异常即通过


# ===== BangumiArchive 双库切换测试 =====


@pytest.fixture
def isolated_archive(tmp_path: Path, monkeypatch):
    """创建独立数据目录的 BangumiArchive 实例，不污染全局单例

    注意：本 fixture 会 set config_manager 的 bangumi-archive.enabled=true，
    但全局 archive_shortcut 的隔离由 conftest.py 的 _isolate_archive_shortcut
    autouse fixture 负责（mock reload_config 为 noop），无需在此清理。
    """
    # 重新加载配置到临时目录
    from app.core.config import config_manager

    # 配置测试目录
    config_manager.set("bangumi-archive", "enabled", "true")
    config_manager.set("bangumi-archive", "data_dir", str(tmp_path / "data"))
    config_manager.set("bangumi-archive", "min_disk_space_mb", "1")  # 测试用小阈值

    archive = BangumiArchive()
    # 覆盖路径，确保独立于全局实例
    archive.data_dir = tmp_path / "data"
    archive.data_dir.mkdir(parents=True, exist_ok=True)
    archive.db_a_path = archive.data_dir / "bangumi_archive_a.db"
    archive.db_b_path = archive.data_dir / "bangumi_archive_b.db"
    archive.active_file = archive.data_dir / "bangumi_archive.active"
    archive.meta_file = archive.data_dir / "bangumi_archive.meta"
    archive.min_disk_space_mb = 1
    archive._meta = archive._load_meta()
    yield archive
    # teardown: 还原 config，避免 data_dir 等设置残留影响后续测试
    config_manager.set("bangumi-archive", "enabled", "false")


class TestBangumiArchive:
    def test_initial_state(self, isolated_archive: BangumiArchive):
        """首次启动默认 active='a'，无导入历史"""
        meta = isolated_archive.get_meta()
        assert meta.active == "a"
        assert meta.last_import_at is None
        assert meta.dump_date is None

    def test_active_db_path_default(self, isolated_archive: BangumiArchive):
        """active='a' 时返回 db_a"""
        path = isolated_archive.get_active_db_path()
        assert path.name == "bangumi_archive_a.db"

    def test_inactive_db_path_default(self, isolated_archive: BangumiArchive):
        """active='a' 时 inactive 是 db_b"""
        path = isolated_archive.get_inactive_db_path()
        assert path.name == "bangumi_archive_b.db"

    def test_active_switch_after_b(self, isolated_archive: BangumiArchive):
        """切换 active 到 b 后，active/inactive 路径互换"""
        isolated_archive._meta.active = "b"
        assert isolated_archive.get_active_db_path().name == "bangumi_archive_b.db"
        assert isolated_archive.get_inactive_db_path().name == "bangumi_archive_a.db"

    def test_disk_space_check_passes(self, isolated_archive: BangumiArchive):
        """磁盘空间足够时不抛异常"""
        isolated_archive._check_disk_space()  # 不抛异常即通过

    def test_validate_row_counts_passes(self, isolated_archive: BangumiArchive):
        """subject/episode 有数据时校验通过"""
        isolated_archive._validate_row_counts(
            {"subject": 100, "episode": 200}
        )  # 不抛异常即通过

    def test_validate_row_counts_empty_subject_fails(
        self, isolated_archive: BangumiArchive
    ):
        """subject 表 0 行时报错"""
        with pytest.raises(RuntimeError, match="subject 表行数为 0"):
            isolated_archive._validate_row_counts({"subject": 0, "episode": 100})

    def test_validate_row_counts_empty_episode_fails(
        self, isolated_archive: BangumiArchive
    ):
        """episode 表 0 行时报错"""
        with pytest.raises(RuntimeError, match="episode 表行数为 0"):
            isolated_archive._validate_row_counts({"subject": 100, "episode": 0})

    def test_import_in_progress_flag(self, isolated_archive: BangumiArchive):
        """import_in_progress 标志默认 False"""
        assert isolated_archive.is_import_in_progress is False

    def test_get_status_contains_expected_fields(
        self, isolated_archive: BangumiArchive
    ):
        """status 字典包含所有预期字段"""
        status = isolated_archive.get_status()
        expected_keys = {
            "enabled",
            "active",
            "active_db_path",
            "db_size_bytes",
            "last_import_at",
            "last_import_duration_sec",
            "dump_date",
            "dump_filename",
            "dump_size_bytes",
            "row_counts",
            "last_error",
            "last_error_at",
            "import_in_progress",
            "update_cron",
            "data_dir",
        }
        assert set(status.keys()) >= expected_keys

    def test_load_config_auto_migrate_to_archive_subdir(
        self, tmp_path: Path, monkeypatch
    ):
        """_load_config 检测旧路径下无 db 但 archive 子目录下有时自动迁移

        复现用户场景：之前 data_dir=./data，数据已迁移到 ./data/archive，
        但用户 config.ini 仍写着 data_dir=./data。
        期望：自动切换到 ./data/archive 子目录。
        """
        from app.core.config import config_manager

        # 模拟用户旧配置：data_dir=./data
        # 实际数据库在 ./data/archive 子目录下
        old_data_dir = tmp_path / "data"
        archive_subdir = old_data_dir / "archive"
        archive_subdir.mkdir(parents=True)

        # 在 archive 子目录下放置 db 文件（模拟已迁移）
        (archive_subdir / "bangumi_archive_b.db").touch()

        # 旧路径下没有任何 db 文件
        monkeypatch.chdir(tmp_path)
        config_manager.set("bangumi-archive", "enabled", "true")
        config_manager.set("bangumi-archive", "data_dir", str(old_data_dir))
        config_manager.set("bangumi-archive", "min_disk_space_mb", "1")

        archive = BangumiArchive()

        # 应自动切换到 archive 子目录
        assert archive.data_dir == archive_subdir
        assert archive.db_b_path == archive_subdir / "bangumi_archive_b.db"
        assert archive.db_b_path.exists()

    def test_load_config_no_migrate_when_db_in_configured_dir(
        self, tmp_path: Path, monkeypatch
    ):
        """_load_config 在配置路径下有 db 时不触发迁移"""
        from app.core.config import config_manager

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # 配置路径下直接有 db 文件
        (data_dir / "bangumi_archive_a.db").touch()

        monkeypatch.chdir(tmp_path)
        config_manager.set("bangumi-archive", "enabled", "true")
        config_manager.set("bangumi-archive", "data_dir", str(data_dir))
        config_manager.set("bangumi-archive", "min_disk_space_mb", "1")

        archive = BangumiArchive()

        # 不应切换
        assert archive.data_dir == data_dir
        assert archive.db_a_path == data_dir / "bangumi_archive_a.db"


class TestBackgroundIndexBuildOnStartup:
    """BangumiArchive 启动时触发标题索引后台构建"""

    def test_init_triggers_build_when_enabled_and_db_exists(
        self, tmp_path: Path, monkeypatch
    ):
        """enabled=True 且 active DB 存在时，__init__ 应触发后台构建"""
        # 准备 active DB
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db_a = data_dir / "bangumi_archive_a.db"
        db_a.touch()  # 创建空文件表示存在

        # mock config_manager
        def mock_get(section, key, fallback=None):
            if section == "bangumi-archive":
                if key == "enabled":
                    return "true"
                if key == "data_dir":
                    return str(data_dir)
                if key == "min_disk_space_mb":
                    return "1"
            return fallback

        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.config_manager.get",
            mock_get,
        )

        # mock archive_title_index.build_in_background
        mock_build = MagicMock()
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.archive_title_index.build_in_background",
            mock_build,
        )

        BangumiArchive()
        mock_build.assert_called_once()

    def test_init_skips_build_when_disabled(self, tmp_path: Path, monkeypatch):
        """enabled=False 时不触发后台构建"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        def mock_get(section, key, fallback=None):
            if section == "bangumi-archive":
                if key == "enabled":
                    return "false"
                if key == "data_dir":
                    return str(data_dir)
            return fallback

        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.config_manager.get",
            mock_get,
        )

        mock_build = MagicMock()
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.archive_title_index.build_in_background",
            mock_build,
        )

        BangumiArchive()
        mock_build.assert_not_called()

    def test_init_skips_build_when_db_missing(self, tmp_path: Path, monkeypatch):
        """enabled=True 但 active DB 不存在时不触发构建"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # 不创建 bangumi_archive_a.db

        def mock_get(section, key, fallback=None):
            if section == "bangumi-archive":
                if key == "enabled":
                    return "true"
                if key == "data_dir":
                    return str(data_dir)
            return fallback

        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.config_manager.get",
            mock_get,
        )

        mock_build = MagicMock()
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.archive_title_index.build_in_background",
            mock_build,
        )

        BangumiArchive()
        mock_build.assert_not_called()

    def test_reload_config_triggers_build(self, tmp_path: Path, monkeypatch):
        """reload_config 后应再次触发后台构建"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db_a = data_dir / "bangumi_archive_a.db"
        db_a.touch()

        def mock_get(section, key, fallback=None):
            if section == "bangumi-archive":
                if key == "enabled":
                    return "true"
                if key == "data_dir":
                    return str(data_dir)
            return fallback

        monkeypatch.setattr(
            "app.utils.bangumi_archive._archive.config_manager.get",
            mock_get,
        )

        mock_build = MagicMock()
        monkeypatch.setattr(
            "app.utils.bangumi_archive._title_index.archive_title_index.build_in_background",
            mock_build,
        )

        archive = BangumiArchive()
        mock_build.assert_called_once()
        # reload_config 后再次触发（build_in_background 内部有就绪检查，不会重复构建）
        archive.reload_config()
        assert mock_build.call_count == 2
