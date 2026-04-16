"""飞牛观看记录 → CustomItem → SyncService"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ...core.config import config_manager
from ...core.database import FEINIU_MIN_UPDATE_WATERMARK_META_KEY, database_manager
from ...core.logging import logger
from ...models.sync import CustomItem
from ..sync_service import sync_service
from .models import FeiniuWatchRecord
from .reader import fetch_completed_watch_records

# 同步记录 / 通知中的来源标识（与 Plex、Trakt 等并列）
FEINIU_SYNC_SOURCE = "feiniu"


def ensure_feiniu_startup_watermark() -> None:
    """仅手改 config.ini 启用飞牛时：启动进程若尚无水位则立即写入，避免等到首次定时同步。"""
    cfg = config_manager.get_feiniu_config()
    if not cfg.get("enabled"):
        return
    db_path = (cfg.get("db_path") or "").strip()
    if not db_path or not Path(db_path).is_file():
        return
    if database_manager.get_feiniu_meta(FEINIU_MIN_UPDATE_WATERMARK_META_KEY):
        return
    database_manager.set_feiniu_min_update_watermark_now()


@dataclass
class FeiniuSyncResult:
    success: bool
    message: str
    synced_count: int
    skipped_count: int
    error_count: int


class FeiniuSyncService:
    def _record_to_custom_item(self, rec: FeiniuWatchRecord) -> CustomItem | None:
        if rec.display_title.startswith("视频-"):
            return None
        ori = rec.original_title if rec.original_title else rec.display_title
        return CustomItem(
            media_type="episode",
            title=rec.display_title,
            ori_title=ori,
            season=rec.season,
            episode=rec.episode,
            release_date=rec.release_date,
            user_name=rec.username,
            source=FEINIU_SYNC_SOURCE,
        )

    async def run_sync(
        self,
        *,
        user_filter: str | None = None,
        ignore_enabled: bool = False,
    ) -> FeiniuSyncResult:
        cfg = config_manager.get_feiniu_config()
        if not ignore_enabled and not cfg.get("enabled"):
            return FeiniuSyncResult(True, "飞牛同步未启用", 0, 0, 0)

        db_path = (cfg.get("db_path") or "").strip()
        if not db_path:
            return FeiniuSyncResult(False, "未配置飞牛数据库路径 db_path", 0, 0, 1)

        if not Path(db_path).is_file():
            return FeiniuSyncResult(False, f"数据库文件不存在: {db_path}", 0, 0, 1)

        uf = (
            user_filter
            if user_filter is not None
            else (cfg.get("user_filter") or "all")
        )
        # 仅同步水位建立之后（含首次运行写入时刻）在库中更新的记录，不追溯启用前存量
        wm_ms = database_manager.get_or_create_feiniu_min_update_watermark_ms()
        records = fetch_completed_watch_records(
            db_path,
            user_guid=str(uf),
            time_range=str(cfg.get("time_range") or "all"),
            limit=int(cfg.get("limit") or 100),
            min_percent=int(cfg.get("min_percent") or 85),
            min_update_time_ms=wm_ms,
        )

        synced = skipped = errors = 0
        for rec in records:
            if database_manager.is_feiniu_item_synced(rec.user_guid, rec.item_guid):
                skipped += 1
                continue

            item = self._record_to_custom_item(rec)
            if not item:
                skipped += 1
                continue

            try:
                # 同步执行以便根据结果写入 feiniu_sync_history；未预期异常时由 sync_custom_item 的 except 写入 error 记录
                result = await asyncio.to_thread(
                    sync_service.sync_custom_item, item, FEINIU_SYNC_SOURCE
                )
            except Exception as e:
                logger.error(f"飞牛单条同步执行失败: {e}")
                errors += 1
                await asyncio.sleep(0.05)
                continue

            if result.status == "success":
                database_manager.save_feiniu_sync_history(
                    rec.user_guid, rec.item_guid, rec.update_time_ms or None
                )
                synced += 1
            elif result.status == "ignored":
                skipped += 1
            else:
                errors += 1
            await asyncio.sleep(0.05)

        ok = errors == 0
        return FeiniuSyncResult(
            ok,
            f"飞牛同步: 已提交 {synced}, 跳过 {skipped}, 失败 {errors}",
            synced,
            skipped,
            errors,
        )


feiniu_sync_service = FeiniuSyncService()
