"""BangumiData 缓存管理 Mixin

职责：
- 远程下载 / 本地缓存文件维护
- 内存缓存（解析后）命中统计与失效
- 启动时检查与预加载
- 缓存统计、清理、强制更新

所有方法通过 self. 访问其他 mixin（MatchingMixin/IndexMixin），由 __init__.py
中的 BangumiData 组合类统一持有实例状态。
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from datetime import datetime, timedelta

import ijson

from ...core.logging import logger


class CacheMixin:
    """缓存与数据加载相关方法"""

    # ----- 缓存有效性 / 下载 -----

    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效（未过期）"""
        if not os.path.exists(self.local_cache_path):
            return False

        try:
            # 获取文件最后修改时间
            mtime = os.path.getmtime(self.local_cache_path)
            last_modified = datetime.fromtimestamp(mtime)
            now = datetime.now()

            # 如果缓存时间小于设定的TTL，则缓存有效
            return (now - last_modified) < timedelta(days=self.cache_ttl_days)
        except Exception as e:
            logger.error(f"检查缓存有效期时出错: {e}")
            return False

    def _download_data(self) -> bool:
        """从远程下载 bangumi-data 数据

        返回:
            bool: 下载是否成功
        """
        logger.debug(f"正在从 {self.data_url} 下载 bangumi-data...")

        proxies = {}
        if self.http_proxy:
            proxies = {"http": self.http_proxy, "https": self.http_proxy}

        try:
            from . import _request_with_retry

            response = _request_with_retry(
                self.data_url, proxies=proxies, stream=True, ssl_verify=self.ssl_verify
            )

            # 确保缓存目录存在
            cache_dir = os.path.dirname(self.local_cache_path)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)

            # 如果设置了使用缓存，则保存到本地
            if self.use_cache:
                with open(self.local_cache_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.debug(f"bangumi-data 已缓存到 {self.local_cache_path}")

            return True
        except Exception as e:
            logger.error(f"下载 bangumi-data 失败: {e}")
            return False

    def _ensure_fresh_data(self) -> bool:
        """确保数据是最新的

        如果缓存不存在或已过期，则重新下载

        返回:
            bool: 是否成功确保数据最新
        """
        if not self.use_cache:
            return True

        if not self._is_cache_valid():
            logger.debug("缓存不存在或已过期，正在重新下载数据...")
            return self._download_data()

        return True

    # ----- 数据解析（内存缓存） -----

    def _parse_data(self) -> Generator[dict, None, None]:
        """解析数据，以生成器方式返回，使用内存缓存避免重复解析"""
        # 首先确保数据是最新的
        self._ensure_fresh_data()

        # 检查内存缓存是否有效
        if self._data_cache is not None:
            self._cache_hit_count += 1
            logger.debug(f"使用内存缓存数据 (命中次数: {self._cache_hit_count})")
            for item in self._data_cache:
                yield item
            return

        self._cache_miss_count += 1
        logger.debug(f"缓存未命中，重新解析数据 (未命中次数: {self._cache_miss_count})")

        # 如果没有缓存，先解析数据到内存
        logger.debug("解析数据到内存缓存")
        items = []

        if self.use_cache and os.path.exists(self.local_cache_path):
            # 从缓存文件中解析
            try:
                with open(self.local_cache_path, "rb") as f:
                    for item in ijson.items(f, "items.item"):
                        items.append(item)
            except Exception as e:
                logger.error(f"从缓存解析 bangumi-data 失败: {e}")

                # 如果缓存文件解析失败，尝试重新下载
                if self._download_data():
                    with open(self.local_cache_path, "rb") as f:
                        for item in ijson.items(f, "items.item"):
                            items.append(item)
        else:
            # 从网络直接解析
            try:
                from . import _request_with_retry

                proxies = {}
                if self.http_proxy:
                    proxies = {"http": self.http_proxy, "https": self.http_proxy}

                with _request_with_retry(
                    self.data_url,
                    proxies=proxies,
                    stream=True,
                    ssl_verify=self.ssl_verify,
                ) as response:
                    for item in ijson.items(response.raw, "items.item", use_float=True):
                        items.append(item)
            except Exception as e:
                logger.error(f"流式解析 bangumi-data 失败: {e}")
                # 如果网络请求失败，但有缓存文件，尝试使用缓存
                if os.path.exists(self.local_cache_path):
                    logger.debug(f"尝试使用缓存文件 {self.local_cache_path}")
                    with open(self.local_cache_path, "rb") as f:
                        for item in ijson.items(f, "items.item"):
                            items.append(item)

        # 更新内存缓存
        self._data_cache = items
        self._cache_timestamp = time.time()

        # 数据已刷新，重建标题索引
        self._build_title_index()

        # 从内存缓存中yield数据
        for item in items:
            yield item

    # ----- 缓存统计 / 清理 / 强制更新 -----

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息

        返回:
            Dict: 包含缓存统计信息的字典
        """
        total_requests = self._cache_hit_count + self._cache_miss_count
        hit_rate = (
            (self._cache_hit_count / total_requests * 100) if total_requests > 0 else 0
        )

        return {
            "cache_hits": self._cache_hit_count,
            "cache_misses": self._cache_miss_count,
            "total_requests": total_requests,
            "hit_rate": hit_rate,
            "cache_size": len(self._data_cache) if self._data_cache else 0,
            "cache_age_minutes": (time.time() - self._cache_timestamp) / 60
            if self._cache_timestamp
            else 0,
        }

    def clear_cache(self) -> None:
        """清理内存缓存"""
        self._data_cache = None
        self._cache_timestamp = None
        self._title_index.clear()
        logger.debug("内存缓存已清理")

    def force_update(self) -> bool:
        """强制更新 bangumi-data 数据

        返回:
            bool: 更新是否成功
        """
        logger.info("强制更新 bangumi-data 数据...")
        success = self._download_data()
        if success:
            self.clear_cache()
        return success

    # ----- 启动时检查与预加载 -----

    def _check_and_download_cache_on_startup(self) -> None:
        """启动时检查缓存，如果缺少则下载

        这个方法在类初始化时被调用，确保缓存文件存在且有效
        """
        if not self.use_cache:
            logger.debug("缓存功能已禁用，跳过缓存检查")
            return

        if not os.path.exists(self.local_cache_path):
            logger.info(f"缓存文件不存在: {self.local_cache_path}，正在下载...")
            success = self._download_data()
            if success:
                logger.info("缓存文件下载成功")
            else:
                logger.warning("缓存文件下载失败，将在需要时尝试从网络获取数据")
        elif not self._is_cache_valid():
            logger.info(f"缓存文件已过期（超过 {self.cache_ttl_days} 天），正在更新...")
            success = self._download_data()
            if success:
                logger.info("缓存文件更新成功")
            else:
                logger.warning("缓存文件更新失败，将使用现有缓存文件")
        else:
            logger.debug("缓存文件存在且有效，无需下载")

    def _preload_data_to_memory(self) -> None:
        """初始化时预加载数据到内存"""
        try:
            logger.info("初始化时预加载 bangumi-data 到内存...")
            start_time = time.time()

            # 确保数据是最新的
            self._ensure_fresh_data()

            # 解析数据到内存
            items = []
            if self.use_cache and os.path.exists(self.local_cache_path):
                # 从缓存文件中解析
                try:
                    with open(self.local_cache_path, "rb") as f:
                        for item in ijson.items(f, "items.item"):
                            items.append(item)
                except Exception as e:
                    logger.error(f"预加载时从缓存解析 bangumi-data 失败: {e}")
                    return
            else:
                # 从网络直接解析
                try:
                    from . import _request_with_retry

                    proxies = {}
                    if self.http_proxy:
                        proxies = {"http": self.http_proxy, "https": self.http_proxy}

                    with _request_with_retry(
                        self.data_url,
                        proxies=proxies,
                        stream=True,
                        ssl_verify=self.ssl_verify,
                    ) as response:
                        for item in ijson.items(
                            response.raw, "items.item", use_float=True
                        ):
                            items.append(item)
                except Exception as e:
                    logger.error(f"预加载时流式解析 bangumi-data 失败: {e}")
                    return

            # 更新内存缓存
            self._data_cache = items
            self._cache_timestamp = time.time()

            end_time = time.time()
            logger.info(
                f"预加载完成，共加载 {len(items)} 个项目，耗时 {end_time - start_time:.2f}秒"
            )

        except Exception as e:
            logger.error(f"预加载 bangumi-data 到内存失败: {e}")
            # 预加载失败不影响后续使用，会在第一次调用时重新加载
