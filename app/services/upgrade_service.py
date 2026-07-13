"""
一键升级服务

仅支持直装模式（非 Docker）。Docker 用户需手动升级。
"""

# ruff: noqa: UP045 — 兼容 Python 3.9

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx

from ..core.app_version import get_version
from ..core.config import config_manager
from ..core.database import database_manager
from ..core.logging import logger
from ..utils.docker_helper import docker_helper

DOWNLOAD_URL = "https://github.com/SanaeMio/Bangumi-syncer/releases/latest/download/Bangumi-syncer.zip"
DOWNLOAD_TIMEOUT = 300.0
DOWNLOAD_MAX_RETRIES = 3
KEEP_BACKUPS = 3

# 公共 GitHub 反代，用于国内网络环境下载失败时的备选源
_GH_PROXY_MIRRORS = [
    "https://ghfast.top/",
    "https://gh-proxy.com/",
]


class UpgradeStage(str, Enum):
    DOWNLOADING = "downloading"
    BACKING_UP = "backing_up"
    APPLYING = "applying"
    INSTALLING_DEPS = "installing_deps"
    RESTARTING = "restarting"
    DONE = "done"
    ERROR = "error"


@dataclass
class UpgradeProgress:
    upgrade_id: str
    stage: UpgradeStage = UpgradeStage.DOWNLOADING
    percent: int = 0
    message: str = ""
    error: Optional[str] = None


