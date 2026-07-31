"""Archive 下载与校验

职责：
- 拉取 latest.json 获取最新 dump 地址
- 下载 zip 文件（带镜像 fallback）
- SHA256 校验

进度回调签名：(task_id, ArchiveStage, percent, message) -> None
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from ...core.logging import logger
from ..http_base import AsyncHttpClient
from ._archive import ArchiveStage

# 下载超时（秒）：dump zip ~400MB，需较长时间
_DOWNLOAD_TIMEOUT = 600.0
_DOWNLOAD_MAX_RETRIES = 3
_CHUNK_SIZE = 8192

# latest.json 拉取超时
_LATEST_JSON_TIMEOUT = 30.0

ProgressCb = Callable[[str, ArchiveStage, int, str], None]


class ArchiveDownloader:
    """Archive dump 下载器

    Args:
        http_proxy: HTTP 代理（None 表示不使用代理）
        ssl_verify: SSL 证书校验
        mirrors: GitHub 镜像源前缀列表
        progress_cb: 进度回调 (task_id, stage, percent, message)
    """

    def __init__(
        self,
        http_proxy: Optional[str] = None,
        ssl_verify: bool = True,
        mirrors: tuple[str, ...] = (),
        progress_cb: Optional[ProgressCb] = None,
    ) -> None:
        self.http_proxy = http_proxy
        self.ssl_verify = ssl_verify
        self.mirrors = mirrors
        self._progress_cb = progress_cb

    def _emit(
        self, task_id: str, stage: ArchiveStage, percent: int, message: str
    ) -> None:
        if self._progress_cb is not None:
            self._progress_cb(task_id, stage, percent, message)

    # ===== latest.json 拉取 =====

    async def fetch_latest(self, latest_json_url: str) -> dict[str, Any]:
        """拉取 latest.json，返回解析后的字典"""
        urls = self._build_latest_urls(latest_json_url)
        last_error: Optional[Exception] = None

        for url_idx, url in enumerate(urls):
            source_label = (
                "GitHub" if url_idx == len(urls) - 1 else f"镜像{url_idx + 1}"
            )
            try:
                async with AsyncHttpClient(
                    label="Archive-latest",
                    timeout=_LATEST_JSON_TIMEOUT,
                    proxy=self.http_proxy,
                    verify=self.ssl_verify,
                    follow_redirects=True,
                    max_retries=2,
                ).prefix("📦") as client:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        raise RuntimeError(
                            f"拉取 latest.json 失败: HTTP {resp.status_code}"
                        )
                    data = resp.json()
                    logger.info(
                        f"bangumi_archive: 从 {source_label} 拉取 latest.json 成功，"
                        f"created_at={data.get('created_at')}"
                    )
                    return data
            except (httpx.HTTPError, ValueError, RuntimeError) as e:
                last_error = e
                logger.warning(
                    f"bangumi_archive: 从 {source_label} 拉取 latest.json 失败: {e}"
                )

        raise RuntimeError(
            f"拉取 latest.json 失败（已尝试 {len(urls)} 个源）: {last_error}"
        )

    def _build_latest_urls(self, latest_json_url: str) -> list[str]:
        urls: list[str] = []
        for mirror in self.mirrors:
            urls.append(f"{mirror}{latest_json_url}")
        urls.append(latest_json_url)
        return urls

    # ===== zip 下载 =====

    async def download(
        self, latest: dict[str, Any], task_id: str, tmp_dir: Path
    ) -> Path:
        """下载 dump zip 文件

        Args:
            latest: latest.json 解析后的字典
            task_id: 任务 ID（供进度回调）
            tmp_dir: 临时工作目录（data_dir/.tmp/），zip 下载到其下的任务子目录

        Returns:
            下载的 zip 文件路径（tmp_dir/<task_id>/zip_filename）
        """
        download_url = latest.get("browser_download_url", "")
        if not download_url:
            raise RuntimeError("latest.json 缺少 browser_download_url 字段")

        urls = self._build_download_urls(download_url)

        # 在 tmp_dir 下为本次任务创建独立子目录，便于导入后统一清理
        task_dir = tmp_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        zip_filename = latest.get("name") or "dump.zip"
        zip_path = task_dir / zip_filename

        if self.http_proxy:
            logger.info(f"bangumi_archive: 使用代理 {self.http_proxy}")

        last_error: Optional[Exception] = None

        for url_idx, url in enumerate(urls):
            source_label = (
                "GitHub" if url_idx == len(urls) - 1 else f"镜像{url_idx + 1}"
            )

            if url_idx > 0:
                self._emit(
                    task_id,
                    ArchiveStage.DOWNLOAD_SWITCH,
                    10,
                    f"主源下载失败，切换到 {source_label}",
                )

            for attempt in range(_DOWNLOAD_MAX_RETRIES):
                if attempt > 0:
                    wait = min(2**attempt, 10)
                    logger.warning(
                        f"bangumi_archive: {source_label}下载失败，{wait}秒后重试"
                        f"（第{attempt + 1}/{_DOWNLOAD_MAX_RETRIES}次）"
                    )
                    self._emit(
                        task_id,
                        ArchiveStage.DOWNLOAD_RETRY,
                        10,
                        f"{source_label}下载失败，{wait}秒后重试"
                        f"（第{attempt + 1}/{_DOWNLOAD_MAX_RETRIES}次）",
                    )
                    await asyncio.sleep(wait)

                try:
                    await self._download_single(
                        url=url,
                        zip_path=zip_path,
                        source_label=source_label,
                        task_id=task_id,
                    )
                    logger.info(
                        f"bangumi_archive: 从 {source_label} 下载成功，"
                        f"文件大小: {zip_path.stat().st_size // 1024}KB"
                    )
                    return zip_path

                except httpx.TimeoutException:
                    last_error = RuntimeError(f"{source_label}下载超时")
                    logger.warning(f"bangumi_archive: {last_error}")
                except httpx.RequestError as e:
                    last_error = e
                    logger.warning(f"bangumi_archive: {source_label}下载连接失败: {e}")
                except RuntimeError as e:
                    last_error = e
                    logger.warning(f"bangumi_archive: {source_label}下载失败: {e}")

        try:
            shutil.rmtree(task_dir, ignore_errors=True)
        except OSError:
            pass

        proxy_hint = ""
        if not self.http_proxy:
            proxy_hint = "。若网络受限，请在配置文件 [bangumi-archive] http_proxy 或 [dev] script_proxy 中设置代理"

        raise RuntimeError(
            f"下载失败（已尝试 {len(urls)} 个源，每个重试 {_DOWNLOAD_MAX_RETRIES} 次）"
            f"{proxy_hint}: {last_error}"
        )

    def _build_download_urls(self, github_download_url: str) -> list[str]:
        urls: list[str] = []
        for mirror in self.mirrors:
            urls.append(f"{mirror}{github_download_url}")
        urls.append(github_download_url)
        return urls

    async def _download_single(
        self,
        url: str,
        zip_path: Path,
        source_label: str,
        task_id: str,
    ) -> None:
        """从单个 URL 下载 zip 文件（流式）"""
        async with AsyncHttpClient(
            label="Archive-download",
            timeout=_DOWNLOAD_TIMEOUT,
            proxy=self.http_proxy,
            verify=self.ssl_verify,
            follow_redirects=True,
            max_retries=0,
        ).prefix("📦") as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"下载失败: HTTP {resp.status_code}")

                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                last_reported = 0
                # 按大小节流：每 10MB 推送一次进度，避免每 8KB chunk 推送
                # 导致 _progress_logs 暴涨 + SSE 序列化巨大历史阻塞事件循环
                _PROGRESS_INTERVAL = 10 * 1024 * 1024

                with open(zip_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            if (
                                downloaded - last_reported >= _PROGRESS_INTERVAL
                                or downloaded == total
                            ):
                                last_reported = downloaded
                                pct = 10 + int(downloaded / total * 50)
                                self._emit(
                                    task_id,
                                    ArchiveStage.DOWNLOADING,
                                    pct,
                                    f"正在从{source_label}下载... {_fmt_size(downloaded)} / {_fmt_size(total)}",
                                )
                        elif downloaded - last_reported >= _PROGRESS_INTERVAL:
                            last_reported = downloaded
                            self._emit(
                                task_id,
                                ArchiveStage.DOWNLOADING,
                                30,
                                f"正在从{source_label}下载... {_fmt_size(downloaded)}",
                            )

    # ===== SHA256 校验 =====

    async def verify_sha256(
        self, zip_path: Path, digest: str, task_id: str = ""
    ) -> None:
        """校验 zip 文件的 SHA256"""
        if not digest:
            logger.warning("bangumi_archive: 未提供 digest，跳过 SHA256 校验")
            return

        if task_id:
            self._emit(task_id, ArchiveStage.VERIFYING, 58, "校验 SHA256")

        m = re.match(r"sha256:(\w+)", digest.strip())
        if not m:
            raise RuntimeError(f"digest 格式非法: {digest!r}")
        expected = m.group(1).lower()

        actual = await asyncio.to_thread(self._compute_sha256, zip_path)
        if actual != expected:
            raise RuntimeError(
                f"SHA256 校验失败: 期望 {expected[:16]}..., 实际 {actual[:16]}..."
            )
        logger.info(f"bangumi_archive: SHA256 校验通过 ({actual[:16]}...)")

        if task_id:
            self._emit(task_id, ArchiveStage.VERIFYING, 60, "SHA256 校验通过")

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes // 1024}KB"
    return f"{size_bytes // (1024 * 1024)}MB"
