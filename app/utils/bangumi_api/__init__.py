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

from ...core.config import config_manager
from ...core.logging import logger
from ..http_base import SyncHttpClient
from ._archive_shortcut import archive_shortcut
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
        # timeout=10.0：单次请求 10s 超时，避免错误 subject_id 触发链式调用时
        # 每跳都要等 30s 才失败，导致整个同步流程卡死（线程池被占满）
        self.req = (
            SyncHttpClient(
                label="Bangumi",
                proxy=http_proxy,
                verify=ssl_verify,
                follow_redirects=True,
                max_retries=3,
                timeout=10.0,
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
                timeout=10.0,
            )
            .prefix("📚")
            .success_tpl("Bangumi 请求成功")
            .failure_tpl("Bangumi 请求失败")
        )

        # 代理失败标记：一旦代理失败，后续请求都直接使用直连
        self._proxy_failed = False

        # API 不可达标记 + TTL：重试耗尽后置 True，避免后续请求再次等 10s×3 重试
        # _api_unreachable_until 之前都跳过实际请求，直接走降级（写操作入队、读操作走 archive）
        # TTL 到期后下一次请求恢复探测；成功后清除标记
        self._api_unreachable: bool = False
        self._api_unreachable_until: float = 0.0
        self._api_unreachable_ttl: int = (
            300  # 默认 5 分钟，可被 [bangumi-replay] api_probe_interval 覆盖
        )

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

        # Archive 短路协调器（引用全局单例，便于测试时替换）
        # enabled=False 时所有 try_* 立即返回 archive_disabled，等价于原行为
        self._archive = archive_shortcut

        # 最近一次读操作的命中来源（""=API/未命中，"archive"=本地归档命中）
        # 由 search/search_old/get_subject/get_related_subjects 在 archive 短路命中时置 "archive"，
        # 调用方（sync_service）据此把匹配过程步骤标记为 archive 而非 api_search。
        # 每次 bgm_search 入口会重置为 ""，反映该次搜索的最终命中来源。
        self.last_hit_source: str = ""

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

    # ------------------------------------------------------------------
    # API 可达性标记（供 http_layer 与 collection 层协作）
    # ------------------------------------------------------------------

    def is_api_unreachable(self) -> bool:
        """API 是否处于不可达状态（TTL 内直接降级，不发请求）"""
        if not self._api_unreachable:
            return False
        # TTL 已过期，自动恢复探测
        if time.time() >= self._api_unreachable_until:
            self._api_unreachable = False
            logger.info("📚 Bangumi API 不可达 TTL 已过期，恢复探测")
            return False
        return True

    def mark_api_unreachable(self) -> None:
        """标记 API 不可达，按 TTL 推迟下一次探测"""
        # 从配置读取 TTL（[bangumi-replay] 段，未配置时仍可用默认值）
        try:
            ttl = int(
                config_manager.get("bangumi-replay", "api_probe_interval", fallback=300)
            )
            if ttl < 30:
                ttl = 30
        except (TypeError, ValueError):
            ttl = 300
        self._api_unreachable = True
        self._api_unreachable_until = time.time() + ttl
        self._api_unreachable_ttl = ttl
        logger.warning(
            f"📚 Bangumi API 标记为不可达，{ttl} 秒内读操作走 archive、写操作入待同步队列"
        )

    def mark_api_reachable(self) -> None:
        """请求成功后清除不可达标记（API 已恢复）"""
        if self._api_unreachable:
            logger.info("📚 Bangumi API 已恢复可达，清除不可达标记")
        self._api_unreachable = False
        self._api_unreachable_until = 0.0

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
        # 重新加载 Archive 短路配置（配置变更后调用方可立即生效）
        self._archive.reload_config()

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        logger.debug(
            f"BangumiApi GET请求: {self.host}/{path}, 代理: {self.http_proxy if self.http_proxy else '无'}"
        )
        res = self._request_with_retry(
            "GET", self.req, f"{self.host}/{path}", params=params
        )
        # 请求成功即清除不可达标记
        self.mark_api_reachable()
        return self._check_auth_error(res)

    def post(
        self,
        path: str,
        _json: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        logger.debug(
            f"BangumiApi POST请求: {self.host}/{path}, 代理: {self.http_proxy if self.http_proxy else '无'}"
        )
        res = self._request_with_retry(
            "POST", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        self.mark_api_reachable()
        return self._check_auth_error(res)

    def put(
        self,
        path: str,
        _json: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        res = self._request_with_retry(
            "PUT", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        self.mark_api_reachable()
        return self._check_auth_error(res)

    def patch(
        self,
        path: str,
        _json: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        res = self._request_with_retry(
            "PATCH", self.req, f"{self.host}/{path}", json=_json, params=params
        )
        self.mark_api_reachable()
        return self._check_auth_error(res)

    def close(self) -> None:
        """关闭底层 httpx.Client，释放连接池资源

        BangumiApi 持有两个 SyncHttpClient（req / _req_not_auth），
        短生命周期实例（如放送日历/今日放送提醒中临时构造的）应在使用完毕后调用 close()，
        避免连接池句柄泄漏。长生命周期实例（如 sync_service 主客户端）无需调用。
        """
        try:
            self.req.close()
        except Exception as e:
            logger.debug(f"关闭 BangumiApi.req 失败: {e}")
        try:
            self._req_not_auth.close()
        except Exception as e:
            logger.debug(f"关闭 BangumiApi._req_not_auth 失败: {e}")
