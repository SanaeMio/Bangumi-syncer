"""ECH 配置提供器（app/utils/ech.py）单元测试

覆盖：配置读取、域名匹配、DoH dns-json / wireformat 解析、成功后缓存
与 TTL、失败降级、manual 模式、工厂 ech 参数透传。
"""

from __future__ import annotations

import base64
import struct
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core.config import config_manager
from app.utils import ech as ech_module
from app.utils.ech import (
    _build_dns_query,
    _parse_dns_json,
    _parse_wire_response,
    get_ech_ssl_context,
    is_ech_host,
)
from app.utils.http_client import create_async_client, create_sync_client

# 语法合法的 ECHConfigList（public_name=cloudflare-ech.com），
# 用于 utls set_ech_configs 解析校验；密钥为任意占位（客户端不校验）。


def _valid_ech_config() -> bytes:
    """构造合法 ECHConfigList（避免依赖上游样例字符串的稳定性）。"""
    public_name = b"cloudflare-ech.com"
    contents = (
        b"\x00"  # config_id
        + struct.pack(">H", 0x0020)  # kem_id = X25519
        + struct.pack(">H", 32)
        + b"\x00" * 32  # 占位 public_key
        + struct.pack(">H", 4)
        + struct.pack(">HH", 1, 3)  # cipher_suites: HKDF-SHA256+AES128GCM / +CHACHA20
        + b"\x40"  # maximum_name_length
        + bytes([len(public_name)])
        + public_name
        + struct.pack(">H", 0)  # extensions
    )
    config = struct.pack(">HH", 0xFDFD, len(contents)) + contents
    return struct.pack(">H", len(config)) + config


VALID_ECH = _valid_ech_config()
VALID_ECH_B64 = base64.b64encode(VALID_ECH).decode()


@pytest.fixture(autouse=True)
def _clear_ech_cache(monkeypatch):
    """每个测试前清空 ECH 模块缓存（ctx 缓存 / utls 可用性）。"""
    ech_module._cache.clear()
    ech_module._utls_available = None
    yield
    ech_module._cache.clear()


def _patch_dev_config(values: dict):
    """patch config_manager.get：命中 [dev] 指定选项时返回给定值，其余走原逻辑。"""
    real_get = config_manager.get

    def fake_get(section, option, fallback=None):
        if section == "dev" and option in values:
            return values[option]
        return real_get(section, option, fallback=fallback)

    return patch.object(config_manager, "get", side_effect=fake_get)


# ── 配置读取与域名匹配 ───────────────────────────────────────────────────


def test_ech_mode_off_default():
    """未配置时 ech_mode 为 off，上下文字段返回 None（不启用）。"""
    with _patch_dev_config({}):
        assert ech_module.ech_mode() == "off"
        assert get_ech_ssl_context() is None


def test_ech_mode_manual_missing_config_returns_none():
    """manual 模式但未提供 ech_ech_config → 降级 None。"""
    with _patch_dev_config({"ech_mode": "manual"}):
        assert get_ech_ssl_context() is None


def test_ech_mode_manual_invalid_base64_returns_none():
    """manual 模式提供非法 base64 → 降级 None。"""
    with _patch_dev_config(
        {"ech_mode": "manual", "ech_ech_config": "!!!not-base64!!!"}
    ):
        assert get_ech_ssl_context() is None


def test_is_ech_host_default_targets():
    """默认目标域名：bgm.tv 及子域命中，外部域不命中。"""
    with _patch_dev_config({}):
        assert is_ech_host("bgm.tv")
        assert is_ech_host("api.bgm.tv")
        assert is_ech_host("chii.in")
        assert is_ech_host("next.bgm.tv")
        assert is_ech_host("lain.bgm.tv")
        assert not is_ech_host("github.com")
        assert not is_ech_host("evil-bgm.tv")  # 后缀匹配需带点
        assert not is_ech_host("")


def test_is_ech_host_custom_targets():
    """自定义 ech_hosts：覆盖默认列表。"""
    with _patch_dev_config({"ech_hosts": "example.com, foo.org"}):
        assert is_ech_host("example.com")
        assert is_ech_host("api.example.com")
        assert is_ech_host("foo.org")
        assert not is_ech_host("bgm.tv")


