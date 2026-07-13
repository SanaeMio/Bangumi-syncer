"""驱动抽象基类的数据模型

为各驱动（feiniu/fongmi/trakt 等）提供统一的同步结果与观看记录基类。
各驱动可继承后扩展自己的字段。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BaseSyncResult:
    """驱动同步结果基类

    字段与现有 FeiniuSyncResult / FongmiSyncResult 完全一致，
    各驱动可继承后添加扩展字段（如 FongmiSyncResult.discovered_devices）。
    """

    success: bool
    message: str
    synced_count: int = 0
    skipped_count: int = 0
    error_count: int = 0


@dataclass(frozen=True)
class BaseWatchRecord:
    """观看记录基类

    各驱动的 WatchRecord 可继承后扩展自己的字段
    （如 FongmiWatchRecord.device_ip、FeiniuWatchRecord.item_guid）。
    """

    title: str
    season: int
    episode: int
    release_date: str
    user_name: str
