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
from app.utils.bangumi_archive._store import ArchiveStore

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
            assert "idx_episode_airdate" in index_names  # 放送日历视图用
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

    @pytest.mark.asyncio
    async def test_import_ignores_duplicate_primary_key(
        self, tmp_path: Path, temp_db_path: Path
    ):
        """dump 中存在重复主键时应静默跳过，不报 UNIQUE constraint 错误

        subject_relation 复合主键 (subject_id, relation_type, related_subject_id)
        在真实 dump 中可能出现重复行，INSERT OR IGNORE 静默跳过冲突行。
        """
        zip_path = tmp_path / "dup.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            subj_lines = [
                json.dumps({"id": 1, "type": 2, "name": "A"}),
                json.dumps({"id": 2, "type": 2, "name": "B"}),
            ]
            zf.writestr("subject.jsonlines", "\n".join(subj_lines))
            zf.writestr("episode.jsonlines", "")
            # 两行重复主键
            rel = json.dumps(
                {
                    "subject_id": 1,
                    "relation_type": 1,
                    "related_subject_id": 2,
                    "order": 1,
                }
            )
            zf.writestr("subject-relations.jsonlines", "\n".join([rel, rel]))

        importer = ArchiveImporter()
        row_counts, _ = await importer.import_all(
            zip_path=zip_path, target_db=temp_db_path, task_id="test", progress_cb=None
        )

        # count 是读取行数（含重复），DB 里实际只 1 行（OR IGNORE 去重）
        assert row_counts["subject_relation"] == 2
        conn = sqlite3.connect(str(temp_db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM subject_relation").fetchone()[0]
        finally:
            conn.close()
        assert count == 1

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


class TestExtractZipSecurity:
    """_extract_zip 的 Zip-Slip 路径穿越防护

    本地上传 zip 内容不可信，恶意成员（../、绝对路径）必须被跳过，
    不得写出到解压根目录之外。
    """

    def _make_zip(self, zip_path: Path, entries: list[tuple[str, str]]) -> Path:
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in entries:
                zf.writestr(name, content)
        return zip_path

    def test_skips_parent_traversal_members(self, tmp_path: Path):
        """.. 穿越成员被跳过，且不写出到 extract_dir 之外"""
        zip_path = self._make_zip(
            tmp_path / "evil.zip", [("../../evil.txt", "pwn"), ("ok.txt", "fine")]
        )
        extract_dir = tmp_path / "out"
        extract_dir.mkdir()

        ArchiveImporter()._extract_zip(zip_path, extract_dir)

        assert not (tmp_path / "evil.txt").exists()
        assert list(extract_dir.iterdir()) == [extract_dir / "ok.txt"]

    def test_skips_absolute_path_members(self, tmp_path: Path):
        """绝对路径（POSIX 与 Windows 盘符）成员被跳过"""
        zip_path = self._make_zip(
            tmp_path / "abs.zip",
            [("/etc/evil.txt", "pwn"), ("C:/Windows/evil.txt", "pwn")],
        )
        extract_dir = tmp_path / "out"
        extract_dir.mkdir()

        ArchiveImporter()._extract_zip(zip_path, extract_dir)

        assert list(extract_dir.iterdir()) == []

    def test_skips_windows_backslash_traversal(self, tmp_path: Path):
        """Windows 反斜杠形式的 ..\\ 穿越成员被跳过"""
        zip_path = self._make_zip(tmp_path / "bs.zip", [("..\\..\\evil.txt", "pwn")])
        extract_dir = tmp_path / "out"
        extract_dir.mkdir()

        ArchiveImporter()._extract_zip(zip_path, extract_dir)

        assert list(extract_dir.iterdir()) == []

    def test_extracts_normal_nested_members(self, tmp_path: Path):
        """正常嵌套目录成员照常解压，不影响既有功能"""
        zip_path = self._make_zip(
            tmp_path / "normal.zip",
            [("subject.jsonlines", "data"), ("nested/episode.jsonlines", "e")],
        )
        extract_dir = tmp_path / "out"

        ArchiveImporter()._extract_zip(zip_path, extract_dir)

        assert (extract_dir / "subject.jsonlines").read_text() == "data"
        assert (extract_dir / "nested" / "episode.jsonlines").read_text() == "e"


# ===== ArchiveStore 按日期查询测试 =====


class TestArchiveStoreAiringQuery:
    """ArchiveStore.get_episodes_by_airdate 按日期范围查询"""

    @pytest.fixture
    def store_with_data(self, tmp_path: Path) -> ArchiveStore:
        """构造含多条 episode/subject 的测试库"""
        db_path = tmp_path / "test_archive.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE subject (
                id INTEGER PRIMARY KEY, type INTEGER, name TEXT, name_cn TEXT,
                date TEXT, favorite INTEGER
            );
            CREATE TABLE episode (
                id INTEGER PRIMARY KEY, name TEXT, name_cn TEXT,
                airdate TEXT, subject_id INTEGER, sort INTEGER, type INTEGER
            );
            INSERT INTO subject (id, type, name, name_cn, date, favorite) VALUES
                (1, 2, 'Anime A', '动画A', '2026-01-01', 10),
                (2, 2, 'Anime B', '动画B', '2026-01-02', 5),
                (3, 6, 'Real C', '三次元C', '2026-01-03', 3),
                (4, 1, 'Book D', '书籍D', '2026-01-04', 0);
            INSERT INTO episode (id, name, name_cn, airdate, subject_id, sort, type) VALUES
                (101, 'EP01', '第一集', '2026-02-01', 1, 1, 0),
                (102, 'EP02', '第二集', '2026-02-08', 1, 2, 0),
                (201, 'EP01', '第一集', '2026-02-01', 2, 1, 0),
                (301, 'EP01', '第一集', '2026-02-15', 3, 1, 0),
                (401, 'EP01', '第一集', '2026-02-01', 4, 1, 0),
                (103, 'SP', '特别篇', '', 1, 3, 0);
            CREATE INDEX idx_episode_airdate ON episode(airdate);
            """
        )
        conn.commit()
        conn.close()

        from app.utils.bangumi_archive import _archive

        # 保存原值，teardown 时恢复，避免污染全局单例
        orig_db_a = _archive.bangumi_archive.db_a_path
        orig_active = _archive.bangumi_archive._meta.active
        _archive.bangumi_archive.db_a_path = db_path
        _archive.bangumi_archive._meta.active = "a"

        store = ArchiveStore()
        yield store

        # teardown：关闭连接并恢复全局单例状态
        store.close()
        _archive.bangumi_archive.db_a_path = orig_db_a
        _archive.bangumi_archive._meta.active = orig_active

    def test_date_range_filter(self, store_with_data: ArchiveStore):
        """日期范围查询只返回范围内的 episode"""
        results = store_with_data.get_episodes_by_airdate("2026-02-01", "2026-02-01")
        # 2026-02-01 有 subject 1, 2, 4 的 EP01（subject 4 是书籍 type=1，被过滤）
        assert len(results) == 2
        subject_ids = {r["subject_id"] for r in results}
        assert subject_ids == {1, 2}

    def test_subject_types_filter(self, store_with_data: ArchiveStore):
        """默认仅返回 type=2(动画) 和 type=6(三次元)，排除书籍"""
        results = store_with_data.get_episodes_by_airdate("2026-02-01", "2026-02-28")
        types = {r["subject_type"] for r in results}
        assert types == {2, 6}  # 不含 type=1 的书籍

    def test_empty_airdate_excluded(self, store_with_data: ArchiveStore):
        """airdate 为空的 episode 应被排除"""
        results = store_with_data.get_episodes_by_airdate("2026-01-01", "2026-12-31")
        # subject 1 的 SP（id=103）airdate='' 应被排除
        ep_ids = {r["episode_id"] for r in results}
        assert 103 not in ep_ids

    def test_subject_ids_filter(self, store_with_data: ArchiveStore):
        """subject_ids 非空时仅返回这些 subject 的 episode"""
        results = store_with_data.get_episodes_by_airdate(
            "2026-02-01", "2026-02-28", subject_ids={1}
        )
        assert len(results) == 2  # subject 1 的 EP01 和 EP02
        assert all(r["subject_id"] == 1 for r in results)

    def test_empty_subject_ids(self, store_with_data: ArchiveStore):
        """subject_ids 为空集合时应返回空列表（不构造 IN () SQL）"""
        results = store_with_data.get_episodes_by_airdate(
            "2026-02-01", "2026-02-28", subject_ids=set()
        )
        assert results == []

    def test_join_subject_fields(self, store_with_data: ArchiveStore):
        """结果应包含 JOIN subject 的 name/name_cn/type"""
        results = store_with_data.get_episodes_by_airdate("2026-02-15", "2026-02-15")
        assert len(results) == 1
        r = results[0]
        assert r["subject_id"] == 3
        assert r["subject_name"] == "Real C"
        assert r["subject_name_cn"] == "三次元C"
        assert r["subject_type"] == 6
        assert r["airdate"] == "2026-02-15"

    def test_ordered_by_airdate(self, store_with_data: ArchiveStore):
        """结果按 airdate 升序排列"""
        results = store_with_data.get_episodes_by_airdate("2026-02-01", "2026-02-28")
        dates = [r["airdate"] for r in results]
        assert dates == sorted(dates)


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
        isolated_archive.check_disk_space()  # 不抛异常即通过

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

    def test_cleanup_stale_tmp_removes_old_dirs(self, isolated_archive: BangumiArchive):
        """_cleanup_stale_tmp 删除超过阈值的残留子目录，保留新目录"""
        import os
        import time as _time

        from app.utils.bangumi_archive._archive import _TMP_DIR_MAX_AGE_SEC

        tmp_dir = isolated_archive.get_tmp_dir()
        # 模拟两个残留目录：一个过期，一个未过期
        stale_dir = tmp_dir / "upload_old_crash"
        fresh_dir = tmp_dir / "upload_new"
        stale_dir.mkdir(parents=True, exist_ok=True)
        fresh_dir.mkdir(parents=True, exist_ok=True)
        (stale_dir / "dump.zip").write_bytes(b"fake")
        (fresh_dir / "dump.zip").write_bytes(b"fake")

        # 将 stale_dir 的 mtime 改为超过阈值
        old_time = _time.time() - _TMP_DIR_MAX_AGE_SEC - 60
        os.utime(stale_dir, (old_time, old_time))

        isolated_archive._cleanup_stale_tmp()

        assert not stale_dir.exists(), "过期残留目录应被删除"
        assert fresh_dir.exists(), "未过期目录应保留"

    def test_cleanup_stale_tmp_skips_active_task_dir(
        self, isolated_archive: BangumiArchive
    ):
        """_cleanup_stale_tmp 不删除正在进行的任务目录（即使已过期）"""
        import os
        import time as _time

        from app.utils.bangumi_archive._archive import _TMP_DIR_MAX_AGE_SEC

        tmp_dir = isolated_archive.get_tmp_dir()
        # 模拟正在进行的任务目录（已过期但不应被删）
        active_task_id = "20260731_120000"
        active_dir = tmp_dir / active_task_id
        active_dir.mkdir(parents=True, exist_ok=True)
        (active_dir / "dump.zip").write_bytes(b"fake")
        old_time = _time.time() - _TMP_DIR_MAX_AGE_SEC - 60
        os.utime(active_dir, (old_time, old_time))

        # 标记为正在进行的任务
        isolated_archive._import_in_progress = True
        isolated_archive._current_task_id = active_task_id

        isolated_archive._cleanup_stale_tmp()

        assert active_dir.exists(), "正在进行的任务目录不应被删除"


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


# subject 517106 = 逃げ上手の若君 第二期：全局连续 sort 13..24（type=0），外传 SP sort=25（type=3）
_SEASON2_ROWS = [
    (13, "EP1", "", "", "2025-01-01", 0, 0, 517106, 13, 0),
    (14, "EP2", "", "", "2025-01-08", 0, 0, 517106, 14, 0),
    (15, "EP3", "", "", "2025-01-15", 0, 0, 517106, 15, 0),
    (16, "EP4", "", "", "2025-01-22", 0, 0, 517106, 16, 0),
    (17, "EP5", "", "", "2025-01-29", 0, 0, 517106, 17, 0),
    (18, "EP6", "", "", "2025-02-05", 0, 0, 517106, 18, 0),
    (19, "EP7", "", "", "2025-02-12", 0, 0, 517106, 19, 0),
    (20, "EP8", "", "", "2025-02-19", 0, 0, 517106, 20, 0),
    (21, "EP9", "", "", "2025-02-26", 0, 0, 517106, 21, 0),
    (22, "EP10", "", "", "2025-03-05", 0, 0, 517106, 22, 0),
    (23, "EP11", "", "", "2025-03-12", 0, 0, 517106, 23, 0),
    (24, "EP12", "", "", "2025-03-19", 0, 0, 517106, 24, 0),
    (25, "SP", "", "", "2025-03-26", 0, 0, 517106, 25, 3),
]

# subject 900001：首话 sort 为 NULL，用于覆盖排序键取到 None 的场景
_NULL_SORT_ROWS = [
    (701, "EP1", "", "", "2025-01-01", 0, 0, 900001, None, 0),
    (702, "EP2", "", "", "2025-01-08", 0, 0, 900001, 5, 0),
    (703, "EP3", "", "", "2025-01-15", 0, 0, 900001, 6, 0),
]

# subject 900002：多季合并到同一条目，sort 每季重置为 1（S1 ids 301-305，S2 ids 306-310）
_SORT_RESET_ROWS = [
    (301, "EP1", "", "", "2025-01-01", 0, 0, 900002, 1, 0),
    (302, "EP2", "", "", "2025-01-08", 0, 0, 900002, 2, 0),
    (303, "EP3", "", "", "2025-01-15", 0, 0, 900002, 3, 0),
    (304, "EP4", "", "", "2025-01-22", 0, 0, 900002, 4, 0),
    (305, "EP5", "", "", "2025-01-29", 0, 0, 900002, 5, 0),
    (306, "EP1", "", "", "2025-02-05", 0, 0, 900002, 1, 0),
    (307, "EP2", "", "", "2025-02-12", 0, 0, 900002, 2, 0),
    (308, "EP3", "", "", "2025-02-19", 0, 0, 900002, 3, 0),
    (309, "EP4", "", "", "2025-02-26", 0, 0, 900002, 4, 0),
    (310, "EP5", "", "", "2025-03-05", 0, 0, 900002, 5, 0),
]


def _archive_store(tmp_path: Path, rows: list[tuple], db_name: str):
    """用给定 episode 行建立临时 Archive 库，挂到全局单例并提供就绪的 ArchiveStore

    表结构与 Archive dump 一致（不含 ep 列）。teardown 时关闭连接并恢复全局单例状态。
    """
    db_path = tmp_path / db_name
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE episode ("
        "id INTEGER, name TEXT, name_cn TEXT, description TEXT, "
        "airdate TEXT, disc INTEGER, duration INTEGER, "
        "subject_id INTEGER, sort INTEGER, type INTEGER)"
    )
    conn.executemany(
        "INSERT INTO episode (id,name,name_cn,description,airdate,disc,"
        "duration,subject_id,sort,type) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    from app.utils.bangumi_archive import _archive

    orig_db_a = _archive.bangumi_archive.db_a_path
    orig_active = _archive.bangumi_archive._meta.active
    _archive.bangumi_archive.db_a_path = db_path
    _archive.bangumi_archive._meta.active = "a"

    store = ArchiveStore()
    yield store

    store.close()
    _archive.bangumi_archive.db_a_path = orig_db_a
    _archive.bangumi_archive._meta.active = orig_active


class TestArchiveEpisodeEpField:
    """ArchiveStore.get_episodes 应在数据边界补全季内话数 ep 字段

    Archive 按全局连续 sort 存储（如第二期 sort 13..24），不提供 API 中的季内集编号 ep。
    下游匹配层（episodes.py 的 _match_target_ep_rows / 连续编号季边界检测）依赖 ep 作为
    季内话数，因此必须在 get_episodes 返回前补全。
    """

    @pytest.fixture
    def store_with_episodes(self, tmp_path: Path) -> ArchiveStore:
        yield from _archive_store(tmp_path, _SEASON2_ROWS, "ep_synth.db")

    @pytest.fixture
    def store_with_null_sort(self, tmp_path: Path) -> ArchiveStore:
        yield from _archive_store(tmp_path, _NULL_SORT_ROWS, "null_sort.db")

    @pytest.fixture
    def store_with_sort_reset(self, tmp_path: Path) -> ArchiveStore:
        yield from _archive_store(tmp_path, _SORT_RESET_ROWS, "sort_reset.db")

    @pytest.fixture
    def store_empty(self, tmp_path: Path) -> ArchiveStore:
        yield from _archive_store(tmp_path, [], "empty.db")

    @pytest.fixture
    def store_only_sp(self, tmp_path: Path) -> ArchiveStore:
        rows = [(501, "SP", "", "", "2025-01-01", 0, 0, 900003, 1, 3)]
        yield from _archive_store(tmp_path, rows, "only_sp.db")

    @pytest.fixture
    def store_single_episode(self, tmp_path: Path) -> ArchiveStore:
        rows = [(601, "EP1", "", "", "2025-01-01", 0, 0, 900004, 7, 0)]
        yield from _archive_store(tmp_path, rows, "single.db")

    def test_ep_field_synthesized_in_sort_order(self, store_with_episodes):
        """type=0 常规话按 sort 升序补全 1-based 季内 ep（13..24 → 1..12）"""
        eps = store_with_episodes.get_episodes(517106)
        type0 = [e for e in eps if e["type"] == 0]
        assert [e["sort"] for e in type0] == list(range(13, 25))
        assert [e["ep"] for e in type0] == list(range(1, 13))

    def test_ep_field_absent_for_non_type0(self, store_with_episodes):
        """非 type=0（如外传 SP）不补全 ep 字段"""
        eps = store_with_episodes.get_episodes(517106)
        sp = [e for e in eps if e["type"] == 3]
        assert len(sp) == 1
        assert "ep" not in sp[0]

    def test_ep_field_with_type_filter(self, store_with_episodes):
        """episode_type=0 过滤时同样补全 ep"""
        eps = store_with_episodes.get_episodes(517106, episode_type=0)
        assert len(eps) == 12
        assert all(e.get("ep") for e in eps)
        assert [e["ep"] for e in eps] == list(range(1, 13))

    def test_ep_field_synthesized_when_sort_is_null(self, store_with_null_sort):
        """sort 为 NULL 的章节不得使补全抛异常，常规话照常补全 ep"""
        eps = store_with_null_sort.get_episodes(900001)
        assert len(eps) == 3
        assert [e["ep"] for e in eps] == [1, 2, 3]

    def test_ep_field_absent_when_sort_resets(self, store_with_sort_reset):
        """多季合并条目（sort 每季重置为 1）不补全 ep，季边界交由下游 sort 重置检测"""
        eps = store_with_sort_reset.get_episodes(900002)
        assert len(eps) == 10
        assert all("ep" not in e for e in eps)

    def test_ep_field_empty_subject_no_error(self, store_empty):
        """无章节的条目返回空列表，不补全也不报错"""
        assert store_empty.get_episodes(900003) == []

    def test_ep_field_only_non_type0_no_ep(self, store_only_sp):
        """条目内只有非本篇章节时不补全 ep"""
        eps = store_only_sp.get_episodes(900003)
        assert len(eps) == 1
        assert "ep" not in eps[0]

    def test_ep_field_single_episode_is_one(self, store_single_episode):
        """单集条目的季内话数为 1"""
        eps = store_single_episode.get_episodes(900004)
        assert len(eps) == 1
        assert eps[0]["ep"] == 1