def test_is_ech_host_blank_falls_back_to_default():
    """ech_hosts 留空时回退默认列表。"""
    with _patch_dev_config({"ech_hosts": " "}):
        assert is_ech_host("bgm.tv")
        assert not is_ech_host("github.com")


# ── DoH 解析 ────────────────────────────────────────────────────────────


def test_parse_dns_json_extracts_ech():
    """dns-json 响应中提取 ech= 并解码。"""
    payload = "1 . ech=" + VALID_ECH_B64 + " alpn=h2,h3"
    text = f'{{"Answer": [{{"name": "cloudflare-ech.com.", "type": 65, "data": "{payload}"}}]}}'
    assert _parse_dns_json(text) == VALID_ECH


def test_parse_dns_json_no_ech_returns_none():
    """无 ech= 字段返回 None。"""
    text = '{"Answer": [{"name": "cloudflare-ech.com.", "type": 65, "data": "1 . "}]}'
    assert _parse_dns_json(text) is None


def test_parse_dns_json_invalid_base64_returns_none():
    """ech= 含非法字符（被 b64decode 容忍为空）→ 视为无配置。"""
    text = '{"Answer": [{"name": "x.", "type": 65, "data": "1 . ech=@@@@"}]}'
    assert not _parse_dns_json(text)


def test_build_dns_query_structure():
    """wireformat 查询含 HTTPS QTYPE(65) 与 OPT。"""
    q = _build_dns_query()
    assert len(q) >= 32
    assert struct.unpack(">H", q[4:6])[0] == 1  # qdcount
    assert b"cloudflare" in q and b"\x03ech\x03com\x00" in q


def _build_wire_response() -> bytes:
    """构造 RFC 8484 响应：1 条 HTTPS(65) 记录，含 ech 参数(key=5)与 alpn(key=1)。"""

    def qname(name: str) -> bytes:
        return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"

    rdata = (
        struct.pack(">H", 1)  # SVCB priority=1
        + b"\x00"  # target = root
        + struct.pack(">HH", 5, len(VALID_ECH))  # ech
        + VALID_ECH
        + struct.pack(">HH", 1, 9)  # alpn
        + b"\x03h2\x03h3"
    )
    answer = (
        b"\xc0\x0c"  # NAME 压缩指针 → question 的 QNAME
        + struct.pack(">HHIH", 65, 1, 60, len(rdata))
        + rdata
    )
    header = struct.pack(">HHHHHH", 0x1234, 0x8180, 1, 1, 0, 0)
    question = qname("cloudflare-ech.com") + struct.pack(">HH", 65, 1)
    return header + question + answer


def test_parse_wire_response_extracts_ech():
    assert _parse_wire_response(_build_wire_response()) == VALID_ECH


def test_parse_wire_response_garbage_returns_none():
    assert _parse_wire_response(b"") is None
    assert _parse_wire_response(b"\x00" * 12) is None


# ── 上下文获取：成功 / 降级 / 缓存 ───────────────────────────────────────


def test_get_ech_context_doh_success():
    """doh 模式：fetch 成功 → 构建 utls 上下文（真实 utls，无需网络）。"""
    ok_json = (
        '{"Answer": [{"name": "cloudflare-ech.com.", "type": 65, '
        '"data": "1 . ech=' + VALID_ECH_B64 + '"}]}'
    )
    with (
        _patch_dev_config({"ech_mode": "doh"}),
        patch("app.utils.ech._fetch_doh_json", return_value=ok_json),
    ):
        ctx = get_ech_ssl_context()
        assert ctx is not None
        assert hasattr(ctx, "set_ech_configs")

        # doh 模式 fetch 失败 → 降级 None（不抛异常）
        with (
            patch.object(
                ech_module,
                "_fetch_doh_json",
                side_effect=MagicMock(side_effect=httpx.ConnectError("x")),
            ),
            patch.object(
                ech_module, "_fetch_doh_wire", side_effect=httpx.ConnectError("x")
            ),
        ):
            ech_module._cache.clear()
            assert get_ech_ssl_context() is None


