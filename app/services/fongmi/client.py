"""fongmi 局域网设备发现与 /media 轮询

参考 fongmi 官方 docs/LOCAL.md：
- 端口范围 9978-9998（NanoHTTPD），默认 9978
- GET /device → {uuid, name, ip, type, version, ...}
- GET /media  → {state, speed, title, artist, artwork, url, duration, position}

注：/media 端点为 fongmi 扩展，原版 TVBoxOSC 谱系不支持，故本驱动仅适用于
fongmi 及其 fork（TV-K、OK影视、WebHomeTV 等）。
"""

from __future__ import annotations

import asyncio
import re

import httpx

from ...core.logging import logger
from ...utils.http_base import AsyncHttpClient
from .models import FongmiDevice, FongmiWatchRecord

# 端口与超时
PORT_START = 9978
PORT_END = 9998
_DEFAULT_PORT = 9978
_HTTP_TIMEOUT = 3.0  # /media 请求超时
_PROBE_TIMEOUT = 0.3  # /device 探测超时（局域网内足够）
_DISCOVER_SEMAPHORE = 100  # 网段扫描并发上限

# 直播流标记（Long.MIN_VALUE）
_LONG_MIN = -9223372036854775808

# ===== 集数/季号解析 =====
#
# 季号策略（方案 B）：
#   仅当出现 *明确的* 季号标记（S02E03 中 S>1、"第二季"、"Season 2"）才返回 season>1；
#   S01/S00/无标记 一律视为 season=1，交给 SyncService 沿 Bangumi 续集链查找。
#   避免源站统一标记为 S01 时误匹配到某一具体季。
#
# 集号优先级：SxxExx > EP/Exx > 第N集 > #N > [N] > 文件名兜底
_SEASON_EP_RE = re.compile(r"[Ss](\d{1,2})\s*\.?[Ee](\d{1,3})", re.IGNORECASE)
_EP_RE = re.compile(r"\b[Ee][Pp]?[\s.\-_]?\s*0*(\d{1,3})\b", re.IGNORECASE)
_CN_EP_RE = re.compile(r"第\s*0*(\d{1,3})\s*[集话話章回]")
_HASH_EP_RE = re.compile(r"#\s*0*(\d{1,3})\b")
_BRACKET_EP_RE = re.compile(r"[\[【(]\s*0*(\d{1,3})\s*[\]】)]")

# 季号标记（仅当 N>1 时才视为多季）
_SEASON_CN_RE = re.compile(r"第\s*([一二三四五六七八九十2-9])\s*[季期]")
_SEASON_EN_RE = re.compile(r"[Ss]eason\s*0*([2-9]\d?)", re.IGNORECASE)
_CN_NUM_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

# 文件名清理用正则
_BRACKET_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|【[^】]*】|《[^》]*》")
_SIZE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB)", re.IGNORECASE)
_RESOLUTION_RE = re.compile(
    r"\b(?:4K|2160P|1080P|720P|480P|360P|UHD|FHD|HD|HDR)\b", re.IGNORECASE
)
_RANGE_RE = re.compile(r"\b\d{1,4}\s*-\s*\d{1,4}\b")
_FIRST_NUM_RE = re.compile(r"\d{1,3}")

# 剧场版/电影关键词（URL 或 artist 命中即视为单条影片）
_MOVIE_KEYWORD_RE = re.compile(
    r"剧场版|劇場版|电影|電影|\bMovie\b|\bFilm\b",
    re.IGNORECASE,
)


def _extract_episode_from_filename(text: str) -> int:
    """文件名兜底：清理后取第一个 1-3 位数字。

    移除扩展名、方括号内容、文件大小、分辨率、合集范围、季号标记后取首个数字。
    """
    if not text:
        return 0
    filename = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    filename = re.sub(r"\.\w+$", "", filename)
    filename = _BRACKET_RE.sub(" ", filename)
    filename = _SIZE_RE.sub(" ", filename)
    filename = _RESOLUTION_RE.sub(" ", filename)
    filename = _RANGE_RE.sub(" ", filename)
    filename = _SEASON_CN_RE.sub(" ", filename)
    filename = _SEASON_EN_RE.sub(" ", filename)
    m = _FIRST_NUM_RE.search(filename)
    if m:
        n = int(m.group())
        if 1 <= n <= 999:
            return n
    return 0


def _detect_explicit_season(text: str) -> int:
    """检测明确的季号标记，仅在 N>1 时返回 N，否则返回 1。"""
    if not text:
        return 1
    m = _SEASON_CN_RE.search(text)
    if m:
        token = m.group(1)
        n = _CN_NUM_MAP.get(token) or (int(token) if token.isdigit() else 0)
        if n > 1:
            return n
    m = _SEASON_EN_RE.search(text)
    if m:
        n = int(m.group(1))
        if n > 1:
            return n
    return 1


