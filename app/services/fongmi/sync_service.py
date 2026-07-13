"""fongmi 观看记录 → CustomItem → SyncService

通过局域网轮询 /media 端点获取实时播放状态。去重采用进程内 set
（设备 IP + episode_url），不新增数据库表，避免侵入 database 模块。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from ...core.config import config_manager
from ...core.logging import logger
from ...models.sync import CustomItem
from ..sync_service import sync_service
from .client import (
    discover_devices,
    fetch_all_media_status,
    fetch_completed_records,
    fetch_media,
    media_to_record,
    parse_device_entry,
)
from .models import FongmiDevice, FongmiWatchRecord

# 同步记录 / 通知中的来源标识（与 Plex、Trakt、feiniu 等并列）
FONGMI_SYNC_SOURCE = "fongmi"


@dataclass
class FongmiSyncResult:
    success: bool
    message: str
    synced_count: int
    skipped_count: int
    error_count: int
    discovered_devices: int = 0


class FongmiSyncService:
    """fongmi 同步服务

    进程内去重集合：{(device_ip, episode_url)}，每个设备每集只同步一次。
    进程重启后集合清空，但已标记过的 Bangumi 条目重复标记无副作用（幂等）。
    """

    _synced_keys: set[tuple[str, str]] = set()

    def _record_to_custom_item(self, rec: FongmiWatchRecord) -> CustomItem:
        return CustomItem(
            media_type="episode",
            title=rec.title,
            ori_title=None,
            season=rec.season,
            episode=rec.episode,
            release_date=rec.release_date,
            user_name=rec.device_name,
            source=FONGMI_SYNC_SOURCE,
        )

    async def _resolve_devices(self, cfg: dict) -> list[FongmiDevice]:
        """根据配置解析要轮询的设备列表（手动配置优先，可选网段扫描）"""
        devices: list[FongmiDevice] = []
        raw_devices = (cfg.get("devices") or "").strip()
        if raw_devices:
            entries = [s.strip() for s in raw_devices.split(",") if s.strip()]
            results = await asyncio.gather(
                *[parse_device_entry(e) for e in entries],
                return_exceptions=True,
            )
            for entry, res in zip(entries, results):
                if isinstance(res, Exception):
                    logger.warning(f"fongmi 设备连接异常: {entry} - {res}")
                elif res:
                    devices.append(res)
                    logger.info(f"fongmi 设备已连接: {res.name} ({res.ip}:{res.port})")
                else:
                    logger.warning(f"fongmi 设备连接失败: {entry}")

        if cfg.get("auto_scan") and (cfg.get("subnet") or "").strip():
            found = await discover_devices(str(cfg["subnet"]).strip())
            existing_ips = {d.ip for d in devices}
            for d in found:
                if d.ip not in existing_ips:
                    devices.append(d)

        return devices

    async def run_sync(self, *, ignore_enabled: bool = False) -> FongmiSyncResult:
        cfg = config_manager.get_fongmi_config()
        if not ignore_enabled and not cfg.get("enabled"):
            return FongmiSyncResult(True, "fongmi 同步未启用", 0, 0, 0)

        devices = await self._resolve_devices(cfg)
        if not devices:
            return FongmiSyncResult(False, "未发现任何 fongmi 设备", 0, 0, 0, 0)

        min_percent = int(cfg.get("min_percent") or 95)
        records = await fetch_completed_records(devices, min_percent)

        synced = skipped = errors = 0
        for rec in records:
            key = (rec.device_ip, rec.episode_url)
            if key in self._synced_keys:
                skipped += 1
                continue

            item = self._record_to_custom_item(rec)
            try:
                result = await asyncio.to_thread(
                    sync_service.sync_custom_item, item, FONGMI_SYNC_SOURCE
                )
            except Exception as e:
                logger.error(f"fongmi 单条同步执行失败: {e}")
                errors += 1
                continue

            if result.status == "success":
                self._synced_keys.add(key)
                synced += 1
            elif result.status == "ignored":
                skipped += 1
            else:
                errors += 1

        ok = errors == 0
        return FongmiSyncResult(
            ok,
            f"fongmi 同步: 已提交 {synced}, 跳过 {skipped}, 失败 {errors}",
            synced,
            skipped,
            errors,
            len(devices),
        )

    async def debug_scan(self) -> dict:
        """调试用：搜寻设备并拉取当前 /media 状态（不过滤完成）"""
        cfg = config_manager.get_fongmi_config()
        logger.info(
            f"fongmi 调试扫描开始：enabled={cfg.get('enabled')}, "
            f"devices={cfg.get('devices')!r}, auto_scan={cfg.get('auto_scan')}, "
            f"subnet={cfg.get('subnet')!r}"
        )
        devices = await self._resolve_devices(cfg)
        logger.info(f"fongmi 调试扫描：发现 {len(devices)} 台设备")
        media_list = await fetch_all_media_status(devices)
        logger.info(f"fongmi 调试扫描：拉取到 {len(media_list)} 条媒体状态")
        return {"discovered_devices": len(devices), "devices": media_list}

    async def debug_sync_one(
        self, device_ip: str, device_port: int, device_name: str
    ) -> dict:
        """调试用：对指定设备当前播放内容执行一次 Bangumi 同步，返回前后对比。

        返回 {before: FongMi 解析结果, after: Bangumi 匹配+标记结果}。
        """
        device = FongmiDevice(
            ip=device_ip,
            port=device_port,
            uuid="",
            name=device_name or device_ip,
            device_type=0,
        )

        async with httpx.AsyncClient() as client:
            media = await fetch_media(device, client)

        if not media:
            return {
                "before": {"device_ip": device_ip, "media": None},
                "after": {"status": "error", "message": "无法拉取该设备的 /media 状态"},
            }

        rec = media_to_record(device, media)
        if not rec:
            return {
                "before": {
                    "device_ip": device_ip,
                    "title": (media.get("title") or "").strip(),
                },
                "after": {"status": "error", "message": "无法解析播放记录（标题为空）"},
            }

        before = {
            "device_ip": device_ip,
            "device_name": device.name,
            "title": rec.title,
            "season": rec.season,
            "episode": rec.episode,
            "url": rec.episode_url,
            "artist": rec.artist,
            "duration": media.get("duration", 0),
            "position": media.get("position", 0),
        }

        item = self._record_to_custom_item(rec)
        try:
            result = await asyncio.to_thread(
                sync_service.sync_custom_item, item, FONGMI_SYNC_SOURCE
            )
        except Exception as e:
            logger.error(f"fongmi 调试同步执行失败: {e}")
            return {
                "before": before,
                "after": {"status": "error", "message": f"同步异常: {e}"},
            }

        after = {
            "status": result.status,
            "message": result.message,
            "data": result.data or {},
        }
        if result.status == "success":
            self._synced_keys.add((rec.device_ip, rec.episode_url))

        return {"before": before, "after": after}


fongmi_sync_service = FongmiSyncService()