def test_get_ech_context_cache_and_ttl(monkeypatch):
    """缓存生效：TTL 内不重复 fetch；过期后重新 fetch。"""
    fetch_calls = {"n": 0}

    def fake_fetch(doh_url: str, proxy: str | None) -> str:
        fetch_calls["n"] += 1
        return (
            '{"Answer": [{"name": "cloudflare-ech.com.", "type": 65, '
            '"data": "1 . ech=' + VALID_ECH_B64 + '"}]}'
        )

    with (
        _patch_dev_config({"ech_mode": "doh"}),
        patch.object(
            ech_module, "_fetch_doh_json", side_effect=MagicMock(side_effect=fake_fetch)
        ),
        patch.object(
            ech_module, "_fetch_doh_wire", side_effect=httpx.ConnectError("x")
        ),
    ):
        assert len(ech_module._cache) == 0, "fixture 应已清空缓存"
        assert get_ech_ssl_context() is not None
        assert get_ech_ssl_context() is not None
        assert fetch_calls["n"] == 1  # 第二次命中缓存

        # 模拟 TTL 过期
        monkeypatch.setattr(ech_module, "CONTEXT_TTL_SECONDS", -1)
        ech_module._cache.clear()
        assert get_ech_ssl_context() is not None
        assert fetch_calls["n"] == 2


def test_get_ech_context_manual_success():
    """manual 模式：直接使用 ech_ech_config 构建上下文。"""
    with _patch_dev_config({"ech_mode": "manual", "ech_ech_config": VALID_ECH_B64}):
        ctx = get_ech_ssl_context()
        assert ctx is not None


def test_get_ech_context_manual_build_failure_degrades():
    """manual 模式：配置合法但 utls 构建失败 → 降级 None。"""
    with (
        _patch_dev_config({"ech_mode": "manual", "ech_ech_config": VALID_ECH_B64}),
        patch("app.utils.ech._build_ech_context", return_value=None),
        patch("app.utils.ech._utls_available_check", return_value=False),
    ):
        assert get_ech_ssl_context() is None


def test_doh_use_proxy_enabled_passes_proxy(monkeypatch):
    """ech_doh_use_proxy=True 时 DoH 查询携带 script_proxy。"""
    captured: dict = {}

    def fake_fetch(doh_url: str, proxy: str | None) -> str:
        captured["proxy"] = proxy
        return '{"Answer": []}'

    with (
        _patch_dev_config(
            {
                "ech_mode": "doh",
                "ech_doh_use_proxy": True,
                "script_proxy": "http://127.0.0.1:7890",
            }
        ),
        patch("app.utils.ech._fetch_doh_json", side_effect=fake_fetch),
        patch("app.utils.ech._fetch_doh_wire", side_effect=httpx.ConnectError("x")),
    ):
        get_ech_ssl_context()
        assert captured["proxy"] == "http://127.0.0.1:7890"


def test_doh_use_proxy_false_passes_none(monkeypatch):
    """ech_doh_use_proxy 关闭（含 "False" 字符串）→ DoH 直连。"""
    captured: dict = {}

    def fake_fetch(doh_url: str, proxy: str | None) -> str:
        captured["proxy"] = proxy
        return '{"Answer": []}'

    with (
        _patch_dev_config(
            {
                "ech_mode": "doh",
                "ech_doh_use_proxy": "False",
                "script_proxy": "http://127.0.0.1:7890",
            }
        ),
        patch("app.utils.ech._fetch_doh_json", side_effect=fake_fetch),
        patch("app.utils.ech._fetch_doh_wire", side_effect=httpx.ConnectError("x")),
    ):
        get_ech_ssl_context()
        assert captured["proxy"] is None


# ── 工厂 ech 参数透传 ────────────────────────────────────────────────────


