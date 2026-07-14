"""HTTP 客户端基类：统一日志/重试/异常处理

通过封装 httpx.Client / httpx.AsyncClient，在请求前后注入日志钩子，
支持链式配置自定义成功/失败消息，不改变原有 httpx API 行为。

日志策略：
  INFO  — 自定义简短消息 + 技术摘要（METHOD url → status elapsed）
  DEBUG — 请求详情（headers, params, body）+ 响应详情（headers, body）

使用示例::

    from app.utils.http_base import SyncHttpClient

    client = (
        SyncHttpClient(label="Webhook", timeout=10.0)
        .prefix("🔔")
        .success_tpl("推送成功")
        .failure_tpl("推送失败")
    )
    with client:
        resp = client.post(url, json=payload)
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..core.logging import logger
from .http_client import create_async_client, create_sync_client
from .retry import RETRY_EXCEPTIONS, RETRY_STATUS_CODES, compute_backoff_delay


class HttpClientBase:
    """日志与重试的公共逻辑（不直接实例化）。

    子类需实现 ``_create_client()`` 返回底层 httpx 客户端，
    并在 ``request()`` 中调用 ``_log_request`` / ``_log_success`` / ``_log_failure``。
    """

    def __init__(
        self,
        *,
        label: str = "HTTP",
        max_retries: int = 0,
        backoff_base: int = 2,
        backoff_cap: float | None = None,
        **client_kwargs: Any,
    ) -> None:
        self._label = label
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._client_kwargs = client_kwargs
        # 日志钩子默认值
        self._prefix: str = ""
        self._success_msg: Callable[[httpx.Response, str], str] | None = None
        self._failure_msg: Callable[[Exception, str], str] | None = None
        self._success_tpl: str = "请求成功"
        self._failure_tpl: str = "请求失败"
        self._silent_failure: bool = False

    # ===== 链式配置方法 =====

    def prefix(self, text: str) -> HttpClientBase:
        """日志前缀（emoji 或短标签）"""
        self._prefix = f"{text} " if text else ""
        return self

    def success_tpl(self, tpl: str) -> HttpClientBase:
        """成功日志模板，支持纯文本或带变量: {url} {status_code} {elapsed}"""
        self._success_tpl = tpl
        return self

    def failure_tpl(self, tpl: str) -> HttpClientBase:
        """失败日志模板，支持纯文本或带变量: {url} {error} {error_type}"""
        self._failure_tpl = tpl
        return self

    def on_success(self, fn: Callable[[httpx.Response, str], str]) -> HttpClientBase:
        """回调模式：fn(response, url) -> str，完全控制成功日志内容"""
        self._success_msg = fn
        return self

    def on_failure(self, fn: Callable[[Exception, str], str]) -> HttpClientBase:
        """回调模式：fn(error, url) -> str，完全控制失败日志内容"""
        self._failure_msg = fn
        return self

    def silent_failure(self, enabled: bool = True) -> HttpClientBase:
        """静默失败日志（用于批量探测/扫描场景，由调用方统一记录汇总日志）"""
        self._silent_failure = enabled
        return self

    # ===== 日志格式化 =====

    def _format_success(self, response: httpx.Response, url: str) -> str:
        if self._success_msg:
            return self._success_msg(response, url)
        try:
            return self._success_tpl.format(
                url=url,
                status_code=response.status_code,
                elapsed=f"{response.elapsed.total_seconds():.2f}s",
            )
        except KeyError:
            return self._success_tpl

    def _format_failure(self, error: Exception, url: str) -> str:
        if self._failure_msg:
            return self._failure_msg(error, url)
        try:
            return self._failure_tpl.format(
                url=url,
                error=error,
                error_type=type(error).__name__,
            )
        except KeyError:
            return self._failure_tpl

    # ===== 日志输出（分层） =====

    def _log_request(self, method: str, url: str, **kwargs: Any) -> None:
        """DEBUG: 请求详情（method, url, headers, params, body）"""
        parts: list[str] = [f"[{self._label}] 请求 → {method.upper()} {url}"]

        headers = kwargs.get("headers")
        if headers:
            parts.append(f"  Headers: {dict(headers)}")

        params = kwargs.get("params")
        if params:
            parts.append(f"  Params: {dict(params)}")

        json_body = kwargs.get("json")
        if json_body is not None:
            try:
                parts.append(
                    f"  Body(JSON): "
                    f"{_json.dumps(json_body, ensure_ascii=False, default=str)[:500]}"
                )
            except (TypeError, ValueError):
                parts.append("  Body(JSON): <无法序列化>")

        data_body = kwargs.get("data")
        if data_body is not None:
            parts.append(f"  Body(Form): {data_body}")

        logger.debug("\n".join(parts))

    def _log_success(self, response: httpx.Response, method: str, url: str) -> None:
        """INFO: 自定义消息 + 技术摘要; DEBUG: 响应详情"""
        # INFO: 自定义简短消息
        msg = self._format_success(response, url)
        logger.info(f"{self._prefix}{msg}")

        # INFO: 技术摘要
        elapsed = response.elapsed.total_seconds()
        logger.info(
            f"[{self._label}] {method.upper()} {url} "
            f"→ {response.status_code} ({elapsed:.2f}s)"
        )

        # DEBUG: 响应详情
        parts: list[str] = [
            f"[{self._label}] 响应 ← {response.status_code} ({elapsed:.2f}s)"
        ]
        parts.append(f"  Headers: {dict(response.headers)}")
        body = response.text[:500] if response.text else "<空>"
        parts.append(f"  Body: {body}")
        logger.debug("\n".join(parts))

    def _log_failure(self, error: Exception, method: str, url: str) -> None:
        """INFO: 自定义消息 + 技术摘要；silent_failure 时静默"""
        if self._silent_failure:
            return
        # INFO: 自定义简短消息
        msg = self._format_failure(error, url)
        logger.info(f"{self._prefix}{msg}")

        # INFO: 技术摘要
        logger.info(
            f"[{self._label}] {method.upper()} {url} → {type(error).__name__}: {error}"
        )

    def _log_retry(self, attempt: int, reason: str, delay: float) -> None:
        """WARNING: 重试信息"""
        logger.warning(
            f"[{self._label}] 第 {attempt} 次重试，{delay}s 后重试（{reason}）"
        )

    # ===== 重试判断 =====

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code in RETRY_STATUS_CODES

    def _should_retry_exception(self, error: Exception) -> bool:
        return isinstance(error, RETRY_EXCEPTIONS)


class SyncHttpClient(HttpClientBase):
    """同步 HTTP 客户端：封装 httpx.Client，注入日志与重试"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: httpx.Client = create_sync_client(**self._client_kwargs)

    @property
    def client(self) -> httpx.Client:
        """底层 httpx.Client（供需要直接操作的场景使用）"""
        return self._client

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """发起请求，自动日志 + 可选重试"""
        self._log_request(method, url, **kwargs)
        response: httpx.Response | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, url, **kwargs)

                if (
                    self._should_retry_status(response.status_code)
                    and attempt < self._max_retries
                ):
                    delay = compute_backoff_delay(
                        attempt, base=self._backoff_base, cap=self._backoff_cap
                    )
                    self._log_retry(attempt + 1, f"HTTP {response.status_code}", delay)
                    time.sleep(delay)
                    continue

                self._log_success(response, method, url)
                return response

            except Exception as e:
                if self._should_retry_exception(e) and attempt < self._max_retries:
                    delay = compute_backoff_delay(
                        attempt, base=self._backoff_base, cap=self._backoff_cap
                    )
                    self._log_retry(attempt + 1, type(e).__name__, delay)
                    time.sleep(delay)
                    continue
                self._log_failure(e, method, url)
                raise

        # 状态码重试耗尽：返回最后一次响应
        assert response is not None
        self._log_success(response, method, url)
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SyncHttpClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class AsyncHttpClient(HttpClientBase):
    """异步 HTTP 客户端：封装 httpx.AsyncClient，注入日志与重试"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: httpx.AsyncClient = create_async_client(**self._client_kwargs)

    @property
    def client(self) -> httpx.AsyncClient:
        """底层 httpx.AsyncClient（供需要直接操作的场景使用）"""
        return self._client

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """发起请求，自动日志 + 可选重试"""
        self._log_request(method, url, **kwargs)
        response: httpx.Response | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)

                if (
                    self._should_retry_status(response.status_code)
                    and attempt < self._max_retries
                ):
                    delay = compute_backoff_delay(
                        attempt, base=self._backoff_base, cap=self._backoff_cap
                    )
                    self._log_retry(attempt + 1, f"HTTP {response.status_code}", delay)
                    await asyncio.sleep(delay)
                    continue

                self._log_success(response, method, url)
                return response

            except Exception as e:
                if self._should_retry_exception(e) and attempt < self._max_retries:
                    delay = compute_backoff_delay(
                        attempt, base=self._backoff_base, cap=self._backoff_cap
                    )
                    self._log_retry(attempt + 1, type(e).__name__, delay)
                    await asyncio.sleep(delay)
                    continue
                self._log_failure(e, method, url)
                raise

        # 状态码重试耗尽：返回最后一次响应
        assert response is not None
        self._log_success(response, method, url)
        return response

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)

    def stream(self, method: str, url: str, **kwargs: Any):
        """流式请求（仅记录请求日志，响应由调用方处理）"""
        self._log_request(method, url, **kwargs)
        return self._client.stream(method, url, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
