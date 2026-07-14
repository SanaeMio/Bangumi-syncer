"""
标记重试 Mixin：同步/异步标记剧集为已看，带指数退避重试。
"""

import asyncio
import time
from typing import Any

from ...core.logging import logger
from ...utils.bangumi_api import BangumiApi


class RetryMixin:
    """标记剧集重试逻辑（同步 + 异步）。"""

    # 实例属性类型声明（实际在 __init__.py 的 SyncService.__init__ 中初始化）
    _executor: Any  # ThreadPoolExecutor，供异步版本使用

    def _retry_mark_episode(
        self, bgm_api: BangumiApi, subject_id: str, ep_id: str, max_retries: int = 3
    ) -> int:
        """带重试机制的标记剧集方法（优化版，减少阻塞时间）"""
        for attempt in range(max_retries + 1):
            try:
                mark_status = bgm_api.mark_episode_watched(
                    subject_id=subject_id, ep_id=ep_id
                )
                if attempt > 0:
                    logger.info(f"重试成功，第 {attempt + 1} 次尝试标记成功")
                return mark_status
            except Exception as e:
                if attempt < max_retries:
                    # 优化延迟策略：减少最大延迟时间
                    delay = min(2**attempt, 3)  # 最大延迟3秒: 1, 2, 3秒
                    logger.error(
                        f"标记剧集失败: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )

                    # 使用非阻塞方式等待（在线程池中执行时不会阻塞主线程）
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"标记剧集失败，已达到最大重试次数 {max_retries}: {str(e)}"
                    )
                    raise e
        # This line should never be reached due to the loop logic
        return 0  # pragma: no cover

    async def _retry_mark_episode_async(
        self, bgm_api: BangumiApi, subject_id: str, ep_id: str, max_retries: int = 3
    ) -> int:
        """异步版本的重试标记剧集方法"""
        for attempt in range(max_retries + 1):
            try:
                # 在线程池中执行同步操作
                loop = asyncio.get_running_loop()
                mark_status = await loop.run_in_executor(
                    self._executor, bgm_api.mark_episode_watched, subject_id, ep_id
                )
                if attempt > 0:
                    logger.info(f"异步重试成功，第 {attempt + 1} 次尝试标记成功")
                return mark_status
            except Exception as e:
                if attempt < max_retries:
                    delay = min(2**attempt, 3)  # 最大延迟3秒
                    logger.error(
                        f"异步标记剧集失败: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )

                    # 使用异步等待，不阻塞事件循环
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"异步标记剧集失败，已达到最大重试次数 {max_retries}: {str(e)}"
                    )
                    raise e
        # This line should never be reached due to the loop logic
        return 0  # pragma: no cover
