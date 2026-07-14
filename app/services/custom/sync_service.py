"""自定义 Webhook 同步服务

最简形态的 Webhook 驱动同步服务，直接接收 CustomItem 并委托给共享 SyncService。
作为新驱动接入的参考模板（被动 webhook 型，无 extractor / scheduler）。

参见 docs/development/new-driver-guide.md 了解新驱动接入指南。
"""

from __future__ import annotations

from ...core.logging import logger
from ...models.sync import CustomItem, SyncResponse
from ..sync_service import sync_service

CUSTOM_SYNC_SOURCE = "custom"


class CustomSyncService:
    """自定义 Webhook 同步服务

    特点：
    - 不需要 extractor（直接接收 CustomItem，无需格式转换）
    - 不需要 scheduler（被动 webhook 模式，由外部推送触发）
    - 不需要独立配置节（使用全局 webhook_key 鉴权）

    作为新驱动接入的最简参考模板。
    """

    def sync_item(
        self,
        item: CustomItem,
        source: str = CUSTOM_SYNC_SOURCE,
        sync_svc=None,
    ) -> SyncResponse:
        """同步处理自定义 webhook 请求

        Args:
            item: CustomItem 已是通用模型，无需 extractor 转换
            source: 来源标识，默认 "custom"
            sync_svc: 共享 SyncService 实例。未传入时使用模块级单例。

        Returns:
            SyncResponse: 同步结果
        """
        if sync_svc is None:
            sync_svc = sync_service

        logger.debug(
            f"CustomSyncService 同步处理: {item.title} S{item.season:02d}E{item.episode:02d}, source={source}"
        )
        return sync_svc.sync_custom_item(item, source=source)

    async def sync_item_async(
        self,
        item: CustomItem,
        source: str = CUSTOM_SYNC_SOURCE,
        sync_svc=None,
    ) -> str:
        """异步处理自定义 webhook 请求

        Args:
            item: CustomItem 已是通用模型，无需 extractor 转换
            source: 来源标识，默认 "custom"
            sync_svc: 共享 SyncService 实例。未传入时使用模块级单例。

        Returns:
            str: 异步任务 ID
        """
        if sync_svc is None:
            sync_svc = sync_service

        logger.debug(
            f"CustomSyncService 异步处理: {item.title} S{item.season:02d}E{item.episode:02d}, source={source}"
        )
        return await sync_svc.sync_custom_item_async(item, source=source)


custom_sync_service = CustomSyncService()
