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

from typing import Any

import httpx


def _ech_enabled(ech: bool | str) -> bool:
    """ECH 开关归一：False/"off"/"0"/"false" 均视为关闭。"""
    if isinstance(ech, bool):
        return ech
    return str(ech).strip().lower() not in ("", "off", "0", "false", "none")


def create_sync_client(
    *,
    proxy: str | None = None,
    verify: bool = True,
    ech: bool | str = False,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    headers: dict | None = None,
    **extra: Any,
) -> httpx.Client:
    """创建同步 httpx.Client

    Args:
        proxy: 代理地址（httpx 0.28+ 使用 proxy 单数参数）
        verify: 是否验证 SSL 证书
        ech: ECH 模式开关（"off"/False 关闭；"doh"/"manual"/True 启用）。
            启用时 verify 替换为带 ECH 的 utls SSLContext，
            ECH 配置获取失败则自动降级为原 verify 行为（普通 TLS）。
        timeout: 请求超时时间（秒）
        follow_redirects: 是否跟随重定向
        headers: 自定义请求头
        **extra: 透传给 httpx.Client 的额外参数（如 limits）

    Returns:
        httpx.Client 实例
    """
    kwargs: dict = {
        "verify": verify,
        "timeout": timeout,
        "follow_redirects": follow_redirects,
    }
    if _ech_enabled(ech):
        from app.utils.ech import get_ech_ssl_context

        ech_verify = get_ech_ssl_context()
        if ech_verify is not None:
            kwargs["verify"] = ech_verify
    if proxy:
        kwargs["proxy"] = proxy
    if headers:
        kwargs["headers"] = headers
    kwargs.update(extra)
    return httpx.Client(**kwargs)


def create_async_client(
    *,
    proxy: str | None = None,
    verify: bool = True,
    ech: bool | str = False,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    headers: dict | None = None,
    **extra: Any,
) -> httpx.AsyncClient:
    """创建异步 httpx.AsyncClient

    Args:
        proxy: 代理地址（httpx 0.28+ 使用 proxy 单数参数）
        verify: 是否验证 SSL 证书
        ech: ECH 模式开关（同 create_sync_client）
        timeout: 请求超时时间（秒）
        follow_redirects: 是否跟随重定向
        headers: 自定义请求头
        **extra: 透传给 httpx.AsyncClient 的额外参数（如 limits）

    Returns:
        httpx.AsyncClient 实例
    """
    kwargs: dict = {
        "verify": verify,
        "timeout": timeout,
        "follow_redirects": follow_redirects,
    }
    if _ech_enabled(ech):
        from app.utils.ech import get_ech_ssl_context

        ech_verify = get_ech_ssl_context()
        if ech_verify is not None:
            kwargs["verify"] = ech_verify
    if proxy:
        kwargs["proxy"] = proxy
    if headers:
        kwargs["headers"] = headers
    kwargs.update(extra)
    return httpx.AsyncClient(**kwargs)
