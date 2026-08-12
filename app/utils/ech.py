"""ECH（Encrypted Client Hello）配置提供器

通过 DoH 的 HTTPS RR 获取 Cloudflare 通用 ECHConfigList，构建带 ECH 的
utls SSLContext，供 httpx ``verify=`` 注入（见 http_client 两工厂的 ``ech`` 参数）。
目标域名按 ``[dev] ech_hosts`` 后缀匹配；DoH 获取失败或 utls 不可用时
**静默降级**为普通 TLS，不影响同步功能。

配置项（均在 ``[dev]`` 段，与代理 script_proxy / ssl_verify 同段）：

- ``ech_mode``: ``off``（默认，不启用）/ ``doh``（自动查询）/ ``manual``（直接提供 base64 配置）
- ``ech_doh_url``: 默认 ``https://dns.alidns.com/resolve``（阿里公共 DNS，国内直连稳定）；备选方案海外网络可改 ``https://dns.google/resolve``；兼容 dns-json（``?name=&type=``）
  与 RFC 8484 wireformat（POST application/dns-message）两种 DoH 端点
- ``ech_doh_use_proxy``: DoH 查询是否走 ``[dev] script_proxy``，默认否（直连，更利于 SRI 隐私）
- ``ech_hosts``: 逗号分隔的 ECH 目标域名，默认 ``bgm.tv,chii.in,next.bgm.tv,lain.bgm.tv``
  （后缀匹配，bgm.tv 同时覆盖 api.bgm.tv 等子域）
- ``ech_ech_config``: ``manual`` 模式下直接提供 base64 编码的 ECHConfigList

CF 通用配置说明：从 ``cloudflare-ech.com`` 的 HTTPS RR 提取的 ``ech=`` 字段适用于
任意 CF 代理域名（bgm.tv 系均在 CF 后），按 1.1 节机制即"内层 SNI=真实域名（加密）、
外层无 SNI"。
"""

from __future__ import annotations

import base64
import json
import ssl
import struct
import threading
import time

import httpx

from app.core.config import config_manager
from app.core.logging import logger

# 默认 DoH 端点（dns-json 格式，可被 [dev] ech_doh_url 覆盖，方案 B Worker 亦走此链路）
DEFAULT_DOH_URL = "https://dns.alidns.com/resolve"
# 默认 ECH 目标域名（后缀匹配）
DEFAULT_ECH_HOSTS = "bgm.tv,chii.in,next.bgm.tv,lain.bgm.tv"
# ECH 配置缓存 TTL（秒）；到期后重新查询 DoH 以跟随配置轮换
CONTEXT_TTL_SECONDS = 1200
# 获取失败结果的缓存 TTL（秒）：短暂失效，避免瞬时 DoH 抖动让 ECH 长时间失联
FAILED_TTL_SECONDS = 60
# DoH 查询超时
DOH_TIMEOUT_SECONDS = 10.0

ECH_CONFIG_SOURCE_HOST = "cloudflare-ech.com"

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, ssl.SSLContext | None]] = {}
# per-key 单飞锁：缓存过期时只允许一个线程发起 DoH 查询，其余等待复用
_inflight: dict[str, threading.Lock] = {}
_utls_available: bool | None = None
# 缓存缺失哨兵（与"缓存了 None 失败结果"区分）
_MISS = object()


# ── 配置读取 ───────────────────────────────────────────────────────────────


def ech_mode() -> str:
    """当前 ECH 模式（[dev] ech_mode，默认 off）。"""
    raw = config_manager.get("dev", "ech_mode", fallback="off")
    return str(raw).strip().lower() or "off"


def _doh_settings() -> tuple[str, bool]:
    """(doh_url, use_proxy) 快照。"""
    url = str(
        config_manager.get("dev", "ech_doh_url", fallback=DEFAULT_DOH_URL) or ""
    ).strip()
    if not url:
        url = DEFAULT_DOH_URL
    raw_proxy = config_manager.get("dev", "ech_doh_use_proxy", fallback=False)
    use_proxy = (
        raw_proxy
        if isinstance(raw_proxy, bool)
        else str(raw_proxy).strip().lower() not in ("false", "0", "no", "off", "")
    )
    return url, use_proxy


def _inline_ech_config() -> str:
    """manual 模式下的 base64 ECHConfigList（[dev] ech_ech_config）。"""
    return str(config_manager.get("dev", "ech_ech_config", fallback="") or "").strip()


