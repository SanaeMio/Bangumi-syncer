"""Bangumi API 失败分类与降级策略（单一来源）。

策略表：

===============  =================  ==========
失败             分类                熔断
===============  =================  ==========
429 / 限速超时    RATE_LIMITED       否
连接失败 / 超时 / 5xx  UNREACHABLE   是
其它 4xx / 业务异常  CLIENT_ERROR      否
===============  =================  ==========

读路径：``READ_FALLBACK_ERRORS`` → 返回空结果；
写路径：``RATE_LIMITED`` / ``UNREACHABLE`` → 入队，后者额外熔断；
``CLIENT_ERROR`` → 原样上抛。
"""

from __future__ import annotations

from enum import Enum

import httpx

from .rate_limit import RateLimitTimeoutError

RATE_LIMIT_STATUS = 429
UNREACHABLE_STATUSES = frozenset({500, 502, 503, 504})

#: 读路径可降级为“空结果”的异常（网络/状态错误 + 限速预约超时）
READ_FALLBACK_ERRORS = (httpx.HTTPError, RateLimitTimeoutError)


class FailureKind(Enum):
    """失败语义分类（决定是否熔断、读空还是写入队）。"""

    RATE_LIMITED = "rate_limited"
    UNREACHABLE = "unreachable"
    CLIENT_ERROR = "client_error"


def classify_status(status: int) -> FailureKind:
    """HTTP 状态码 → 失败分类。"""
    if status == RATE_LIMIT_STATUS:
        return FailureKind.RATE_LIMITED
    if status in UNREACHABLE_STATUSES or status >= 500:
        return FailureKind.UNREACHABLE
    return FailureKind.CLIENT_ERROR


def classify_failure(exc: BaseException) -> FailureKind:
    """异常 → 失败分类。"""
    if isinstance(exc, RateLimitTimeoutError):
        return FailureKind.RATE_LIMITED
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code if response is not None else 0
        return classify_status(status)
    if isinstance(exc, httpx.HTTPError):
        # 连接失败 / 超时 / 传输层异常
        return FailureKind.UNREACHABLE
    return FailureKind.CLIENT_ERROR


def should_mark_unreachable(exc: BaseException) -> bool:
    """该失败是否应触发 300s 熔断（仅 UNREACHABLE）。"""
    return classify_failure(exc) is FailureKind.UNREACHABLE


def write_failure_reason(exc: BaseException, *, movie: bool = False) -> str:
    """写路径入队原因（保持既有 reason 文案）。"""
    if isinstance(exc, RateLimitTimeoutError):
        reason = "rate_limited"
    elif isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code if response is not None else 0
        reason = f"http_{status}"
    elif isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        reason = "connect_error"
    else:
        reason = "unreachable"
    return f"{reason}_movie_watching" if movie else reason
