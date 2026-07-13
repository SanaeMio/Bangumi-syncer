"""httpx 客户端统一工厂

消除项目中 6 处重复的 httpx.Client / httpx.AsyncClient 构造逻辑，
统一处理 httpx 0.28+ 的 proxy（单数）、verify、timeout、follow_redirects 参数。

使用示例：
    # 同步客户端
    from app.utils.http_client import create_sync_client
    with create_sync_client(proxy=http_proxy, verify=ssl_verify) as client:
        response = client.get(url)

    # 异步客户端
    from app.utils.http_client import create_async_client
    async with create_async_client(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url)
"""

from __future__ import annotations

import httpx


def create_sync_client(
    *,
    proxy: str | None = None,
    verify: bool = True,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    headers: dict | None = None,
) -> httpx.Client:
    """创建同步 httpx.Client

    Args:
        proxy: 代理地址（httpx 0.28+ 使用 proxy 单数参数）
        verify: 是否验证 SSL 证书
        timeout: 请求超时时间（秒）
        follow_redirects: 是否跟随重定向
        headers: 自定义请求头

    Returns:
        httpx.Client 实例
    """
    kwargs: dict = {
        "verify": verify,
        "timeout": timeout,
        "follow_redirects": follow_redirects,
    }
    if proxy:
        kwargs["proxy"] = proxy
    if headers:
        kwargs["headers"] = headers
    return httpx.Client(**kwargs)


def create_async_client(
    *,
    proxy: str | None = None,
    verify: bool = True,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    headers: dict | None = None,
) -> httpx.AsyncClient:
    """创建异步 httpx.AsyncClient

    Args:
        proxy: 代理地址（httpx 0.28+ 使用 proxy 单数参数）
        verify: 是否验证 SSL 证书
        timeout: 请求超时时间（秒）
        follow_redirects: 是否跟随重定向
        headers: 自定义请求头

    Returns:
        httpx.AsyncClient 实例
    """
    kwargs: dict = {
        "verify": verify,
        "timeout": timeout,
        "follow_redirects": follow_redirects,
    }
    if proxy:
        kwargs["proxy"] = proxy
    if headers:
        kwargs["headers"] = headers
    return httpx.AsyncClient(**kwargs)
