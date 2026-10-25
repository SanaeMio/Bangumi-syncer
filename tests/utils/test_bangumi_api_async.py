"""Bangumi API 异步请求层测试（agent 语境；与同步路径共享进程级限流器）。

覆盖：
- 请求前经 ``acquire_async`` 预约令牌（在发出请求之前），与同步 ``acquire``
  共享同一令牌桶（sync 消耗 → async 感知、快速失败且不发请求）；
- 响应后按状态通知限流器（429 冻结 / 成功重置升级计数）；
- 与同步语义对齐：不可达短路与标记、网络错误降级、重试状态码升格为异常；
- 3 个只读异步方法（search / get_subject / get_related_subjects）：
  缓存命中不请求、鉴权头按需注入（search 不带 Authorization）。
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api.rate_limit import (
    RateLimiter,
    RateLimitTimeoutError,
    reset_bgm_rate_limiter,
)


def _mock_response(status_code: int = 200, json_data: Any = None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.headers = headers if headers is not None else {}
    resp.text = ""
    resp.elapsed.total_seconds.return_value = 0.01
    resp.request = MagicMock()
    return resp


class _RecordingLimiter:
    """记录异步预约/通知的假限速器（鸭子类型注入 get_bgm_rate_limiter）。"""

    def __init__(self) -> None:
        self.acquire_async_calls = 0
        self.notified: list[float | None] = []
        self.success_calls = 0

    async def acquire_async(self, timeout: float | None = None) -> float:
        self.acquire_async_calls += 1
        return 0.0

    def acquire(self, timeout: float | None = None) -> float:
        return 0.0

    def notify_rate_limited(self, retry_after: float | None = None) -> None:
        self.notified.append(retry_after)

    def notify_success(self) -> None:
        self.success_calls += 1


class _FakeAsyncHttpClient:
    """伪 AsyncHttpClient：支持链式配置与 async with，按队列返回响应/异常。"""

    instances: list[_FakeAsyncHttpClient] = []
    responses: list[Any] = []
    requests: list[dict] = []

    def __init__(self, **kwargs: Any) -> None:
        self.client_kwargs = kwargs
        _FakeAsyncHttpClient.instances.append(self)

    # 链式配置（与真实 AsyncHttpClient 一致，返回 self）
    def prefix(self, _text: str):
        return self

    def success_tpl(self, _text: str):
        return self

    def failure_tpl(self, _text: str):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        type(self).requests.append(
            {
                "method": method,
                "url": url,
                "kwargs": kwargs,
                "headers": self.client_kwargs.get("headers") or {},
            }
        )
        item = type(self).responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _async_client_fixture():
    """重置伪 client 状态与限速器单例（防用例间污染）。"""
    _FakeAsyncHttpClient.instances = []
    _FakeAsyncHttpClient.responses = []
    _FakeAsyncHttpClient.requests = []
    yield
    reset_bgm_rate_limiter(None)


def _install_limiter(limiter: Any) -> None:
    reset_bgm_rate_limiter(cast(RateLimiter, limiter))


def _patch_async_client():
    return patch(
        "app.utils.bangumi_api.async_http_layer.AsyncHttpClient",
        new=_FakeAsyncHttpClient,
    )


# ---------------------------------------------------------------------------
# search_async：预约 → 请求 → 解析；鉴权头不带
# ---------------------------------------------------------------------------


async def test_search_async_acquires_token_then_requests():
    """async 请求前恰一次 acquire_async；search 不带 Authorization 头。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [
        _mock_response(200, {"data": [{"id": 1, "name": "x"}]})
    ]

    api = BangumiApi(access_token="tok")
    with _patch_async_client():
        result = await api.search_async("t")

    assert result == [{"id": 1, "name": "x"}]
    assert limiter.acquire_async_calls == 1
    assert limiter.success_calls == 1
    req = _FakeAsyncHttpClient.requests[0]
    assert req["method"] == "POST"
    assert req["url"].endswith("/search/subjects")
    assert "Authorization" not in req["headers"]
    assert req["headers"].get("User-Agent")


async def test_search_async_cache_hit_skips_request_and_acquire():
    """二次相同查询命中实例缓存：不再预约、不再请求。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [_mock_response(200, {"data": [{"id": 7}]})]

    api = BangumiApi()
    with _patch_async_client():
        first = await api.search_async("same")
        second = await api.search_async("same")

    assert first == second == [{"id": 7}]
    assert limiter.acquire_async_calls == 1
    assert len(_FakeAsyncHttpClient.requests) == 1


async def test_search_async_unreachable_short_circuits_without_request():
    """不可达 TTL 内直接降级空结果：不预约、不请求。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    api = BangumiApi()
    api.mark_api_unreachable()

    with _patch_async_client():
        result = await api.search_async("t")

    assert result == []
    assert limiter.acquire_async_calls == 0
    assert _FakeAsyncHttpClient.requests == []