def parse_episode_info(url: str, artist: str) -> tuple[int, int]:
    """从 /media 的 url 与 artist 解析 (season, episode)。

    方案 B：S01/S00/无标记 → season=1；仅 S>1 或「第二季」「Season 2」→ season>1。
    集号优先级：url > artist；缺失回退为 1。
    """
    sources = [s for s in (url or "", artist or "") if s]

    # 1. SxxExx（同时解析 season）
    for text in sources:
        m = _SEASON_EP_RE.search(text)
        if m:
            s = int(m.group(1))
            ep = max(1, int(m.group(2)))
            return (s if s > 1 else 1, ep)

    # 2-6. 仅解析集号
    episode = 0
    for pattern in (_EP_RE, _CN_EP_RE, _HASH_EP_RE, _BRACKET_EP_RE):
        if episode:
            break
        for text in sources:
            m = pattern.search(text)
            if m:
                episode = max(1, int(m.group(1)))
                break

    # 6. 文件名兜底
    if not episode:
        for text in sources:
            ep = _extract_episode_from_filename(text)
            if ep > 0:
                episode = ep
                break

    if not episode:
        episode = 1

    # 季号判定
    season = 1
    for text in sources:
        s = _detect_explicit_season(text)
        if s > 1:
            season = s
            break

    return season, episode


def _is_movie(url: str, artist: str | None) -> bool:
    """判断是否为剧场版/电影。

    命中关键词（剧场版/劇場版/电影/電影/Movie/Film）即视为单条影片。
    """
    for text in (url or "", artist or ""):
        if text and _MOVIE_KEYWORD_RE.search(text):
            return True
    return False


# ===== 设备发现与探测 =====


def _is_fongmi_device_info(info: dict) -> bool:
    """判断 /device 返回是否像 fongmi 设备"""
    return any(k in info for k in ("uuid", "name", "ip"))


def _build_device(ip: str, port: int, info: dict) -> FongmiDevice:
    """从 /device 响应构造 FongmiDevice"""
    return FongmiDevice(
        ip=ip,
        port=port,
        uuid=str(info.get("uuid", "")),
        name=str(info.get("name", ip)),
        device_type=int(info.get("type", 0) or 0),
        version=str(info.get("version", "")),
    )


async def _probe_device(client: AsyncHttpClient, ip: str, port: int) -> dict | None:
    """探测单个 ip:port 的 /device 端点"""
    try:
        resp = await client.get(f"http://{ip}:{port}/device", timeout=_PROBE_TIMEOUT)
        if resp.status_code == 200:
            info = resp.json()
            if isinstance(info, dict) and _is_fongmi_device_info(info):
                return info
    except (httpx.HTTPError, ValueError) as e:
        logger.debug("设备探测失败: %s", e)
    return None


async def _probe_ports(
    client: AsyncHttpClient, ip: str, ports: list[int]
) -> tuple[int, dict] | None:
    """并行探测指定 IP 的多个端口，返回第一个命中的 (port, info)"""

    async def _one(p: int) -> tuple[int, dict | None]:
        return p, await _probe_device(client, ip, p)

    tasks = [asyncio.create_task(_one(p)) for p in ports]
    for result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(result, tuple) and result[1]:
            return result
    return None


async def discover_devices(
    subnet: str, base_client: AsyncHttpClient | None = None
) -> list[FongmiDevice]:
    """扫描网段（如 192.168.1）的默认端口 9978，发现 fongmi 设备。

    只探测 9978（254 次），不扫全端口范围。非标端口设备请在 devices 配置中指定 ip:port。
    """
    if not subnet:
        return []

    own_client = base_client is None
    client = base_client or AsyncHttpClient(
        label="Fongmi",
        limits=httpx.Limits(max_connections=300, max_keepalive_connections=50),
        max_retries=0,
    ).prefix("📡")
    found: list[FongmiDevice] = []

    async def _check(ip: str) -> FongmiDevice | None:
        info = await _probe_device(client, ip, _DEFAULT_PORT)
        return _build_device(ip, _DEFAULT_PORT, info) if info else None

    try:
        sem = asyncio.Semaphore(_DISCOVER_SEMAPHORE)

        async def _bounded(ip: str) -> FongmiDevice | None:
            async with sem:
                return await _check(ip)

        tasks = [asyncio.create_task(_bounded(f"{subnet}.{i}")) for i in range(1, 255)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, FongmiDevice):
                found.append(r)
    finally:
        if own_client:
            await client.aclose()

    logger.info(f"fongmi 设备发现：网段 {subnet}.0/24 共找到 {len(found)} 台设备")
    return found