def test_factory_ech_disabled_keeps_verify():
    """ech=False / "off" / "0" 时 verify 保持原值，不触发 ECH 获取。"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client()
        assert mock_client_cls.call_args.kwargs["verify"] is True
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client(ech="off")
        assert mock_client_cls.call_args.kwargs["verify"] is True
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client(ech=False)
        assert mock_client_cls.call_args.kwargs["verify"] is True


def test_factory_ech_enabled_replaces_verify():
    """ech 开启且上下文可用 → verify 替换为 utls 上下文。"""
    fake_ctx = MagicMock()
    with (
        patch("app.utils.http_client.httpx.Client") as mock_client_cls,
        patch("app.utils.ech.get_ech_ssl_context", return_value=fake_ctx),
    ):
        create_sync_client(ech="doh")
        assert mock_client_cls.call_args.kwargs["verify"] is fake_ctx


def test_factory_ech_degraded_keeps_verify():
    """ech 开启但上下文不可用（降级）→ verify 保持原值。"""
    with (
        patch("app.utils.http_client.httpx.Client") as mock_client_cls,
        patch("app.utils.ech.get_ech_ssl_context", return_value=None),
    ):
        create_sync_client(ech=True)
        assert mock_client_cls.call_args.kwargs["verify"] is True


def test_factory_async_ech_passthrough():
    """异步工厂同样支持 ech 参数。"""
    fake_ctx = MagicMock()
    with (
        patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls,
        patch("app.utils.ech.get_ech_ssl_context", return_value=fake_ctx),
    ):
        create_async_client(ech="manual")
        assert mock_client_cls.call_args.kwargs["verify"] is fake_ctx


# ── 请求日志 [ECH] 前缀 ────────────────────────────────────────────────────


def _fake_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.elapsed.total_seconds.return_value = 0.1
    resp.text = "{}"
    resp.headers = {}
    return resp


def test_request_log_ech_tag_on_target_host():
    """命中 ech_hosts 的请求，日志行带 [ECH] 前缀（要求 ECH 上下文实际生效）。"""
    from app.utils import http_base as http_base_module
    from app.utils.http_base import SyncHttpClient

    fake_ctx = MagicMock()
    fake_client = MagicMock()
    fake_client.request.return_value = _fake_response()
    with (
        patch("app.utils.ech.get_ech_ssl_context", return_value=fake_ctx),
        patch(
            "app.utils.http_base.create_sync_client", return_value=fake_client
        ) as mock_factory,
        patch.object(http_base_module.logger, "debug") as mock_debug,
    ):
        client = SyncHttpClient(label="Bangumi", ech="doh")
        assert client._ech_active is True
        client.get("https://api.bgm.tv/v0/subjects/1")

    assert mock_factory.call_args.kwargs["ech"] == "doh"
    debug_calls = [str(c[0]) for c in mock_debug.call_args_list if c.args]
    assert any(
        "[ECH] [Bangumi] 请求 → GET https://api.bgm.tv/v0/subjects/1" in s
        for s in debug_calls
    )
    assert any(
        "[ECH] [Bangumi] GET https://api.bgm.tv/v0/subjects/1 → 200" in s
        for s in debug_calls
    )
    assert any("[ECH] [Bangumi] 响应 ← 200" in s for s in debug_calls)


def test_request_log_no_ech_tag_off_domain():
    """非 ech_hosts 域名（或 ECH 未生效）不打 [ECH] 前缀。"""
    from app.utils import http_base as http_base_module
    from app.utils.http_base import SyncHttpClient

    fake_client = MagicMock()
    fake_client.request.return_value = _fake_response()
    with (
        patch("app.utils.ech.get_ech_ssl_context", return_value=MagicMock()),
        patch("app.utils.http_base.create_sync_client", return_value=fake_client),
        patch.object(http_base_module.logger, "debug") as mock_debug,
    ):
        client = SyncHttpClient(label="Bangumi", ech="doh")
        client.get("https://example.com/api")

    debug_calls = [str(c[0]) for c in mock_debug.call_args_list if c.args]
    assert all("[ECH]" not in s for s in debug_calls)


def test_request_log_no_ech_tag_when_context_unavailable():
    """ECH 上下文构建失败（降级普通 TLS）→ 即使命中域名也不打 [ECH]。"""
    from app.utils import http_base as http_base_module
    from app.utils.http_base import SyncHttpClient

    fake_client = MagicMock()
    fake_client.request.return_value = _fake_response()
    with (
        patch("app.utils.ech.get_ech_ssl_context", return_value=None),
        patch("app.utils.http_base.create_sync_client", return_value=fake_client),
        patch.object(http_base_module.logger, "debug") as mock_debug,
    ):
        client = SyncHttpClient(label="Bangumi", ech="doh")
        assert client._ech_active is False
        client.get("https://api.bgm.tv/v0/subjects/1")

    debug_calls = [str(c[0]) for c in mock_debug.call_args_list if c.args]
    assert all("[ECH]" not in s for s in debug_calls)
