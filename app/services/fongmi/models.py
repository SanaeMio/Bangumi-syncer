"""fongmi 设备与观看记录模型"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FongmiDevice:
    """单台 fongmi 设备（来自 /device 端点）"""

    ip: str
    port: int
    uuid: str
    name: str
    device_type: int  # 0=手机, 1=电视
    version: str = ""


@dataclass(frozen=True)
class FongmiWatchRecord:
    """单条可同步的观看记录（已满足「看完」条件）

    fongmi 的 /media 端点无直接集数字段，集号需从 url/artist 解析，
    解析失败时回退为 1。release_date 在 /media 中也无可信来源，统一传空串，
    让 SyncService 侧按标题/季/集匹配。

    is_movie 标识剧场版/电影：此时 season=1, episode=1，由 SyncService
    走 movie 分支（标记 Bangumi 条目为在看/看过）。

    media_type 标识更细粒度的类型（episode/movie/ova/oad/real_action），
    默认为空串，为空时按 is_movie 推导。
    """

    device_ip: str
    device_name: str
    title: str
    episode: int
    season: int
    episode_url: str
    artist: str | None = None
    release_date: str = ""
    is_movie: bool = False
    media_type: str = ""