def is_ech_host(host: str) -> bool:
    """目标域名后缀匹配（bgm.tv 覆盖 api.bgm.tv；非目标域返回 False）。"""
    hosts_raw = str(
        config_manager.get("dev", "ech_hosts", fallback=DEFAULT_ECH_HOSTS) or ""
    )
    targets = [h.strip().lower() for h in hosts_raw.split(",") if h.strip()]
    if not targets:
        targets = [h.strip().lower() for h in DEFAULT_ECH_HOSTS.split(",")]
    host = host.strip().lower()
    return any(host == t or host.endswith("." + t) for t in targets)


def _resolve_proxy() -> str | None:
    """DoH 查询使用的代理（ech_doh_use_proxy=True 时跟随 [dev] script_proxy）。"""
    _url, use_proxy = _doh_settings()
    if not use_proxy:
        return None
    proxy = config_manager.get("dev", "script_proxy", fallback="")
    return str(proxy).strip() or None


# ── DoH 查询 ───────────────────────────────────────────────────────────────


def _fetch_doh_json(doh_url: str, proxy: str | None) -> str:
    """dns-json 查询（GET ?name=&type=HTTPS），返回响应文本。"""
    sep = "&" if "?" in doh_url else "?"
    url = f"{doh_url}{sep}name={ECH_CONFIG_SOURCE_HOST}&type=HTTPS"
    with httpx.Client(proxy=proxy, timeout=DOH_TIMEOUT_SECONDS) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def _fetch_doh_wire(doh_url: str, proxy: str | None) -> bytes:
    """RFC 8484 wireformat 查询（POST application/dns-message），返回响应字节。"""
    with httpx.Client(proxy=proxy, timeout=DOH_TIMEOUT_SECONDS) as client:
        resp = client.post(
            doh_url,
            content=_build_dns_query(),
            headers={"Content-Type": "application/dns-message"},
        )
        resp.raise_for_status()
        return resp.content


def _build_dns_query() -> bytes:
    """构造 cloudflare-ech.com 的 HTTPS(65) + OPT 查询（RFC 8484）。"""
    query = bytearray()
    query += struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 1)
    for label in ("cloudflare", "ech", "com"):
        query += bytes([len(label)]) + label.encode("ascii")
    query += b"\x00"
    query += struct.pack(">HH", 65, 1)  # QTYPE=HTTPS, QCLASS=IN
    query += b"\x00"  # OPT 根域名
    query += struct.pack(">HHIH", 41, 4096, 0, 0)  # TYPE=OPT, UDP 4096
    return bytes(query)


def _parse_dns_json(text: str) -> bytes | None:
    """从 dns-json 响应中提取 ech=（base64）并解码。"""
    data = json.loads(text)
    for answer in data.get("Answer", []):
        entry = str(answer.get("data", ""))
        if "ech=" in entry:
            ech_b64 = entry.split("ech=", 1)[1].split()[0].strip()
            try:
                # RFC 9460 的 ech= 是不带 padding 的 base64，补齐后再解码
                return base64.b64decode(ech_b64 + "=" * (-len(ech_b64) % 4))
            except ValueError:
                logger.warning(
                    f"ECH DoH 解析失败: ech= 字段非法 ({ECH_CONFIG_SOURCE_HOST})"
                )
                return None
    return None


def _parse_wire_response(raw: bytes) -> bytes | None:
    """从 RFC 8484 响应中提取首个 HTTPS(65) 记录的 ech 参数（SVCB key=5）。"""

    def skip_name(data: bytes, offset: int) -> int:
        while offset < len(data):
            length = data[offset]
            if length == 0:
                return offset + 1
            if length & 0xC0 == 0xC0:  # 压缩指针
                return offset + 2
            offset += 1 + length
        return offset

    if len(raw) < 12:
        return None
    ancount = struct.unpack(">H", raw[6:8])[0]
    offset = 12
    offset = skip_name(raw, offset) + 4  # question: qname + qtype/qclass
    for _ in range(ancount):
        if raw[offset] & 0xC0 == 0xC0:
            offset += 2
        else:
            offset = skip_name(raw, offset)
        if offset + 10 > len(raw):
            return None
        rtype, _rclass, _ttl, rdlength = struct.unpack(
            ">HHIH", raw[offset : offset + 10]
        )
        offset += 10
        rdata = raw[offset : offset + rdlength]
        offset += rdlength
        if rtype != 65 or len(rdata) < 4:
            continue
        # SVCB: priority(2) target-name 参数列表
        pos = 2
        pos = skip_name(rdata, pos)
        while pos + 4 <= len(rdata):
            key, length = struct.unpack(">HH", rdata[pos : pos + 4])
            pos += 4
            value = rdata[pos : pos + length]
            pos += length
            if key == 5 and value:  # ech
                return value
    return None


# ── ECH 配置获取与上下文构建 ───────────────────────────────────────────────


