"""Jellyfin Webhook 同步服务

从共享 SyncService 抽离的 Jellyfin 专属同步逻辑。
事件校验、数据提取、分发到 sync_custom_item / sync_movie_watching。
"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ...models.sync import SyncResponse
from .extractor import extract_jellyfin_data

JELLYFIN_SYNC_SOURCE = "jellyfin"


class JellyfinSyncService:
    """Jellyfin 同步服务

    由共享 SyncService 的 sync_jellyfin_item 方法委托调用。
    异步任务跟踪仍由 SyncService 负责。
    """

    def sync_item(self, jellyfin_data: dict[str, Any], sync_svc=None) -> SyncResponse:
        """处理 Jellyfin 同步请求（核心逻辑）

        Args:
            jellyfin_data: Jellyfin webhook 报文
            sync_svc: 共享 SyncService 实例。未传入时使用模块级单例。
        """
        if sync_svc is None:
            from ..sync_service import sync_service

            sync_svc = sync_service
        try:
            logger.debug(f"接收到Jellyfin同步请求：{jellyfin_data}")

            ntype = jellyfin_data.get("NotificationType", "")
            mtype = (jellyfin_data.get("media_type") or "").lower()
            playback_start_movie = ntype == "PlaybackStart" and mtype == "movie"

            if ntype != "PlaybackStop" and not playback_start_movie:
                logger.debug(f"事件类型{ntype}无需同步，跳过")
                return SyncResponse(
                    status="ignored",
                    message=f"事件类型{ntype}无需同步",
                )

            if ntype == "PlaybackStop":
                if jellyfin_data["PlayedToCompletion"] == "False":
                    logger.debug(
                        f"是否播完：{jellyfin_data['PlayedToCompletion']}，无需同步，跳过"
                    )
                    return SyncResponse(
                        status="ignored", message="未播放完成，跳过同步"
                    )

            # 提取数据并调用自定义同步
            custom_item = extract_jellyfin_data(jellyfin_data)
            logger.debug(f"Jellyfin重新组装JSON报文：{custom_item}")

            if playback_start_movie:
                return sync_svc.sync_movie_watching(
                    custom_item, source=JELLYFIN_SYNC_SOURCE
                )
            return sync_svc.sync_custom_item(custom_item, source=JELLYFIN_SYNC_SOURCE)
        except Exception as e:
            logger.error(f"Jellyfin同步处理出错: {e}")
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")


jellyfin_sync_service = JellyfinSyncService()
