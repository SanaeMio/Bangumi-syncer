"""Bangumi API 失败分类策略（单一来源）测试。

策略表：

| 失败 | 分类 | 熔断 |
|---|---|---|
| 429 / RateLimitTimeoutError | RATE_LIMITED | 否 |
| 连接失败 / 超时 / 5xx | UNREACHABLE | 是 |
| 其它 4xx / 业务异常 | CLIENT_ERROR | 否 |
"""

from __future__ import annotations

import httpx
import pytest

from app.utils.bangumi_api.failure import (
    READ_FALLBACK_ERRORS,
    FailureKind,
    classify_failure,
    should_mark_unreachable,
    write_failure_reason,
)
from app.utils.bangumi_api.rate_limit import RateLimitTimeoutError


def _status_error(status: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        f"HTTP {status}",
        request=httpx.Request("GET", "https://api.example.com"),
        response=httpx.Response(status),
    )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RateLimitTimeoutError("等待 60s 超过预算 30s"), FailureKind.RATE_LIMITED),
        (_status_error(429), FailureKind.RATE_LIMITED),
        (_status_error(500), FailureKind.UNREACHABLE),
        (_status_error(502), FailureKind.UNREACHABLE),
        (_status_error(503), FailureKind.UNREACHABLE),
        (_status_error(504), FailureKind.UNREACHABLE),
        (httpx.ConnectError("refused"), FailureKind.UNREACHABLE),
        (httpx.ReadTimeout("timeout"), FailureKind.UNREACHABLE),
        (_status_error(400), FailureKind.CLIENT_ERROR),
        (_status_error(401), FailureKind.CLIENT_ERROR),
        (_status_error(404), FailureKind.CLIENT_ERROR),
        (ValueError("token expired"), FailureKind.CLIENT_ERROR),
    ],
)
def test_classify_failure(exc: BaseException, expected: FailureKind):
    assert classify_failure(exc) is expected


@pytest.mark.parametrize(
    "exc",
    [
        RateLimitTimeoutError("x"),
        _status_error(429),
    ],
)
def test_rate_limited_does_not_mark_unreachable(exc: BaseException):
    assert should_mark_unreachable(exc) is False


@pytest.mark.parametrize(
    "exc",
    [
        _status_error(500),
        _status_error(502),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("timeout"),
    ],
)
def test_unreachable_failures_mark_breaker(exc: BaseException):
    assert should_mark_unreachable(exc) is True


def test_read_fallback_covers_http_and_rate_limit_errors():
    assert issubclass(RateLimitTimeoutError, READ_FALLBACK_ERRORS)
    assert issubclass(httpx.HTTPError, READ_FALLBACK_ERRORS)


@pytest.mark.parametrize(
    ("exc", "movie", "expected"),
    [
        (RateLimitTimeoutError("x"), False, "rate_limited"),
        (RateLimitTimeoutError("x"), True, "rate_limited_movie_watching"),
        (_status_error(429), False, "http_429"),
        (_status_error(500), True, "http_500_movie_watching"),
        (httpx.ConnectError("x"), False, "connect_error"),
        (httpx.ConnectError("x"), True, "connect_error_movie_watching"),
        (httpx.ReadTimeout("x"), False, "connect_error"),
    ],
)
def test_write_failure_reason(exc: BaseException, movie: bool, expected: str):
    assert write_failure_reason(exc, movie=movie) == expected