async def test_search_async_network_error_returns_empty_and_marks_unreachable():
    """网络错误：降级空结果并标记 API 不可达（与同步语义对齐）。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [httpx.ConnectError("boom")] * 4

    api = BangumiApi()
    with (
        _patch_async_client(),
        patch("app.utils.bangumi_api.async_http_layer.asyncio.sleep", new=AsyncMock()),
    ):
        result = await api.search_async("t")

    assert result == []
    assert api.is_api_unreachable() is True


# ---------------------------------------------------------------------------
# get_subject_async / get_related_subjects_async：鉴权头、解析、归档短路
# ---------------------------------------------------------------------------


async def test_get_subject_async_sends_auth_header_and_parses():
    """带鉴权请求注入 Authorization；返回 dict 原样透传并写缓存。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [_mock_response(200, {"id": 123})]

    api = BangumiApi(access_token="tok")
    with _patch_async_client():
        result = await api.get_subject_async(123)

    assert result == {"id": 123}
    req = _FakeAsyncHttpClient.requests[0]
    assert req["headers"].get("Authorization") == "Bearer tok"
    assert req["url"].endswith("/subjects/123")


async def test_get_subject_async_archive_hit_skips_request():
    """归档短路命中：直接返回本地数据（不预约、不请求）。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    api = BangumiApi()
    shortcut = MagicMock()
    shortcut.hit = True
    shortcut.data = {"id": 9, "name": "archive"}

    # patch.object 保证退出即恢复（_archive 为全局单例，直接赋值会跨测试污染）
    with (
        _patch_async_client(),
        patch.object(api._archive, "try_get_subject", return_value=shortcut),
    ):
        result = await api.get_subject_async(9)

    assert result == {"id": 9, "name": "archive"}
    assert limiter.acquire_async_calls == 0
    assert _FakeAsyncHttpClient.requests == []


async def test_get_related_subjects_async_accepts_list_payload():
    """关联条目接口可返回 list：原样透传。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [
        _mock_response(200, [{"id": 2, "relation": "续集"}])
    ]

    api = BangumiApi()
    shortcut = MagicMock()
    shortcut.hit = False

    with (
        _patch_async_client(),
        patch("app.utils.bangumi_api.async_http_layer.asyncio.sleep", new=AsyncMock()),
        patch.object(api._archive, "try_get_related_subjects", return_value=shortcut),
    ):
        result = await api.get_related_subjects_async(1)

    assert result == [{"id": 2, "relation": "续集"}]


async def test_get_related_subjects_async_network_error_returns_empty():
    """关联条目网络错误：降级空列表。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [httpx.ReadTimeout("slow")] * 4

    api = BangumiApi()
    shortcut = MagicMock()
    shortcut.hit = False

    with (
        _patch_async_client(),
        patch.object(api._archive, "try_get_related_subjects", return_value=shortcut),
    ):
        result = await api.get_related_subjects_async(1)

    assert result == []


# ---------------------------------------------------------------------------
# 限流通知与共享令牌桶
# ---------------------------------------------------------------------------


async def test_async_request_notifies_limiter_on_429():
    """429 响应 → notify_rate_limited（携带 Retry-After），并升格为异常降级。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [
        _mock_response(429, {"error": "rate"}, headers={"Retry-After": "5"})
    ]

    api = BangumiApi()
    with _patch_async_client():
        result = await api.search_async("t")

    assert result == []  # HTTPStatusError 被降级为空结果
    assert limiter.notified == [5.0]
    assert api.is_api_unreachable() is True


async def test_search_async_shares_bucket_with_sync_and_fails_fast():
    """共享令牌桶：同步侧已耗尽令牌 → async 快速失败（不发出请求）。"""
    limiter = RateLimiter(rate=0.01, burst=1)
    _install_limiter(limiter)
    limiter.acquire()  # 同步客户端先消耗唯一令牌（模拟同步设施）

    api = BangumiApi()
    with _patch_async_client():
        with pytest.raises(RateLimitTimeoutError):
            await api.search_async("t")

    # 快速失败（C1 口径）：请求未发出
    assert _FakeAsyncHttpClient.requests == []


# ---------------------------------------------------------------------------
# 重试与限速：每次尝试都经过 limiter；429 快速失败
# ---------------------------------------------------------------------------


async def test_async_429_does_not_retry_and_notifies_once():
    """429：通知 limiter 后快速失败，不进行重试（只创建 1 个 client）。"""
    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [
        _mock_response(429, {"error": "rate"}, headers={"Retry-After": "5"}),
        _mock_response(200, {"data": [{"id": 1}]}),
    ]

    api = BangumiApi()
    with (
        _patch_async_client(),
        patch("app.utils.bangumi_api.async_http_layer.asyncio.sleep", new=AsyncMock()),
    ):
        result = await api.search_async("t")

    assert result == [], "429 应快速失败并降级为空结果"
    assert len(_FakeAsyncHttpClient.instances) == 1, "429 不应重试"
    assert limiter.acquire_async_calls == 1
    assert limiter.notified == [5.0]


async def test_async_5xx_retries_with_acquire_each_attempt():
    """5xx：外层重试，每次尝试前 acquire_async（共 4 次尝试）。"""
    from unittest.mock import AsyncMock

    limiter = _RecordingLimiter()
    _install_limiter(limiter)
    _FakeAsyncHttpClient.responses = [
        _mock_response(500),
        _mock_response(502),
        _mock_response(503),
        _mock_response(200, {"data": [{"id": 1, "name": "x"}]}),
    ]

    api = BangumiApi()
    with (
        _patch_async_client(),
        patch("app.utils.bangumi_api.async_http_layer.asyncio.sleep", new=AsyncMock()),
    ):
        result = await api.search_async("t")

    assert result == [{"id": 1, "name": "x"}]
    assert len(_FakeAsyncHttpClient.instances) == 4
    assert limiter.acquire_async_calls == 4, "每次尝试前都必须经过限速器"