async def parse_device_entry(
    entry: str, base_client: AsyncHttpClient | None = None
) -> FongmiDevice | None:
    """解析手动配置的单个设备入口（ip 或 ip:port），查询 /device 补全信息。

    指定端口则只探测该端口；未指定则默认端口 9978 优先，未命中再并行扫其余端口。
    """
    entry = (entry or "").strip()
    if not entry:
        return None

    host = entry
    port = 0
    if ":" in entry:
        parts = entry.rsplit(":", 1)
        if parts[1].isdigit():
            host, port_str = parts
            port = int(port_str)

    own_client = base_client is None
    client = base_client or AsyncHttpClient(label="Fongmi", max_retries=0).prefix("📡")
    try:
        # 指定端口或默认端口 9978：单次探测
        target_port = port or _DEFAULT_PORT
        info = await _probe_device(client, host, target_port)
        if info:
            return _build_device(host, target_port, info)
        if port:
            return None

        # 默认端口未命中，并行探测其余端口
        other_ports = [p for p in range(PORT_START, PORT_END + 1) if p != _DEFAULT_PORT]
        hit = await _probe_ports(client, host, other_ports)
        if hit:
            return _build_device(host, hit[0], hit[1])
    finally:
        if own_client:
            await client.aclose()
    return None


# ===== /media 拉取与解析 =====


async def fetch_media(device: FongmiDevice, client: AsyncHttpClient) -> dict | None:
    """获取单台设备的 /media 播放状态"""
    try:
        resp = await client.get(
            f"http://{device.ip}:{device.port}/media", timeout=_HTTP_TIMEOUT
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def media_is_complete(media: dict, min_percent: int) -> bool:
    """判断 /media 是否达到「看完」条件

    - duration 为正且 position/duration >= min_percent/100
    - 直播流（duration <= 0 或 Long.MIN_VALUE）不视为完成
    """
    if not media or not media.get("title"):
        return False
    duration = media.get("duration", 0) or 0
    if duration <= 0 or duration == _LONG_MIN:
        return False
    position = media.get("position", 0) or 0
    if position <= 0:
        return False
    return (position / duration) >= (min_percent / 100.0)


def media_to_record(device: FongmiDevice, media: dict) -> FongmiWatchRecord | None:
    """将 /media 转为 FongmiWatchRecord（仅提取字段，不判断是否完成）

    剧场版/电影：season=1, episode=1，is_movie=True。
    """
    title = (media.get("title") or "").strip()
    if not title:
        return None
    url = str(media.get("url") or "")
    artist = media.get("artist")
    artist_s = str(artist) if artist else None
    is_movie = _is_movie(url, artist_s)
    if is_movie:
        season, episode = 1, 1
    else:
        season, episode = parse_episode_info(url, artist_s or "")
    return FongmiWatchRecord(
        device_ip=device.ip,
        device_name=device.name,
        title=title,
        episode=episode,
        season=season,
        episode_url=url,
        artist=artist_s,
        release_date="",
        is_movie=is_movie,
    )


def media_to_debug_dict(device: FongmiDevice, media: dict | None) -> dict:
    """将 /media 转为调试展示用的 dict（含解析后的集号与进度百分比）"""
    base = {
        "device_ip": device.ip,
        "device_port": device.port,
        "device_name": device.name,
    }
    if not media:
        return {**base, "media": None}

    url = str(media.get("url") or "")
    artist = media.get("artist")
    artist_s = str(artist) if artist else ""
    is_movie = _is_movie(url, artist_s or None)
    if is_movie:
        season, episode = 1, 1
    else:
        season, episode = parse_episode_info(url, artist_s)
    duration = media.get("duration", 0) or 0
    position = media.get("position", 0) or 0
    percent = 0.0
    if duration > 0 and duration != _LONG_MIN and position > 0:
        percent = round(position / duration * 100, 1)
    return {
        **base,
        "media": {
            "state": media.get("state"),
            "title": (media.get("title") or "").strip(),
            "url": url,
            "artist": artist_s,
            "duration": duration,
            "position": position,
            "percent": percent,
            "is_movie": is_movie,
            "parsed_season": season,
            "parsed_episode": episode,
        },
    }


async def fetch_completed_records(
    devices: list[FongmiDevice], min_percent: int
) -> list[FongmiWatchRecord]:
    """并行拉取所有设备的 /media，返回达到「看完」条件的观看记录"""
    if not devices:
        return []
    records: list[FongmiWatchRecord] = []
    async with AsyncHttpClient(label="Fongmi", max_retries=0).prefix("📡") as client:
        tasks = [fetch_media(d, client) for d in devices]
        media_list = await asyncio.gather(*tasks, return_exceptions=True)
        for device, media in zip(devices, media_list):
            if isinstance(media, Exception) or not media:
                continue
            if not media_is_complete(media, min_percent):
                continue
            rec = media_to_record(device, media)
            if rec:
                records.append(rec)
    return records


async def fetch_all_media_status(devices: list[FongmiDevice]) -> list[dict]:
    """并行拉取所有设备的 /media 当前状态（不过滤完成），返回调试展示用 dict 列表"""
    if not devices:
        return []
    async with AsyncHttpClient(label="Fongmi", max_retries=0).prefix("📡") as client:
        tasks = [fetch_media(d, client) for d in devices]
        media_list = await asyncio.gather(*tasks, return_exceptions=True)
    return [
        media_to_debug_dict(d, m if isinstance(m, dict) else None)
        for d, m in zip(devices, media_list)
    ]