class UpgradeService:
    def __init__(self):
        self._progress: dict[str, UpgradeProgress] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
        self._in_progress = False

    @property
    def is_upgrade_in_progress(self) -> bool:
        return self._in_progress

    def is_upgrade_capable(self) -> bool:
        """检查是否支持一键升级（仅直装模式）"""
        if docker_helper.is_docker:
            return False
        # 检查 app/ 目录是否可写
        app_dir = Path("app")
        return app_dir.is_dir() and os.access(str(app_dir), os.W_OK)

    def get_progress(self, upgrade_id: str) -> Optional[UpgradeProgress]:
        return self._progress.get(upgrade_id)

    def get_progress_queue(self, upgrade_id: str) -> Optional[asyncio.Queue]:
        return self._queues.get(upgrade_id)

    def _cleanup_queue(self, upgrade_id: str):
        self._queues.pop(upgrade_id, None)

    def _get_proxy(self) -> Optional[str]:
        """获取代理配置"""
        proxy = config_manager.get("dev", "script_proxy", fallback="")
        return proxy if proxy else None

    @staticmethod
    def _fmt_size(size_bytes: int) -> str:
        """格式化文件大小，自动选择 KB 或 MB"""
        if size_bytes >= 1024 * 1024:
            return f"{size_bytes // 1024 // 1024}MB"
        return f"{size_bytes // 1024}KB"

    def _update_progress(
        self,
        upgrade_id: str,
        stage: UpgradeStage,
        percent: int = 0,
        message: str = "",
        error: Optional[str] = None,
    ):
        p = self._progress.get(upgrade_id)
        if p:
            p.stage = stage
            p.percent = percent
            p.message = message
            p.error = error

        q = self._queues.get(upgrade_id)
        if q:
            try:
                q.put_nowait(
                    UpgradeProgress(
                        upgrade_id=upgrade_id,
                        stage=stage,
                        percent=percent,
                        message=message,
                        error=error,
                    )
                )
            except asyncio.QueueFull:
                pass

    async def start_upgrade(self, target_version: Optional[str] = None) -> str:
        """启动升级任务，返回 upgrade_id"""
        if self._in_progress:
            raise RuntimeError("已有升级任务进行中")

        import uuid

        upgrade_id = str(uuid.uuid4())[:8]
        self._progress[upgrade_id] = UpgradeProgress(
            upgrade_id=upgrade_id,
            message="准备开始升级...",
        )
        self._in_progress = True

        current = get_version()
        logger.info(
            f"[升级] 启动升级任务 {upgrade_id}，当前版本: {current}，目标版本: {target_version or 'latest'}"
        )

        self._queues[upgrade_id] = asyncio.Queue(maxsize=100)
        asyncio.create_task(self._run_upgrade(upgrade_id, target_version))
        return upgrade_id

    async def _run_upgrade(self, upgrade_id: str, target_version: Optional[str]):
        temp_dir = Path("data/upgrade_temp")
        backup_dir = Path("backups")
        app_backup_dir: Optional[Path] = None
        replace_dirs = ["app", "templates", "static"]

        logger.info(f"[升级] {upgrade_id} 开始执行升级流程")
        full_backup_dir: Optional[Path] = None

        try:
            # 1. 下载
            logger.info(f"[升级] {upgrade_id} 阶段 1/4: 下载新版本")
            self._update_progress(
                upgrade_id, UpgradeStage.DOWNLOADING, 5, "正在连接下载源..."
            )
            zip_path = await self._download_zip(upgrade_id, temp_dir)
            size_str = (
                self._fmt_size(zip_path.stat().st_size) if zip_path.exists() else "?"
            )
            logger.info(f"[升级] {upgrade_id} 下载完成: {zip_path} ({size_str})")
            self._update_progress(
                upgrade_id, UpgradeStage.DOWNLOADING, 55, f"下载完成 ({size_str})"
            )

            # 2. 备份数据库和关键文件
            logger.info(f"[升级] {upgrade_id} 阶段 2/4: 备份数据库和关键文件")
            self._update_progress(
                upgrade_id, UpgradeStage.BACKING_UP, 60, "正在备份数据库和关键文件..."
            )
            full_backup_dir = self._backup_all(backup_dir, upgrade_id)
            await asyncio.sleep(0.5)

            # 3. 替换文件
            logger.info(f"[升级] {upgrade_id} 阶段 3/4: 替换应用文件")
            self._update_progress(
                upgrade_id, UpgradeStage.APPLYING, 70, "正在替换应用文件..."
            )
            app_backup_dir = self._apply_update(zip_path, temp_dir, upgrade_id)
            logger.info(f"[升级] {upgrade_id} 文件替换完成")
            await asyncio.sleep(0.5)

            # 4. 安装依赖
            logger.info(f"[升级] {upgrade_id} 阶段 4/4: 安装依赖")
            self._update_progress(
                upgrade_id,
                UpgradeStage.INSTALLING_DEPS,
                90,
                "正在安装依赖（可能需要几分钟）...",
            )
            # 在线程池中执行，避免 subprocess.run 阻塞事件循环导致 SSE 推送中断
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._install_deps)
            logger.info(f"[升级] {upgrade_id} 依赖安装完成")
            self._update_progress(
                upgrade_id, UpgradeStage.INSTALLING_DEPS, 95, "依赖安装完成"
            )

            # 5. 清理旧备份
            self._cleanup_old_backups(backup_dir, KEEP_BACKUPS)

            # 6. 清理临时文件
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

            new_ver = get_version()
            logger.info(f"[升级] {upgrade_id} 升级流程全部完成，当前版本: {new_ver}")
            self._update_progress(
                upgrade_id, UpgradeStage.DONE, 100, "升级完成，即将重启..."
            )

        except Exception as e:
            logger.error(f"[升级] {upgrade_id} 升级失败: {e}")
            self._update_progress(
                upgrade_id, UpgradeStage.ERROR, 0, "升级失败", error=str(e)
            )
            # 回滚数据库
            if full_backup_dir and full_backup_dir.exists():
                self._restore_database(full_backup_dir)
            # 回滚已替换的文件
            if app_backup_dir and app_backup_dir.exists():
                logger.warning(f"[升级] {upgrade_id} 正在回滚应用文件...")
                self._rollback_files(app_backup_dir, replace_dirs)
            # 尝试清理临时文件
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        finally:
            self._in_progress = False
            self._cleanup_queue(upgrade_id)

    async def _download_zip(self, upgrade_id: str, temp_dir: Path) -> Path:
        """下载 release zip，带重试和镜像回退机制"""
        temp_dir.mkdir(parents=True, exist_ok=True)
        zip_path = temp_dir / "Bangumi-syncer.zip"

        proxy = self._get_proxy()
        if proxy:
            logger.info(f"[升级] 使用代理: {proxy}")

        # 构建下载源列表：镜像源优先，GitHub 兜底
        urls = [m + DOWNLOAD_URL for m in _GH_PROXY_MIRRORS] + [DOWNLOAD_URL]

        for url_idx, url in enumerate(urls):
            source_label = (
                "GitHub" if url_idx == len(urls) - 1 else f"镜像{url_idx + 1}"
            )

            for attempt in range(DOWNLOAD_MAX_RETRIES):
                if attempt > 0:
                    wait = min(2**attempt, 10)
                    logger.warning(
                        f"[升级] {source_label}下载失败，{wait}秒后重试（第{attempt + 1}/{DOWNLOAD_MAX_RETRIES}次）"
                    )
                    self._update_progress(
                        upgrade_id,
                        UpgradeStage.DOWNLOADING,
                        10,
                        f"{source_label}下载失败，{wait} 秒后重试（第 {attempt + 1}/{DOWNLOAD_MAX_RETRIES} 次）...",
                    )
                    await asyncio.sleep(wait)
                elif url_idx > 0:
                    logger.info(f"[升级] 主源下载失败，切换到{source_label}: {url}")
                    self._update_progress(
                        upgrade_id,
                        UpgradeStage.DOWNLOADING,
                        10,
                        f"正在尝试从{source_label}下载...",
                    )

                try:
                    async with httpx.AsyncClient(
                        timeout=DOWNLOAD_TIMEOUT,
                        proxy=proxy,
                        follow_redirects=True,
                    ) as client:
                        async with client.stream("GET", url) as resp:
                            if resp.status_code != 200:
                                raise RuntimeError(f"下载失败: HTTP {resp.status_code}")

                            total = int(resp.headers.get("content-length", 0))
                            downloaded = 0
                            last_reported = 0

                            with open(zip_path, "wb") as f:
                                async for chunk in resp.aiter_bytes(chunk_size=8192):
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    # 每下载 512KB 或有总量时按百分比更新
                                    if total > 0:
                                        pct = 5 + int(downloaded / total * 50)
                                        self._update_progress(
                                            upgrade_id,
                                            UpgradeStage.DOWNLOADING,
                                            pct,
                                            f"正在下载... {self._fmt_size(downloaded)} / {self._fmt_size(total)}",
                                        )
                                    elif downloaded - last_reported >= 512 * 1024:
                                        last_reported = downloaded
                                        self._update_progress(
                                            upgrade_id,
                                            UpgradeStage.DOWNLOADING,
                                            30,
                                            f"正在下载... {self._fmt_size(downloaded)}",
                                        )

                    logger.info(
                        f"[升级] 从{source_label}下载成功，文件大小: {zip_path.stat().st_size // 1024}KB"
                    )
                    return zip_path

                except httpx.TimeoutException:
                    logger.warning(f"[升级] {source_label}下载超时")
                except httpx.RequestError as e:
                    logger.warning(f"[升级] {source_label}下载连接失败: {e}")

        proxy_hint = ""
        if not proxy:
            proxy_hint = "。若网络受限，请在配置文件 [dev] script_proxy 中设置代理地址"

        msg = f"下载失败（已尝试 {len(urls)} 个源，每个重试 {DOWNLOAD_MAX_RETRIES} 次）{proxy_hint}"
        logger.error(f"[升级] {msg}")
        raise RuntimeError(msg)

    def _backup_all(self, backup_dir: Path, upgrade_id: str = "") -> Path:
        """备份数据库和关键应用文件到同一个目录，返回备份目录路径"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = backup_dir / f"backup_{timestamp}"
        dest.mkdir(parents=True, exist_ok=True)
        backed_up = []

        # 备份数据库
        db_path = database_manager.db_path
        if db_path.exists():
            try:
                db_backup_path = dest / "sync_records.db"
                src_conn = sqlite3.connect(str(db_path))
                dst_conn = sqlite3.connect(str(db_backup_path))
                src_conn.backup(dst_conn)
                dst_conn.close()
                src_conn.close()
                backed_up.append("sync_records.db")
                logger.info(f"数据库已备份到: {db_backup_path}")
                if upgrade_id:
                    self._update_progress(
                        upgrade_id, UpgradeStage.BACKING_UP, 61, "数据库备份完成"
                    )
            except Exception as e:
                raise RuntimeError(f"数据库备份失败: {e}")

        # 备份单个文件
        for name in self._BACKUP_FILES:
            src = Path(name)
            if src.exists():
                shutil.copy2(src, dest / name)
                backed_up.append(name)

        # 备份目录
        _ignore = shutil.ignore_patterns("__pycache__")
        for d in self._BACKUP_DIRS:
            src = Path(d)
            if src.is_dir():
                shutil.copytree(src, dest / d, dirs_exist_ok=True, ignore=_ignore)
                backed_up.append(f"{d}/")

        if backed_up:
            logger.info(f"已备份: {', '.join(backed_up)}")
            if upgrade_id:
                self._update_progress(
                    upgrade_id,
                    UpgradeStage.BACKING_UP,
                    65,
                    f"已备份 {len(backed_up)} 项",
                )

        return dest

    def _restore_database(self, backup_dir: Path):
        """从备份目录恢复数据库"""
        db_backup = backup_dir / "sync_records.db"
        if not db_backup.exists():
            return

        db_path = database_manager.db_path
        try:
            # 关闭现有连接
            database_manager.close()
        except Exception as e:
            logger.debug("database_manager.close 失败: %s", e)

        try:
            shutil.copy2(db_backup, db_path)
            logger.info(f"数据库已从备份恢复: {db_backup}")
        except Exception as e:
            logger.error(f"数据库恢复失败: {e}")

    # 需要持久化备份的关键文件（升级后可能被覆盖）
    _BACKUP_FILES = [
        "release_manifest.json",
        "requirements.txt",
        "pyproject.toml",
        "uv.lock",
        "start.bat",
        "config.ini",
        "config.dev.ini",
        "bangumi_mapping.json",
    ]

    # 需要持久化备份的目录
    _BACKUP_DIRS = ["app", "templates", "static"]

    def _apply_update(
        self, zip_path: Path, temp_dir: Path, upgrade_id: str = ""
    ) -> Path:
        """解压 zip 并替换应用文件，返回备份目录路径（用于回滚）"""
        extract_dir = temp_dir / "extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)

        logger.info(f"[升级] 正在解压 {zip_path.name}")
        if upgrade_id:
            self._update_progress(
                upgrade_id, UpgradeStage.APPLYING, 70, f"正在解压 {zip_path.name}..."
            )
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            logger.error("[升级] 下载的 zip 文件损坏")
            raise RuntimeError("下载的文件损坏，请重试")

        # 替换目录列表
        replace_dirs = ["app", "templates", "static"]
        replace_files = [
            "release_manifest.json",
            "requirements.txt",
            "pyproject.toml",
            "uv.lock",
            "start.bat",
        ]

        project_root = Path(".")

        # 备份当前应用文件（用于回滚，排除 __pycache__）
        app_backup_dir = temp_dir / "app_backup"
        app_backup_dir.mkdir(exist_ok=True)
        _ignore = shutil.ignore_patterns("__pycache__")

        for d in replace_dirs:
            src = project_root / d
            if src.exists():
                shutil.copytree(
                    src, app_backup_dir / d, dirs_exist_ok=True, ignore=_ignore
                )

        try:
            # 替换目录
            dir_progress = {"app": 72, "templates": 74, "static": 76}
            for d in replace_dirs:
                src = extract_dir / d
                dst = project_root / d
                if src.exists():
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    logger.info(f"[升级] 已替换目录: {d}/")
                    if upgrade_id:
                        self._update_progress(
                            upgrade_id,
                            UpgradeStage.APPLYING,
                            dir_progress.get(d, 75),
                            f"已替换 {d}/",
                        )

            # 替换文件
            for f in replace_files:
                src = extract_dir / f
                if src.exists():
                    shutil.copy2(src, project_root / f)
                    logger.info(f"[升级] 已替换文件: {f}")

            if upgrade_id:
                self._update_progress(
                    upgrade_id, UpgradeStage.APPLYING, 78, "文件替换完成"
                )

        except Exception as e:
            # 回滚
            logger.error(f"[升级] 文件替换失败，正在回滚: {e}")
            self._rollback_files(app_backup_dir, replace_dirs)
            raise RuntimeError(f"文件替换失败: {e}")

        return app_backup_dir

    def _rollback_files(self, backup_dir: Path, replace_dirs: list[str]):
        """从备份目录回滚应用文件"""
        project_root = Path(".")
        for d in replace_dirs:
            backup = backup_dir / d
            dst = project_root / d
            if backup.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(backup, dst)
        logger.info("已从备份回滚应用文件")

    def _install_deps(self):
        """安装 Python 依赖"""
        from ..utils.runtime_python import persist_runtime_python

        # 写入当前解释器路径，供 start.bat 在升级重启时使用同一 Python
        try:
            persist_runtime_python()
        except Exception as e:
            logger.warning(f"[升级] 记录运行时 Python 路径失败: {e}")

        proxy = self._get_proxy()
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            "requirements.txt",
            "--upgrade",
        ]
        if proxy:
            cmd.extend(["--proxy", proxy])

        logger.info(f"[升级] 开始安装依赖: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                stderr_tail = result.stderr[-500:]
                logger.error(
                    f"[升级] pip 安装失败 (exit={result.returncode}):\n{stderr_tail}"
                )
                raise RuntimeError(f"依赖安装失败:\n{stderr_tail}")
            logger.info("[升级] pip install 执行成功")
        except subprocess.TimeoutExpired:
            logger.error("[升级] pip install 超时（超过10分钟）")
            raise RuntimeError("依赖安装超时（超过10分钟）")

    def _cleanup_old_backups(self, backup_dir: Path, keep: int):
        """清理旧备份，只保留最近 keep 个"""
        if not backup_dir.exists():
            return

        backups = sorted(
            [
                d
                for d in backup_dir.iterdir()
                if d.is_dir() and d.name.startswith("backup_")
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for old in backups[keep:]:
            shutil.rmtree(old, ignore_errors=True)
            removed += 1

        if removed:
            logger.info(f"[升级] 已清理 {removed} 个旧备份")

    def verify_upgrade(self, expected_version: str) -> bool:
        """验证升级后版本是否正确"""
        current = get_version()
        return current == expected_version

    def rollback_version(self):
        """回滚版本文件（从备份恢复关键文件和目录）"""
        backup_dir = Path("backups")
        if not backup_dir.exists():
            return

        # 找最近的备份目录
        backups = sorted(
            [
                d
                for d in backup_dir.iterdir()
                if d.is_dir() and d.name.startswith("backup_")
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not backups:
            return

        latest = backups[0]
        restored = []

        # 恢复单个文件
        for name in self._BACKUP_FILES:
            src = latest / name
            if src.exists():
                shutil.copy2(src, name)
                restored.append(name)

        # 恢复目录
        for d in self._BACKUP_DIRS:
            src = latest / d
            if src.is_dir():
                dst = Path(d)
                if dst.exists():
                    shutil.rmtree(dst)
                _ignore = shutil.ignore_patterns("__pycache__")
                shutil.copytree(src, dst, ignore=_ignore)
                restored.append(f"{d}/")

        if restored:
            logger.info(f"已从备份恢复: {', '.join(restored)}")


def restart_application():
    """重启应用进程

    Windows 优先使用 start.bat（与手动启动一致），
    依赖 data/runtime_python.txt 确保与 pip 安装使用同一解释器。
    Linux/macOS 使用 os.execv 替换当前进程。
    """
    logger.info("正在重启应用...")
    try:
        database_manager.close()
    except Exception as e:
        logger.debug("database_manager.close 失败: %s", e)

    if sys.platform == "win32":
        start_bat = Path("start.bat")
        if start_bat.exists():
            logger.info("[重启] 通过 start.bat 启动新进程")
            subprocess.Popen(["cmd", "/c", str(start_bat)], cwd=str(Path.cwd()))
            logger.info("[重启] 新进程已启动，当前进程即将退出")
            os._exit(0)

        # 回退：用 python -m uvicorn 重建命令
        is_uvicorn = "uvicorn" in sys.argv[0].lower()
        if is_uvicorn:
            cmd = [sys.executable, "-m", "uvicorn"] + sys.argv[1:]
        else:
            cmd = [sys.executable] + sys.argv

        logger.info(f"[重启] 命令: {' '.join(cmd)}")
        subprocess.Popen(cmd)
        logger.info("[重启] 新进程已启动，当前进程即将退出")
        os._exit(0)
    else:
        os.execv(sys.executable, [sys.executable] + sys.argv)


upgrade_service = UpgradeService()
