"""Bangumi 专用 HTTP client 工厂。

**内部重试必须为 0**：Bangumi 的重试统一由
:meth:`~app.utils.bangumi_api.http_layer.HttpLayerMixin._request_with_retry` /
:meth:`~app.utils.bangumi_api.async_http_layer.AsyncHttpLayer._request_async`
负责，保证每次尝试都先经过进程级令牌桶（``acquire`` / ``acquire_async``），
并在每次响应后通知 limiter（429 立即冻结、快速失败）。

若把这里的 ``max_retries`` 改回 >0，``http_base`` 的内部重试会绕过限流器，
重新引入「429 冷却期继续触网 / 重试不消耗令牌」的问题。禁止修改；
``tests/utils/test_bangumi_api_http.py`` 与
``tests/utils/test_bangumi_api_async.py`` 有回归守卫。
"""

from __future__ import annotations

from ..http_base import AsyncHttpClient, SyncHttpClient

__all__ = ["create_bgm_async_client", "create_bgm_sync_client"]


def create_bgm_sync_client(
    *,
    label: str,
    proxy: str | None,
    verify: bool,
    ech: str,
) -> SyncHttpClient:
    """构造 Bangumi 同步 client（内部重试固定为 0，见模块 docstring）。"""
    client = SyncHttpClient(
        label=label,
        proxy=proxy,
        verify=verify,
        ech=ech,
        follow_redirects=True,
        max_retries=0,
        timeout=10.0,
    )
    client.prefix("📚")
    client.success_tpl("Bangumi 请求成功")
    client.failure_tpl("Bangumi 请求失败")
    return client


def create_bgm_async_client(
    *,
    proxy: str | None,
    verify: bool,
    ech: str,
    headers: dict[str, str],
) -> AsyncHttpClient:
    """构造 Bangumi 异步 client（内部重试固定为 0，见模块 docstring）。

    调用方每次请求临时创建并通过 ``async with`` 关闭（连接池绑定 event loop，
    不要跨 loop 缓存复用）。
    """
    client = AsyncHttpClient(
        label="Bangumi",
        proxy=proxy,
        verify=verify,
        ech=ech,
        follow_redirects=True,
        max_retries=0,
        timeout=10.0,
        headers=headers,
    )
    client.prefix("📚")
    client.success_tpl("Bangumi 请求成功")
    client.failure_tpl("Bangumi 请求失败")
    return client