def _utls_available_check() -> bool:
    """utls 是否可用（懒加载检查，避免 off 模式导入额外依赖）。"""
    global _utls_available
    if _utls_available is None:
        try:
            import utls  # noqa: F401

            _utls_available = True
        except ImportError:
            _utls_available = False
    return _utls_available


def _build_ech_context(ech_config: bytes) -> ssl.SSLContext | None:
    """基于 utls 构建带 ECH 的 SSLContext（失败返回 None）。"""
    if not _utls_available_check():
        logger.warning("[ECH] utls 依赖不可用，降级为普通 TLS")
        return None
    try:
        import utls

        base = utls.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        base.load_default_certs()
        return base.set_ech_configs(ech_config)
    except Exception as e:
        logger.warning(f"[ECH] 构建 utls 上下文失败，降级为普通 TLS: {e}")
        return None


def _query_ech_config() -> bytes | None:
    """从 DoH 获取 ECHConfigList（先尝试 dns-json，失败后回退 wireformat）。"""
    doh_url, _use_proxy = _doh_settings()
    proxy = _resolve_proxy()
    errors: list[str] = []
    try:
        text = _fetch_doh_json(doh_url, proxy)
        ech = _parse_dns_json(text)
        if ech:
            logger.info(f"[ECH] DoH 获取 ECH 配置成功（{ECH_CONFIG_SOURCE_HOST}）")
            return ech
        errors.append("响应中无 ech= 字段")
    except Exception as e:
        errors.append(f"dns-json 失败: {e}")
    try:
        raw = _fetch_doh_wire(doh_url, proxy)
        ech = _parse_wire_response(raw)
        if ech:
            logger.info(
                f"[ECH] DoH(wireformat) 获取 ECH 配置成功（{ECH_CONFIG_SOURCE_HOST}）"
            )
            return ech
        errors.append("wireformat 响应中无 ech 参数")
    except Exception as e:
        errors.append(f"wireformat 失败: {e}")
    logger.warning(f"[ECH] 无法获取 ECH 配置，本次降级为普通 TLS: {'; '.join(errors)}")
    return None


def _read_cache(key: str) -> object:
    """读取未过期的缓存；缺失/过期返回 _MISS 哨兵。

    成功结果 TTL 为 CONTEXT_TTL_SECONDS，失败结果（缓存的 None）为
    较短的 FAILED_TTL_SECONDS，避免瞬时 DoH 抖动造成长时间静默失效。
    """
    with _cache_lock:
        cached = _cache.get(key)
        if cached is None:
            return _MISS
        now = time.monotonic()
        ctx = cached[1]
        ttl = CONTEXT_TTL_SECONDS if ctx is not None else FAILED_TTL_SECONDS
        if now - cached[0] < ttl:
            return ctx
        return _MISS


def _write_cache(key: str, ctx: ssl.SSLContext | None) -> None:
    with _cache_lock:
        # 构建失败也缓存，避免每次请求重复查询 DoH（失败用短 TTL）
        _cache[key] = (time.monotonic(), ctx)


def get_ech_ssl_context() -> ssl.SSLContext | None:
    """获取带 ECH 的 SSLContext（缓存 + TTL + 配置变化自动失效 + 并发单飞）。

    manual 模式直接使用 ``[dev] ech_ech_config``；doh 模式查询 DoH。
    任何失败路径均返回 None（调用方保持原 verify 行为）。
    """
    mode = ech_mode()
    if mode == "off":
        return None
    if mode == "manual":
        ech_b64 = _inline_ech_config()
        if not ech_b64:
            logger.warning("[ECH] manual 模式未配置 ech_ech_config，降级为普通 TLS")
            return None
        try:
            ech_config = base64.b64decode(ech_b64)
        except ValueError:
            logger.warning("[ECH] ech_ech_config 不是合法 base64，降级为普通 TLS")
            return None
        key = f"manual:{ech_b64}"
    else:
        # utls 不可用则直接降级，避免白跑 DoH 网络查询
        if not _utls_available_check():
            logger.warning("[ECH] utls 依赖不可用，降级为普通 TLS")
            return None
        doh_url, use_proxy = _doh_settings()
        key = f"doh:{doh_url}:{use_proxy}"

    cached = _read_cache(key)
    if cached is not _MISS:
        return cached

    with _inflight.setdefault(key, threading.Lock()):
        # double-checked：等待期间可能已有其他线程填充缓存
        cached = _read_cache(key)
        if cached is not _MISS:
            return cached

        ctx = _query_ech_config() if mode != "manual" else ech_config
        if isinstance(ctx, bytes):
            ctx = _build_ech_context(ctx)
        _write_cache(key, ctx)
        return ctx
