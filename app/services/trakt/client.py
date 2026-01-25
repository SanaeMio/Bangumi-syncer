"""
Trakt.tv API 异步客户端
"""

import asyncio
import time
from datetime import datetime
from typing import Optional, Union
from urllib.parse import urlencode

import httpx

from ...core.config import config_manager
from ...core.logging import logger

# ===== 数据模型导入 =====
from .models import TraktCollectionItem, TraktHistoryItem, TraktRatingItem

# ===== Trakt 客户端 =====


class TraktClient:
    """Trakt.tv API 异步客户端"""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://api.trakt.tv"
        self.client_id = config_manager.get_trakt_config().get("client_id", "")

        # 请求头
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
            "Content-Type": "application/json",
        }

        # 速率限制控制
        self.rate_limit_remaining: int = 1000
        self.rate_limit_reset: int = 0
        self._request_queue: asyncio.Queue = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(5)  # 限制并发请求数

        # 重试配置
        self.max_retries = 3
        self.retry_delay = 1.0

        # HTTP 客户端
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()

    async def _ensure_client(self) -> None:
        """确保 HTTP 客户端已初始化"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=30.0,
                follow_redirects=True,
            )

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> Optional[dict]:
        """发送 HTTP 请求，处理速率限制和重试"""
        await self._ensure_client()
        assert self._client is not None

        # 检查速率限制
        await self._check_rate_limit()

        url = f"{self.base_url}{endpoint}"
        if params:
            url = f"{url}?{urlencode(params)}"

        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(
                    method=method,
                    url=url,
                    json=data,
                    headers=self.headers,
                )

                # 更新速率限制信息
                self._update_rate_limit(response.headers)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 204:
                    return {}  # 无内容
                elif response.status_code == 401:
                    logger.error("Trakt 认证失败，令牌可能已过期")
                    raise ValueError("认证失败")
                elif response.status_code == 429:
                    # 速率限制，等待后重试
                    retry_after_str = response.headers.get("Retry-After", "60")
                    try:
                        retry_after = int(retry_after_str)
                    except (ValueError, TypeError):
                        retry_after = 60  # 默认值
                    logger.warning(f"达到速率限制，等待 {retry_after} 秒后重试")
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    logger.error(
                        f"Trakt API 请求失败: {response.status_code} - {response.text}"
                    )
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay * (2**attempt))
                        continue
                    return None

            except httpx.RequestError as e:
                logger.error(f"Trakt API 请求错误: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (2**attempt))
                    continue
                return None
            except ValueError:
                raise
            except Exception as e:
                logger.error(f"Trakt API 请求异常: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (2**attempt))
                    continue
                return None

        return None

    def _update_rate_limit(self, headers: httpx.Headers) -> None:
        """更新速率限制信息"""
        try:
            remaining = headers.get("X-RateLimit-Remaining")
            reset = headers.get("X-RateLimit-Reset")

            if remaining is not None:
                self.rate_limit_remaining = int(remaining)
            if reset is not None:
                self.rate_limit_reset = int(reset)
        except (ValueError, TypeError):
            pass

    async def _check_rate_limit(self) -> None:
        """检查速率限制，必要时等待"""
        if self.rate_limit_remaining <= 10:
            # 剩余配额不足，等待重置
            now = time.time()
            if self.rate_limit_reset > now:
                wait_time = self.rate_limit_reset - now + 1
                logger.warning(f"速率限制配额不足，等待 {wait_time:.1f} 秒")
                await asyncio.sleep(wait_time)

    # ===== API 方法 =====

    async def get_watched_history(
        self, start_date: Optional[datetime] = None, limit: int = 1000, page: int = 1
    ) -> list[TraktHistoryItem]:
        """获取用户观看历史

        Args:
            start_date: 开始日期，用于增量同步
            limit: 每页数量 (默认1000，最大1000)
            page: 页码 (默认1)
        """
        try:
            endpoint = "/sync/history"
            params: dict[str, Union[str, int]] = {"limit": limit, "page": page}

            if start_date:
                # Trakt 使用 YYYY-MM-DD 格式
                params["start_at"] = start_date.strftime("%Y-%m-%d")

            data = await self._make_request("GET", endpoint, params)

            if not data or not isinstance(data, list):
                return []

            history_items = []
            for item in data:
                try:
                    history_item = TraktHistoryItem(**item)
                    history_items.append(history_item)
                except Exception as e:
                    logger.warning(f"解析观看历史项失败: {e}, 数据: {item}")

            return history_items

        except Exception as e:
            logger.error(f"获取观看历史失败: {e}")
            return []

    async def get_all_watched_history(
        self, start_date: Optional[datetime] = None, max_pages: int = 10
    ) -> list[TraktHistoryItem]:
        """获取所有分页的观看历史（自动分页）

        Args:
            start_date: 开始日期，用于增量同步
            max_pages: 最大页数限制，防止无限循环
        """
        all_items: list[TraktHistoryItem] = []
        page = 1

        while page <= max_pages:
            try:
                items: list[TraktHistoryItem] = await self.get_watched_history(
                    start_date=start_date,
                    limit=1000,  # 每页最大数量
                    page=page,
                )

                if not items:
                    # 没有更多数据
                    break

                all_items.extend(items)

                # 如果返回的数量少于限制，可能是最后一页
                if len(items) < 1000:
                    break

                page += 1

                # 避免请求过快，小延迟
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.error(f"获取第 {page} 页观看历史失败: {e}")
                break

        # 去重：基于 trakt_item_id 和 watched_at
        seen: set[str] = set()
        unique_items: list[TraktHistoryItem] = []

        for item in all_items:
            key = f"{item.trakt_item_id}:{item.watched_at}"
            if key not in seen:
                seen.add(key)
                unique_items.append(item)

        logger.info(
            f"获取到 {len(all_items)} 条历史记录，去重后 {len(unique_items)} 条"
        )
        return unique_items

    async def get_ratings(
        self,
        rating_type: str = "all",  # all, movies, shows, seasons, episodes
        limit: int = 1000,
        page: int = 1,
    ) -> list[TraktRatingItem]:
        """获取用户评分

        Args:
            rating_type: 评分类型
            limit: 每页数量 (默认1000，最大1000)
            page: 页码 (默认1)
        """
        try:
            endpoint = f"/sync/ratings/{rating_type}"
            params = {"limit": limit, "page": page}

            data = await self._make_request("GET", endpoint, params)

            if not data or not isinstance(data, list):
                return []

            rating_items = []
            for item in data:
                try:
                    rating_item = TraktRatingItem(**item)
                    rating_items.append(rating_item)
                except Exception as e:
                    logger.warning(f"解析评分项失败: {e}, 数据: {item}")

            return rating_items

        except Exception as e:
            logger.error(f"获取评分失败: {e}")
            return []

    async def get_collection(
        self,
        collection_type: str = "all",  # all, movies, shows, seasons, episodes
        limit: int = 1000,
        page: int = 1,
    ) -> list[TraktCollectionItem]:
        """获取用户收藏

        Args:
            collection_type: 收藏类型
            limit: 每页数量 (默认1000，最大1000)
            page: 页码 (默认1)
        """
        try:
            endpoint = f"/sync/collection/{collection_type}"
            params = {"limit": limit, "page": page}

            data = await self._make_request("GET", endpoint, params)

            if not data or not isinstance(data, list):
                return []

            collection_items = []
            for item in data:
                try:
                    collection_item = TraktCollectionItem(**item)
                    collection_items.append(collection_item)
                except Exception as e:
                    logger.warning(f"解析收藏项失败: {e}, 数据: {item}")

            return collection_items

        except Exception as e:
            logger.error(f"获取收藏失败: {e}")
            return []

    async def get_user_profile(self) -> Optional[dict]:
        """获取用户个人信息"""
        try:
            endpoint = "/users/me"

            data = await self._make_request("GET", endpoint)

            return data if isinstance(data, dict) else None

        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None

    async def get_movie_info(self, trakt_id: int) -> Optional[dict]:
        """获取电影详细信息"""
        try:
            endpoint = f"/movies/{trakt_id}"

            data = await self._make_request("GET", endpoint)

            return data if isinstance(data, dict) else None

        except Exception as e:
            logger.error(f"获取电影信息失败: {e}")
            return None

    async def get_show_info(self, trakt_id: int) -> Optional[dict]:
        """获取剧集详细信息"""
        try:
            endpoint = f"/shows/{trakt_id}"

            data = await self._make_request("GET", endpoint)

            return data if isinstance(data, dict) else None

        except Exception as e:
            logger.error(f"获取剧集信息失败: {e}")
            return None

    async def get_episode_info(
        self, show_id: int, season: int, episode: int
    ) -> Optional[dict]:
        """获取剧集详细信息"""
        try:
            endpoint = f"/shows/{show_id}/seasons/{season}/episodes/{episode}"

            data = await self._make_request("GET", endpoint)

            return data if isinstance(data, dict) else None

        except Exception as e:
            logger.error(f"获取剧集详情失败: {e}")
            return None

    async def test_connection(self) -> bool:
        """测试连接是否正常"""
        try:
            data = await self.get_user_profile()
            return data is not None
        except Exception as e:
            logger.error(f"测试 Trakt 连接失败: {e}")
            return False


# ===== 客户端工厂 =====


class TraktClientFactory:
    """Trakt 客户端工厂"""

    @staticmethod
    async def create_client(access_token: str) -> Optional[TraktClient]:
        """创建 Trakt 客户端"""
        try:
            client = TraktClient(access_token)
            # 测试连接
            success = await client.test_connection()
            if success:
                return client
            else:
                await client.close()
                return None
        except Exception as e:
            logger.error(f"创建 Trakt 客户端失败: {e}")
            return None
