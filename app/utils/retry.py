"""HTTP 请求重试通用工具

提供共享的重试状态码集合、指数退避计算和同步重试函数，
供 bangumi_api / bangumi_data / trakt 等模块统一复用。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from ..core.logging import logger

T = TypeVar("T")

# 触发重试的 HTTP 状态码
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# HTTP 请求重试时捕获的异常类型
RETRY_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.HTTPError,
)


def compute_backoff_delay(
    attempt: int, *, base: int = 2, cap: int | None = None
) -> float:
    """计算指数退避延迟

    Args:
        attempt: 当前重试次数（从 0 开始）
        base: 退避基数（默认 2，即 base^attempt: 1, 2, 4, 8...）
        cap: 延迟上限（秒），None 表示无上限

    Returns:
        延迟秒数
    """
    delay = float(base**attempt)
    if cap is not None:
        delay = min(delay, cap)
    return delay


def http_retry_sync(
    do_request: Callable[[], httpx.Response],
    *,
    max_retries: int = 3,
    backoff_base: int = 2,
    backoff_cap: int | None = None,
    label: str = "HTTP 请求",
) -> httpx.Response:
    """同步 HTTP 请求重试

    do_request 应返回 httpx.Response。如果状态码在 RETRY_STATUS_CODES 中，
    或抛出 RETRY_EXCEPTIONS 中的异常，则按指数退避重试。

    重试耗尽时：
    - 状态码重试：返回最后一个响应（调用方自行 raise_for_status）
    - 异常重试：重新抛出异常
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = do_request()
            if response.status_code in RETRY_STATUS_CODES:
                if attempt < max_retries:
                    delay = compute_backoff_delay(
                        attempt, base=backoff_base, cap=backoff_cap
                    )
                    logger.error(
                        f"HTTP {response.status_code} 错误，"
                        f"第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )
                    time.sleep(delay)
                    continue
                logger.error(
                    f"HTTP {response.status_code} 错误，已达到最大重试次数 {max_retries}"
                )
            return response
        except RETRY_EXCEPTIONS as e:
            last_exc = e
            if attempt < max_retries:
                delay = compute_backoff_delay(
                    attempt, base=backoff_base, cap=backoff_cap
                )
                logger.error(
                    f"{label}异常: {e}，"
                    f"第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                )
                time.sleep(delay)
                continue
            logger.error(f"{label}异常: {e}，已达到最大重试次数 {max_retries}")
            raise
    raise last_exc  # type: ignore[misc]  # pragma: no cover
