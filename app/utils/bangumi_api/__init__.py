"""
Bangumi API 客户端
"""

from __future__ import annotations

# httpx/socket/time 重新导出以兼容测试 patch（app.utils.bangumi_api.httpx.Client 等）
import socket  # noqa: F401
import time  # noqa: F401
from collections import OrderedDict
from typing import Any

import httpx  # noqa: F401

from ...core.logging import logger
from ..http_base import SyncHttpClient
from .collection import CollectionMixin
from .episodes import EpisodesMixin
from .http_layer import HttpLayerMixin
from .search import SearchMixin


class BangumiApi(HttpLayerMixin, SearchMixin, EpisodesMixin, CollectionMixin):
    def __init__(
        self,
        username: str | None = None,
        access_token: str | None = None,
        private: bool = True,
        http_proxy: str | None = None,
        ssl_verify: bool = True,
        bgm_api_proxy: str | None = None,
        bgm_next_proxy: str | None = None,
    ) -> None:
        self.api_base = (
            bgm_api_proxy.rstrip("/") if bgm_api_proxy else "https://api.bgm.tv"
        )
        self.next_base = (
            bgm_next_proxy.rstrip("/") if bgm_next_proxy else "https://next.bgm.tv"
        )

        self.host = f"{self.api_base}/v0"
        self.username = username
        self.access_token = access_token
        self.private = private
        self.http_proxy = http_proxy
        self.ssl_verify = ssl_verify
        # 使用 SyncHttpClient 封装 httpx.Client（统一日志/重试）
        # max_retries=3：重试由 SyncHttpClient 内置处理，_request_with_retry 仅负责代理回退
        self.req = (
            SyncHttpClient(
                label="Bangumi",
                proxy=http_proxy,
                verify=ssl_verify,
                follow_redirects=True,
                max_retries=3,
            )
            .prefix("📚")
            .success_tpl("Bangumi 请求成功")
            .failure_tpl("Bangumi 请求失败")
        )
        self._req_not_auth = (
            SyncHttpClient(
                label="Bangumi",
                proxy=http_proxy,
                verify=ssl_verify,
                follow_redirects=True,
                max_retries=3,
            )
            .prefix("📚")
            .success_tpl("Bangumi 请求成功")
            .failure_tpl("Bangumi 请求失败")
        )

        # 代理失败标记：一旦代理失败，后续请求都直接使用直连
        self._proxy_failed = False

        # 实例级别的带大小限制缓存，避免无限增长
        _MAX_CACHE_SIZE = 200
        self._cache = {
            "search": OrderedDict(),
            "search_old": OrderedDict(),
            "get_subject": OrderedDict(),
            "get_related_subjects": OrderedDict(),
            "get_episodes": OrderedDict(),
        }
        self._max_cache_size = _MAX_CACHE_SIZE

        # 如果禁用SSL验证，输出警告（httpx 无需抑制 urllib3 警告）
        if not ssl_verify:
            logger.warning(
                "SSL证书验证已禁用，这会降低安全性。建议仅在代理环境下出现SSL错误时使用。"
            )

        logger.debug(
            f"BangumiApi 初始化 - 代理参数: {http_proxy if http_proxy else '无'}, SSL验证: {ssl_verify}"
        )
        self.init()

    def _put_cache(self, category: str, key: Any, value: Any) -> None:
        """写入缓存并淘汰超限条目（LRU）"""
        cache = self._cache[category]
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._max_cache_size:
            cache.popitem(last=False)

    def init(self) -> None:
        for r in self.req, self._req_not_auth:
            r.client.headers.update(
                {
                    "Accept": "application/json",
                    "User-Agent": "SanaeMio/Bangumi-syncer (https://github.com/SanaeMio/Bangumi-syncer)",
                }
            )
            if self.access_token:
                r.client.headers.update(
                    {"Authorization": f"Bearer {self.access_token}"}
                )
        # httpx.Client.headers 是可变的，直接重新赋值即可
        # httpx 存储的 header key 为小写，需大小写不敏感地过滤
        self._req_not_auth.client.headers = {
            k: v
            for k, v in self._req_not_auth.client.headers.items()
            if k.lower() != "authorization"
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        logger.debug(
            f"BangumiApi GET请求: {self.host}/{path}, 代理: {self.http_proxy if self.http_proxy else '无'}"
        )
        res = self._request_with_retry(
            "GET", self.req, f"{self.host}/{path}", params=params
        )
        return self._check_auth_error(res)

    def post(
        self,
        path: str,
        _json: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug(
            f"BangumiApi POST请求: {self.host}/{path}, 代理: {self.http_proxy if self.http_proxy else '无'}"
        )
        res = self._request_with_retry(
            "POST", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        return self._check_auth_error(res)

    def put(
        self,
        path: str,
        _json: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        res = self._request_with_retry(
            "PUT", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        return self._check_auth_error(res)

    def patch(
        self,
        path: str,
        _json: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        res = self._request_with_retry(
            "PATCH", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        return self._check_auth_error(res)
