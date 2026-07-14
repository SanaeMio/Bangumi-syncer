"""bangumi_data 包共享的 HTTP 工具

_BufferedResponse 包装 httpx.Response 以兼容旧 requests 风格的
iter_content / raw / with 上下文；_request_with_retry 提供带重试的同步下载。

保留在独立模块以便 __init__.py 重新导出，兼容测试 patch 路径
（app.utils.bangumi_data._request_with_retry / .httpx.Client / .time）。
"""

from __future__ import annotations

import io

import httpx

from ..http_client import create_sync_client
from ..retry import http_retry_sync


class _BufferedResponse:
    """包装 httpx.Response，兼容旧 requests 风格的 iter_content / raw / with 上下文。

    httpx 同步流式需在 client 存活期间消费；这里在构造时一次性 read() 到
    response.content（bangumi-data 约 5MB，内存可接受），从而解绑 client 生命周期，
    并提供 iter_content / raw 兼容旧调用方。
    """

    def __init__(self, response: httpx.Response):
        self._response = response
        # 确保内容已读取到内存（解绑 client 连接池）
        response.read()
        self.content = response.content
        self.status_code = response.status_code
        self.headers = response.headers

    @property
    def raw(self):
        """兼容 requests 的 response.raw（ijson 增量解析使用）"""
        return io.BytesIO(self.content)

    def iter_content(self, chunk_size=8192):
        """兼容 requests 的 iter_content"""
        with io.BytesIO(self.content) as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def raise_for_status(self):
        self._response.raise_for_status()

    def json(self):
        return self._response.json()

    @property
    def text(self):
        return self._response.text

    def close(self):
        self._response.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _request_with_retry(
    url, proxies=None, stream=False, max_retries=3, ssl_verify=True
):
    """带重试机制的HTTP请求方法（基于 httpx 同步客户端）

    proxies 参数兼容旧 requests 风格的 dict，内部转换为 httpx 代理字符串。
    返回 _BufferedResponse（兼容旧调用方的 iter_content / raw / with 上下文）。
    """
    # 将 requests 风格的 proxies dict 转换为 httpx 代理字符串
    proxy_url = None
    if proxies:
        proxy_url = proxies.get("https") or proxies.get("http")

    def _do_request():
        with create_sync_client(
            proxy=proxy_url, verify=ssl_verify, timeout=30.0
        ) as client:
            return client.get(url)

    response = http_retry_sync(
        _do_request, max_retries=max_retries, label="请求"
    )
    response.raise_for_status()
    return _BufferedResponse(response)
