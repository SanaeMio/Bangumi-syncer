"""bangumi-data 数据查询与缓存

原 bangumi_data.py (1120 行) 拆分为 package：
- __init__.py: BangumiData 组合类 + 单例 + 配置初始化
- _http.py: _BufferedResponse + _request_with_retry（兼容测试 patch 路径）
- cache.py: CacheMixin（下载/缓存/预加载/统计）
- matching.py: MatchingMixin（标题匹配/番剧 ID 查找/相似度）
- index.py: IndexMixin（TMDB 映射/标题索引）

测试兼容性：httpx / time / ijson / _request_with_retry 在 __init__.py 顶层
重新导出，以兼容 patch 路径 app.utils.bangumi_data.{httpx,time,ijson,
_request_with_retry}。
"""

from __future__ import annotations

import time  # noqa: F401

# ===== 重新导出以兼容测试 patch 路径 =====
import httpx  # noqa: F401
import ijson  # noqa: F401

from ...core.config import config_manager
from ._http import _BufferedResponse, _request_with_retry  # noqa: F401
from .cache import CacheMixin
from .index import IndexMixin
from .matching import MatchingMixin


class BangumiData(CacheMixin, MatchingMixin, IndexMixin):
    """处理 bangumi-data 数据的类"""

    def __init__(self) -> None:
        self.data_url = config_manager.get(
            "bangumi-data",
            "data_url",
            fallback="https://unpkg.com/bangumi-data@0.3/dist/data.json",
        )
        self.local_cache_path = config_manager.get(
            "bangumi-data", "local_cache_path", fallback="./bangumi_data_cache.json"
        )
        self.http_proxy = config_manager.get(
            "bangumi-data",
            "http_proxy",
            fallback=config_manager.get("dev", "script_proxy", fallback=""),
        )
        self.ssl_verify = config_manager.get("dev", "ssl_verify", fallback=True)
        self.use_cache = config_manager.get("bangumi-data", "use_cache", fallback=True)
        # 缓存有效期（天），默认7天
        self.cache_ttl_days = config_manager.get(
            "bangumi-data", "cache_ttl_days", fallback=7
        )
        self._cached_data = None
        self._cache_items = None
        # 内存缓存，避免重复解析文件
        self._data_cache = None
        self._cache_timestamp = None
        self._cache_hit_count = 0  # 缓存命中次数
        self._cache_miss_count = 0  # 缓存未命中次数
        # 是否启用更详细的日志，用于调试匹配问题
        self.verbose_logging = config_manager.get("dev", "debug", fallback=False)
        self._cache_tmdb_mapping: dict[str, str] = {}
        # 精确匹配索引：title → [item]，加速常用查询
        self._title_index: dict[str, list[dict]] = {}

        # 启动时检查缓存，如果缺少则下载
        self._check_and_download_cache_on_startup()

        # 启动时预加载数据到内存
        self._preload_data_to_memory()

        # 启动时构建 TMDB 映射番剧名, 用于 trakt 同步时快速查找
        self._build_tmdb_mapping()

        # 启动时构建标题精确匹配索引
        self._build_title_index()


# 全局 bangumi_data 实例
bangumi_data = BangumiData()
