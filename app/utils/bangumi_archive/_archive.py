"""BangumiArchive 单例：协调下载、导入、查询的整体流程

职责：
- 读取配置初始化
- 维护双库 a/b 的 active 指针与 meta
- 调用 _download / _import 模块完成全量更新
- 维护导入任务进度（持久化缓存，刷新页面可恢复）
- 支持手动上传 zip 导入（跳过下载阶段）

进度模型：
- 每个 task 有唯一 task_id（按时间戳生成）
- 进度同时推送到 Queue（供 SSE 实时消费）和持久化到 _progress_cache
- 任务结束后保留最终进度，刷新页面仍可查询
- 同一时刻仅允许一个导入任务
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from ...core.config import config_manager
from ...core.logging import logger

# 双库名（互为备份，循环写入）
_DB_NAMES = ("a", "b")

# 磁盘空间阈值（MB），低于此值跳过导入
# 下载/上传/解压/导入全部在 data_dir/.tmp/ 完成，不再使用系统 temp
# 双库仅在导入时并存：active 库(0.8GB) + 新库(0.8GB)
# 解压后立即删除 zip，导入峰值 = active 库(0.8GB) + 解压(1GB) + 新库(0.8GB) + WAL 余量 ≈ 2.6GB
# 阈值设为 3000MB，预留约 400MB 余量避免导入中途写满
_DEFAULT_MIN_DISK_SPACE_MB = 3000

# 镜像源 fallback（与 upgrade_service 一致）
_GH_PROXY_MIRRORS = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
)

# latest.json 拉取地址
_LATEST_JSON_URL = (
    "https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json"
)

# 默认更新 cron：每周三 08:00（GMT+8），晚于官方 05:00 发布
_DEFAULT_UPDATE_CRON = "0 8 * * 3"

# 进度缓存保留时长（秒）：任务结束后保留 30 分钟
_PROGRESS_CACHE_TTL = 1800

# 进度历史日志最大长度：超过后丢弃最早的事件
# 避免 SSE 连接时 json.dumps 序列化巨大历史阻塞事件循环
_PROGRESS_LOG_MAX = 200

# 进度 Queue 最大长度：避免下载阶段推送过快导致无界堆积
_PROGRESS_QUEUE_MAX = 100


class ArchiveStage(str, Enum):
    """导入流程的阶段枚举（细粒度）"""

    IDLE = "idle"  # 空闲
    CHECKING = "checking"  # 检查磁盘空间
    FETCHING_LATEST = "fetching_latest"  # 拉取 latest.json
    SKIPPED = "skipped"  # dump 未更新，跳过
    DOWNLOADING = "downloading"  # 下载 zip
    DOWNLOAD_RETRY = "download_retry"  # 下载重试
    DOWNLOAD_SWITCH = "download_switch"  # 切换镜像源
    VERIFYING = "verifying"  # SHA256 校验
    EXTRACTING = "extracting"  # 解压
    IMPORTING = "importing"  # 导入数据
    IMPORT_TABLE = "import_table"  # 导入单表（message 含表名与进度）
    INDEXING = "indexing"  # 建立索引
    VACUUMING = "vacuuming"  # VACUUM 压缩
    SWITCHING = "switching"  # 切换 active 指针
    CLEANING = "cleaning"  # 清空旧库
    DONE = "done"  # 完成
    ERROR = "error"  # 失败
    # 上传导入专用
    UPLOAD_RECEIVED = "upload_received"  # 已接收上传文件


@dataclass
class ProgressEvent:
    """进度事件（持久化缓存结构）"""

    task_id: str
    stage: str  # ArchiveStage 值
    percent: int
    message: str
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class ArchiveMeta:
    """archive.meta 文件结构"""

    active: str = "a"
    last_import_at: Optional[str] = None
    last_import_duration_sec: Optional[float] = None
    dump_date: Optional[str] = None
    dump_filename: Optional[str] = None
    dump_size_bytes: Optional[int] = None
    row_counts: dict[str, int] = field(default_factory=dict)
    last_error: Optional[str] = None
    last_error_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "last_import_at": self.last_import_at,
            "last_import_duration_sec": self.last_import_duration_sec,
            "dump_date": self.dump_date,
            "dump_filename": self.dump_filename,
            "dump_size_bytes": self.dump_size_bytes,
            "row_counts": self.row_counts,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchiveMeta:
        return cls(
            active=data.get("active", "a"),
            last_import_at=data.get("last_import_at"),
            last_import_duration_sec=data.get("last_import_duration_sec"),
            dump_date=data.get("dump_date"),
            dump_filename=data.get("dump_filename"),
            dump_size_bytes=data.get("dump_size_bytes"),
            row_counts=data.get("row_counts", {}) or {},
            last_error=data.get("last_error"),
            last_error_at=data.get("last_error_at"),
        )


class BangumiArchive:
    """Bangumi Archive 离线查询层单例"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._load_config()
        self._meta: ArchiveMeta = self._load_meta()
        # 导入任务状态
        self._import_in_progress = False
        self._current_task_id: Optional[str] = None
        # 进度推送队列（按 task_id）：仅当前任务有活跃队列
        self._progress_queues: dict[str, asyncio.Queue] = {}
        # 进度持久化缓存（按 task_id）：保留最终进度供刷新页面查询
        self._progress_cache: dict[str, ProgressEvent] = {}
        # 进度历史日志（按 task_id）：所有阶段变化记录
        self._progress_logs: dict[str, list[ProgressEvent]] = {}
        # 启动时后台预构建标题索引（enabled 且 active DB 存在时）
        # 构建期间 try_search 自动降级到 API，构建完成后查询走 Archive
        self._maybe_start_background_index_build()

    def _maybe_start_background_index_build(self) -> None:
        """启动时若 Archive 已启用且 active DB 存在，初始化 FTS5 查询层连接

        FTS5 方案下无需后台预构建（表在导入时已建好），
        此处仅触发一次连接检查，让 is_ready 状态正确。
        """
        if not self.enabled:
            return
        try:
            active_db = self.get_active_db_path()
            if not active_db.exists():
                return

            def _trigger():
                try:
                    from ._title_index import archive_title_index

                    # FTS5 方案下为 no-op，仅触发连接检查
                    archive_title_index.build_in_background()
                except Exception as e:
                    logger.warning(f"bangumi_archive 启动时 FTS5 查询层初始化失败: {e}")

            # Timer(0, ...) 在新线程中执行，避免模块加载阶段循环 import
            timer = threading.Timer(0.0, _trigger)
            timer.daemon = True
            timer.start()
        except Exception as e:
            logger.warning(f"bangumi_archive 启动时 FTS5 查询层初始化失败: {e}")

    # ===== 配置 =====

    def _load_config(self) -> None:
        self.enabled = bool(
            config_manager.get("bangumi-archive", "enabled", fallback=False)
        )
        # 默认放到 ./data/archive 子目录，避免与 sync_records.db 等其他数据文件混杂
        self.data_dir = Path(
            config_manager.get("bangumi-archive", "data_dir", fallback="./data/archive")
        )
        self.http_proxy = (
            config_manager.get("bangumi-archive", "http_proxy", fallback="").strip()
            or config_manager.get("dev", "script_proxy", fallback="").strip()
            or None
        )
        self.ssl_verify = bool(
            config_manager.get("bangumi-archive", "ssl_verify", fallback=True)
        )
        self.update_cron = (
            config_manager.get("bangumi-archive", "update_cron", fallback="")
            or _DEFAULT_UPDATE_CRON
        )
        self.min_disk_space_mb = int(
            config_manager.get(
                "bangumi-archive",
                "min_disk_space_mb",
                fallback=_DEFAULT_MIN_DISK_SPACE_MB,
            )
        )
        self.retry_interval = int(
            config_manager.get("bangumi-archive", "retry_interval", fallback=3600)
        )

        # 兼容迁移：用户 config.ini 可能显式指定旧路径 ./data，
        # 但实际数据库已迁移到 ./data/archive 子目录。
        # 检测策略：配置路径下找不到 db 文件，但 archive 子目录下有，则使用新路径。
        # 仅在用户显式指定 ./data 或类似父目录时触发，避免误改其他自定义路径。
        if (
            not (self.data_dir / "bangumi_archive_a.db").exists()
            and not (self.data_dir / "bangumi_archive_b.db").exists()
        ):
            archive_subdir = self.data_dir / "archive"
            if (archive_subdir / "bangumi_archive_a.db").exists() or (
                archive_subdir / "bangumi_archive_b.db"
            ).exists():
                logger.info(
                    f"bangumi_archive: 检测到数据目录已迁移到 {archive_subdir}，"
                    f"自动切换（配置值: {self.data_dir}）"
                )
                self.data_dir = archive_subdir

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_a_path = self.data_dir / "bangumi_archive_a.db"
        self.db_b_path = self.data_dir / "bangumi_archive_b.db"
        self.active_file = self.data_dir / "bangumi_archive.active"
        self.meta_file = self.data_dir / "bangumi_archive.meta"

    def reload_config(self) -> None:
        with self._lock:
            self._load_config()
        # 配置变更后（如从 disabled 切换到 enabled）触发后台构建
        self._maybe_start_background_index_build()
        # 同步刷新 archive_shortcut 的 enabled 状态
        # 避免 bangumi_archive.enabled=True 但 archive_shortcut._enabled=False
        # 导致所有 try_* 短路返回 archive_disabled 的问题
        try:
            from ..bangumi_api._archive_shortcut import archive_shortcut

            archive_shortcut.reload_config()
        except Exception as e:
            logger.warning(f"bangumi_archive 同步刷新 archive_shortcut 配置失败: {e}")

    # ===== meta 与 active 指针 =====

    def _load_meta(self) -> ArchiveMeta:
        if not self.meta_file.exists():
            return ArchiveMeta(active=self._read_active_file())
        try:
            with open(self.meta_file, encoding="utf-8") as f:
                data = json.load(f)
            return ArchiveMeta.from_dict(data)
        except (OSError, ValueError) as e:
            logger.warning(f"bangumi_archive meta 文件损坏，使用默认值: {e}")
            return ArchiveMeta(active=self._read_active_file())

    def _read_active_file(self) -> str:
        if not self.active_file.exists():
            logger.info("bangumi_archive 首次启动，初始化 active='a'")
            self._write_active_file("a")
            return "a"
        try:
            content = self.active_file.read_text(encoding="utf-8").strip()
            if content in _DB_NAMES:
                return content
            logger.warning(f"bangumi_archive.active 内容非法: {content!r}，重置为 'a'")
            self._write_active_file("a")
            return "a"
        except OSError as e:
            logger.warning(f"读取 active 文件失败: {e}，使用默认 'a'")
            return "a"

    def _write_active_file(self, name: str) -> None:
        tmp = self.active_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(name)
        os.replace(str(tmp), str(self.active_file))

    def _save_meta(self, meta: ArchiveMeta) -> None:
        tmp = self.meta_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(str(tmp), str(self.meta_file))

    def get_active_db_path(self) -> Path:
        return self.db_a_path if self._meta.active == "a" else self.db_b_path

    def get_inactive_db_path(self) -> Path:
        return self.db_b_path if self._meta.active == "a" else self.db_a_path

    def get_tmp_dir(self) -> Path:
        """获取临时工作目录（下载/上传/解压统一在此完成）

        方案 B：所有临时文件都放在 data_dir/.tmp/ 下，与数据库同磁盘，
        避免系统 temp 与 data_dir 跨磁盘导致的空间检查盲区。
        调用方负责清理自己创建的子目录。
        """
        tmp_dir = self.data_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir

    def get_meta(self) -> ArchiveMeta:
        with self._lock:
            return ArchiveMeta(
                active=self._meta.active,
                last_import_at=self._meta.last_import_at,
                last_import_duration_sec=self._meta.last_import_duration_sec,
                dump_date=self._meta.dump_date,
                dump_filename=self._meta.dump_filename,
                dump_size_bytes=self._meta.dump_size_bytes,
                row_counts=dict(self._meta.row_counts),
                last_error=self._meta.last_error,
                last_error_at=self._meta.last_error_at,
            )

    # ===== 状态查询 =====

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            meta = self._meta
            current_task_id = self._current_task_id
            import_in_progress = self._import_in_progress
        active_path = self.get_active_db_path()
        db_size = active_path.stat().st_size if active_path.exists() else 0
        # 附加当前进度（若有）
        current_progress: Optional[dict[str, Any]] = None
        if current_task_id:
            cache = self._progress_cache.get(current_task_id)
            if cache:
                current_progress = cache.to_dict()
        return {
            "enabled": self.enabled,
            "active": meta.active,
            "active_db_path": str(active_path),
            "db_size_bytes": db_size,
            "last_import_at": meta.last_import_at,
            "last_import_duration_sec": meta.last_import_duration_sec,
            "dump_date": meta.dump_date,
            "dump_filename": meta.dump_filename,
            "dump_size_bytes": meta.dump_size_bytes,
            "row_counts": dict(meta.row_counts),
            "last_error": meta.last_error,
            "last_error_at": meta.last_error_at,
            "import_in_progress": import_in_progress,
            "current_task_id": current_task_id,
            "current_progress": current_progress,
            "update_cron": self.update_cron,
            "data_dir": str(self.data_dir),
        }

    # ===== 进度推送与缓存 =====

    @property
    def is_import_in_progress(self) -> bool:
        return self._import_in_progress

    @property
    def current_task_id(self) -> Optional[str]:
        return self._current_task_id

    def _create_progress_queue(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_PROGRESS_QUEUE_MAX)
        self._progress_queues[task_id] = queue
        return queue

    def get_progress_queue(self, task_id: str) -> Optional[asyncio.Queue]:
        return self._progress_queues.get(task_id)

    def _cleanup_progress_queue(self, task_id: str) -> None:
        self._progress_queues.pop(task_id, None)

    def get_cached_progress(self, task_id: str) -> Optional[ProgressEvent]:
        """获取任务最近一次进度（持久化缓存，刷新页面可恢复）"""
        return self._progress_cache.get(task_id)

    def get_progress_log(self, task_id: str) -> list[dict[str, Any]]:
        """获取任务进度历史日志"""
        events = self._progress_logs.get(task_id, [])
        return [e.to_dict() for e in events]

    def _push_progress(
        self,
        task_id: str,
        stage: ArchiveStage,
        percent: int,
        message: str,
        error: Optional[str] = None,
    ) -> None:
        """推送进度：同时写入缓存、历史日志、Queue（供 SSE 实时消费）"""
        event = ProgressEvent(
            task_id=task_id,
            stage=stage.value,
            percent=percent,
            message=message,
            error=error,
        )
        # 持久化缓存（覆盖式：保留最新）
        self._progress_cache[task_id] = event
        # 历史日志（追加，限制最大长度避免 SSE 序列化巨大历史）
        logs = self._progress_logs.setdefault(task_id, [])
        logs.append(event)
        if len(logs) > _PROGRESS_LOG_MAX:
            del logs[: len(logs) - _PROGRESS_LOG_MAX]
        # 实时队列
        queue = self._progress_queues.get(task_id)
        if queue is not None:
            try:
                queue.put_nowait(event.to_dict())
            except asyncio.QueueFull:
                # 队列满（SSE 消费慢），丢弃最旧事件腾出空间
                try:
                    queue.get_nowait()
                    queue.put_nowait(event.to_dict())
                except asyncio.QueueEmpty:
                    pass

    # ===== 更新流程入口 =====

    async def run_update(self, force: bool = False) -> str:
        """执行一次完整更新流程：下载 → 校验 → 导入 → 切换 → 清空旧库

        Returns:
            task_id
        """
        task_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._create_progress_queue(task_id)

        with self._lock:
            if self._import_in_progress:
                self._cleanup_progress_queue(task_id)
                raise RuntimeError("已有导入任务进行中")
            self._import_in_progress = True
            self._current_task_id = task_id

        try:
            await self._do_update(task_id, force=force)
            self._push_progress(task_id, ArchiveStage.DONE, 100, "更新完成")
            return task_id
        except Exception as e:
            logger.error(f"bangumi_archive 更新失败: {e}")
            self._push_progress(
                task_id, ArchiveStage.ERROR, 100, f"更新失败: {e}", error=str(e)
            )
            with self._lock:
                self._meta.last_error = str(e)
                self._meta.last_error_at = datetime.now(timezone.utc).isoformat()
                self._save_meta(self._meta)
            try:
                from ...services.notification_service import notification_service

                notification_service.notify(
                    "archive_build_failed",
                    source="bangumi-archive",
                    error_message=str(e),
                    task_id=task_id,
                )
            except Exception:
                pass
            raise
        finally:
            with self._lock:
                self._import_in_progress = False
                self._current_task_id = None
            # 保留 Queue 5 分钟供 SSE 消费
            asyncio.get_event_loop().call_later(
                300, lambda: self._cleanup_progress_queue(task_id)
            )
            # 缓存与日志保留 30 分钟后清理
            asyncio.get_event_loop().call_later(
                _PROGRESS_CACHE_TTL,
                lambda: (
                    self._progress_cache.pop(task_id, None),
                    self._progress_logs.pop(task_id, None),
                ),
            )

    async def import_local_zip(self, zip_path: Path) -> str:
        """从本地 zip 文件导入（用户手动下载后上传）

        流程：跳过下载阶段，直接走 解压 → 导入 → 切换 → 清空

        Args:
            zip_path: 用户上传的 zip 文件路径（临时目录）

        Returns:
            task_id
        """
        task_id = "local_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self._create_progress_queue(task_id)

        with self._lock:
            if self._import_in_progress:
                self._cleanup_progress_queue(task_id)
                raise RuntimeError("已有导入任务进行中")
            self._import_in_progress = True
            self._current_task_id = task_id

        try:
            self._push_progress(
                task_id,
                ArchiveStage.UPLOAD_RECEIVED,
                5,
                f"已接收上传文件: {zip_path.name}",
            )
            # 上传流程同样检查磁盘空间，与下载流程保持一致
            # 避免解压/导入阶段磁盘写满导致中途失败
            # 注意：API 层在保存文件前已做一次提前检查，这里作为解压导入前的
            # 最后防线（从 API 调用到此处可能间隔数秒，磁盘可能被其他进程占用）
            self._push_progress(task_id, ArchiveStage.CHECKING, 1, "检查磁盘空间")
            self.check_disk_space()
            await self._do_import(
                task_id=task_id,
                zip_path=zip_path,
                dump_date=None,  # 本地上传无 dump_date
                dump_filename=zip_path.name,
                dump_size_bytes=zip_path.stat().st_size if zip_path.exists() else 0,
                cleanup_zip=False,  # 上传的临时文件由调用方清理
            )
            self._push_progress(task_id, ArchiveStage.DONE, 100, "导入完成")
            return task_id
        except Exception as e:
            logger.error(f"bangumi_archive 本地导入失败: {e}")
            self._push_progress(
                task_id, ArchiveStage.ERROR, 100, f"导入失败: {e}", error=str(e)
            )
            with self._lock:
                self._meta.last_error = str(e)
                self._meta.last_error_at = datetime.now(timezone.utc).isoformat()
                self._save_meta(self._meta)
            raise
        finally:
            with self._lock:
                self._import_in_progress = False
                self._current_task_id = None
            asyncio.get_event_loop().call_later(
                300, lambda: self._cleanup_progress_queue(task_id)
            )
            asyncio.get_event_loop().call_later(
                _PROGRESS_CACHE_TTL,
                lambda: (
                    self._progress_cache.pop(task_id, None),
                    self._progress_logs.pop(task_id, None),
                ),
            )

    async def _do_update(self, task_id: str, force: bool = False) -> None:
        """完整更新流程（含下载）"""
        from ._download import ArchiveDownloader

        # 1. 检查磁盘空间
        self._push_progress(task_id, ArchiveStage.CHECKING, 1, "检查磁盘空间")
        self.check_disk_space()

        # 2. 拉取 latest.json
        self._push_progress(
            task_id, ArchiveStage.FETCHING_LATEST, 3, "拉取 latest.json"
        )
        downloader = ArchiveDownloader(
            http_proxy=self.http_proxy,
            ssl_verify=self.ssl_verify,
            mirrors=_GH_PROXY_MIRRORS,
            progress_cb=self._make_progress_cb(task_id),
        )
        latest = await downloader.fetch_latest(_LATEST_JSON_URL)

        # 3. 对比 dump_date 决定是否跳过
        with self._lock:
            current_dump = self._meta.dump_date
        if not force and current_dump and latest.get("created_at") == current_dump:
            logger.info(f"bangumi_archive: dump_date 未变化 ({current_dump})，跳过更新")
            self._push_progress(
                task_id,
                ArchiveStage.SKIPPED,
                100,
                f"dump 未更新 ({current_dump})，跳过",
            )
            return

        # 4. 下载 zip
        self._push_progress(
            task_id,
            ArchiveStage.DOWNLOADING,
            10,
            f"开始下载 {latest.get('name', 'archive.zip')}",
        )
        zip_path = await downloader.download(
            latest, task_id=task_id, tmp_dir=self.get_tmp_dir()
        )

        # 4.5 SHA256 校验
        digest = latest.get("digest", "")
        if digest:
            await downloader.verify_sha256(zip_path, digest, task_id=task_id)

        try:
            await self._do_import(
                task_id=task_id,
                zip_path=zip_path,
                dump_date=latest.get("created_at"),
                dump_filename=latest.get("name"),
                dump_size_bytes=latest.get("size"),
                cleanup_zip=True,  # 下载的 zip 导入后删除
            )
        finally:
            # 兜底清理：整个任务临时子目录（含 zip 残留 + 解压目录）
            # zip 与解压目录都在 .tmp/<task_dir>/ 下
            try:
                task_tmp_dir = zip_path.parent
                if task_tmp_dir.exists() and task_tmp_dir != self.get_tmp_dir():
                    shutil.rmtree(task_tmp_dir, ignore_errors=True)
            except OSError:
                pass

    async def _do_import(
        self,
        task_id: str,
        zip_path: Path,
        dump_date: Optional[str],
        dump_filename: str,
        dump_size_bytes: int,
        cleanup_zip: bool,
    ) -> None:
        """导入流程：解压 → 导入 → 切换 → 清空（不含下载阶段，可被本地导入复用）"""
        from ._import import ArchiveImporter

        # SHA256 校验（本地上传跳过，因无 digest）
        if dump_date is not None:
            # 走下载流程时 SHA256 已在 download 阶段校验完成，此处仅推送进度
            self._push_progress(task_id, ArchiveStage.VERIFYING, 60, "SHA256 校验通过")

        # 解压 + 导入到 inactive 库
        target_db = self.get_inactive_db_path()
        importer = ArchiveImporter()
        row_counts, duration_sec = await importer.import_all(
            zip_path=zip_path,
            target_db=target_db,
            task_id=task_id,
            progress_cb=self._make_import_progress_cb(task_id),
        )

        # 校验：行数非零
        self._validate_row_counts(row_counts)

        # 切换 active 指针
        new_active = "b" if self._meta.active == "a" else "a"
        self._push_progress(
            task_id, ArchiveStage.SWITCHING, 95, f"切换 active 指针到 {new_active}"
        )
        with self._lock:
            self._meta.active = new_active
            self._meta.last_import_at = datetime.now(timezone.utc).isoformat()
            self._meta.last_import_duration_sec = duration_sec
            self._meta.dump_date = dump_date
            self._meta.dump_filename = dump_filename
            self._meta.dump_size_bytes = dump_size_bytes
            self._meta.row_counts = row_counts
            self._meta.last_error = None
            self._meta.last_error_at = None
            self._write_active_file(new_active)
            self._save_meta(self._meta)

        # 先 invalidate FTS5 查询层，断开与旧 active 库的连接
        # 否则 Windows 上 clear_database 删除旧库会因文件被占用失败 (WinError 32)
        try:
            from ._title_index import archive_title_index

            archive_title_index.invalidate()
        except Exception as e:
            logger.warning(f"bangumi_archive FTS5 查询层 invalidate 失败: {e}")

        # 清空旧库（FTS5 连接已断开，可安全删除）
        old_db = self.get_inactive_db_path()
        self._push_progress(
            task_id, ArchiveStage.CLEANING, 98, f"清空旧库 {old_db.name}"
        )
        importer.clear_database(old_db)

        logger.info(
            f"bangumi_archive 导入完成: active={new_active}, "
            f"rows={row_counts}, duration={duration_sec:.1f}s"
        )

        # 重建 FTS5 查询层连接到新 active 库
        # FTS5 表在导入时已构建，无需后台重建，仅需重连
        # 用 to_thread 包裹避免 _ensure_built 同步阻塞事件循环导致前端白屏
        try:
            await asyncio.to_thread(archive_title_index.build_in_background)
        except Exception as e:
            logger.warning(f"bangumi_archive FTS5 查询层重连失败: {e}")

        # zip 已在 import_all 解压后立即删除，此处无需再清理

    def _make_progress_cb(
        self, task_id: str
    ) -> Callable[[str, ArchiveStage, int, str], None]:
        """下载阶段进度回调（绑定 task_id）"""

        def cb(tid: str, stage: ArchiveStage, percent: int, message: str) -> None:
            self._push_progress(task_id, stage, percent, message)

        return cb

    def _make_import_progress_cb(
        self, task_id: str
    ) -> Callable[[str, str, int, str], None]:
        """导入阶段进度回调（绑定 task_id，使用字符串 stage 兼容 _import.py）"""

        def cb(tid: str, stage: str, percent: int, message: str) -> None:
            # 映射字符串到 ArchiveStage
            try:
                enum_stage = ArchiveStage(stage)
            except ValueError:
                enum_stage = ArchiveStage.IMPORTING
            self._push_progress(task_id, enum_stage, percent, message)

        return cb

    def check_disk_space(self) -> None:
        """检查 data_dir 所在磁盘可用空间是否足够导入

        公开方法，供 API 层在上传文件保存前提前调用，与下载流程对称。
        内部流程（_do_update / import_local_zip）在解压导入前也会调用，
        作为最后一道防线。
        """
        required_mb = self.min_disk_space_mb
        try:
            usage = shutil.disk_usage(str(self.data_dir))
            available_mb = usage.free // (1024 * 1024)
            # 磁盘预警：可用空间低于阈值的 1.5 倍时触发（仍允许流程继续）
            warning_mb = int(required_mb * 1.5)
            if available_mb >= required_mb and available_mb < warning_mb:
                try:
                    from ...services.notification_service import notification_service

                    notification_service.notify(
                        "archive_disk_warning",
                        source="bangumi-archive",
                        available_mb=available_mb,
                        required_mb=required_mb,
                        warning_threshold_mb=warning_mb,
                    )
                except Exception:
                    pass
            if available_mb < required_mb:
                raise RuntimeError(
                    f"磁盘空间不足: 需要 {required_mb}MB, 可用 {available_mb}MB"
                )
            logger.debug(
                f"bangumi_archive 磁盘空间检查: 可用 {available_mb}MB >= {required_mb}MB"
            )
        except OSError as e:
            raise RuntimeError(f"检查磁盘空间失败: {e}") from e

    def _validate_row_counts(self, row_counts: dict[str, int]) -> None:
        subject_count = row_counts.get("subject", 0)
        if subject_count == 0:
            raise RuntimeError("导入校验失败: subject 表行数为 0")
        episode_count = row_counts.get("episode", 0)
        if episode_count == 0:
            raise RuntimeError("导入校验失败: episode 表行数为 0")
        logger.info(
            f"bangumi_archive 行数校验通过: subject={subject_count}, episode={episode_count}"
        )


# 全局单例
bangumi_archive = BangumiArchive()
