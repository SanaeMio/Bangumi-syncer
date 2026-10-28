"""Bangumi API 异步请求层（供 agent 等异步语境使用）。

与同步 :class:`HttpLayerMixin` 的请求语义对齐：

- 请求前经进程级限流器预约（``acquire_async``，与同步 ``acquire`` **共享同一令牌桶**）；
- 响应后按状态通知限流器（429 冻结 / 成功重置升级计数）；
- 网络异常与「重试耗尽仍失败」→ 标记 API 不可达（与同步一致，读路径降级）；
- 传输用 :class:`AsyncHttpClient`（内置重试/日志，与同步 ``SyncHttpClient`` 对称），
  **每次请求临时创建**（``async with``，退出即关闭）：``httpx.AsyncClient`` 的连接池
  绑定创建它的 event loop（测试逐用例新建 loop），临时创建规避跨 loop 复用与关闭
  义务；代理直连回退等同步侧复杂路径暂不覆盖（本层只承载最小只读方法）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ...core.logging import logger
from ..retry import RETRY_STATUS_CODES, compute_backoff_delay
from ._http_client import create_bgm_async_client
from .http_layer import (
    BGM_REQUEST_BACKOFF_BASE,
    BGM_REQUEST_MAX_ATTEMPTS,
    BGM_USER_AGENT,
    notify_limiter_from_response,
)
from .rate_limit import get_bgm_rate_limiter


class AsyncHttpLayer:
    """异步请求驱动（混入 BangumiApi；与同步路径共享 limiter 与实例状态）。"""

    # 宿主类（``BangumiApi``）提供的实例状态（仅类型注解，赋值在 __init__）
    ssl_verify: bool
    access_token: str | None
    http_proxy: str | None
    ech_mode: str

    async def _request_async(
        self, method: str, url: str, *, auth: bool = True, **kwargs: Any
    ) -> httpx.Response:
        """异步请求：每次尝试前预约令牌 → 发送 → 响应通知。

        - 每次尝试（含重试）前 ``acquire_async``，响应后 ``notify_limiter_from_response``；
        - 429：首次即冻结令牌桶并**不再重试**（C1 快速失败）；
        - 5xx：按指数退避重试，重试前仍预约令牌；
        - 网络异常：按退避重试；重试耗尽后标记不可达并原样抛出；
        - 限速预约失败（``RateLimitTimeoutError``）直接冒泡（不发出请求）。
        """
        headers = {
            "Accept": "application/json",
            "User-Agent": BGM_USER_AGENT,
        }
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        for attempt in range(BGM_REQUEST_MAX_ATTEMPTS):
            # 预约在创建 client / 发请求之前；限速超时直接冒泡（不发出请求）
            await get_bgm_rate_limiter().acquire_async()

            try:
                # 链式配置在工厂内完成；每次请求临时创建（连接池绑定 event loop）
                client = create_bgm_async_client(
                    proxy=self.http_proxy,
                    verify=self.ssl_verify,
                    ech=self.ech_mode,
                    headers=headers,
                )
                async with client:
                    res = await client.request(method, url, **kwargs)
            except httpx.HTTPError:
                if attempt < BGM_REQUEST_MAX_ATTEMPTS - 1:
                    delay = compute_backoff_delay(
                        attempt, base=BGM_REQUEST_BACKOFF_BASE
                    )
                    logger.warning(
                        f"📚 Bangumi 异步请求异常，"
                        f"第 {attempt + 1}/{BGM_REQUEST_MAX_ATTEMPTS} 次尝试，"
                        f"{delay}秒后重试"
                    )
                    await asyncio.sleep(delay)
                    continue
                # 连接层错误：与同步一致标记不可达（读写降级），异常原样上抛
                self.mark_api_unreachable()
                raise

            # 429 冻结令牌桶 / 成功重置升级计数（与同步同源；每次响应都通知）
            notify_limiter_from_response(res)

            if res.status_code == 429:
                logger.warning("📚 Bangumi API HTTP 429（快速失败，不重试）")
                # 只冻结令牌桶，不标记 300s 不可达；否则搜索会被 TTL 短路，
                # Retry-After / 指数退避无法生效。
                raise httpx.HTTPStatusError(
                    "HTTP 429 错误，令牌桶已进入冷却（快速失败）",
                    request=res.request,
                    response=res,
                )

            if (
                res.status_code in RETRY_STATUS_CODES
                and attempt < BGM_REQUEST_MAX_ATTEMPTS - 1
            ):
                delay = compute_backoff_delay(attempt, base=BGM_REQUEST_BACKOFF_BASE)
                logger.warning(
                    f"📚 Bangumi API HTTP {res.status_code}，"
                    f"第 {attempt + 1}/{BGM_REQUEST_MAX_ATTEMPTS} 次尝试，"
                    f"{delay}秒后重试"
                )
                await asyncio.sleep(delay)
                continue

            if res.status_code in RETRY_STATUS_CODES:
                # 重试耗尽仍失败（5xx）：与同步一致标记不可达并升格为异常
                logger.warning(
                    f"📚 Bangumi API HTTP {res.status_code}（重试耗尽），标记不可达"
                )
                self.mark_api_unreachable()
                raise httpx.HTTPStatusError(
                    f"HTTP {res.status_code} 错误，已达到最大重试次数",
                    request=res.request,
                    response=res,
                )
            return res

    def mark_api_unreachable(self) -> None:
        """由宿主类（``BangumiApi``）实现：标记 API 不可达（TTL 内走降级）。"""
        ...
