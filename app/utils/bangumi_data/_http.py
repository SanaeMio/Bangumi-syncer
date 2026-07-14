"""bangumi_data 包共享的 HTTP 工具

_BufferedResponse 包装 httpx.Response 以兼容旧 requests 风格的
iter_content / raw / with 上下文；_request_with_retry 提供带重试的同步下载。

保留在独立模块以便 __init__.py 重新导出，兼容测试 patch 路径
（app.utils.bangumi_data._request_with_retry / .httpx.Client / .time）。
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import httpx

from ..http_base import SyncHttpClient


class _BufferedResponse:
    """包装 httpx.Response，兼容旧 requests 风格的 iter_content / raw / with 上下文。

    httpx 同步流式需在 client 存活期间消费；这里在构造时一次性 read() 到
    response.content（bangumi-data 约 5MB，内存可接受），从而解绑 client 生命周期，
    并提供 iter_content / raw 兼容旧调用方。
    """

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        # 确保内容已读取到内存（解绑 client 连接池）
        response.read()
        self.content = response.content
        self.status_code = response.status_code
        self.headers = response.headers

    @property
    def raw(self) -> io.BytesIO:
        """兼容 requests 的 response.raw（ijson 增量解析使用）"""
        return io.BytesIO(self.content)

    def iter_content(self, chunk_size: int = 8192) -> Iterator[bytes]:
        """兼容 requests 的 iter_content"""
        with io.BytesIO(self.content) as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def raise_for_status(self) -> None:
        self._response.raise_for_status()

    def json(self) -> Any:
        return self._response.json()

    @property
    def text(self) -> str:
        return self._response.text

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> _BufferedResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _request_with_retry(
    url: str,
    proxies: dict[str, str] | None = None,
    stream: bool = False,
    max_retries: int = 3,
    ssl_verify: bool = True,
) -> _BufferedResponse:
    """带重试机制的HTTP请求方法（基于 httpx 同步客户端）

    proxies 参数兼容旧 requests 风格的 dict，内部转换为 httpx 代理字符串。
    返回 _BufferedResponse（兼容旧调用方的 iter_content / raw / with 上下文）。
    """
    # 将 requests 风格的 proxies dict 转换为 httpx 代理字符串
    proxy_url = None
    if proxies:
        proxy_url = proxies.get("https") or proxies.get("http")

    client = (
        SyncHttpClient(
            label="BangumiData",
            proxy=proxy_url,
            verify=ssl_verify,
            timeout=30.0,
            max_retries=max_retries,
        )
        .prefix("📦")
        .success_tpl("数据下载成功")
        .failure_tpl("数据下载失败")
    )
    with client:
        response = client.get(url)

    response.raise_for_status()
    return _BufferedResponse(response)
