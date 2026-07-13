"""Emby Webhook 同步服务

从共享 SyncService 抽离的 Emby 专属同步逻辑。
事件校验、字段校验、数据提取、分发到 sync_custom_item / sync_movie_watching。
"""

from __future__ import annotations

import traceback
from typing import Any

from ...core.logging import logger
from ...models.sync import SyncResponse
from .extractor import extract_emby_data

EMBY_SYNC_SOURCE = "emby"


class EmbySyncService:
    """Emby 同步服务

    由共享 SyncService 的 sync_emby_item 方法委托调用。
    异步任务跟踪仍由 SyncService 负责。
    """

    def sync_item(self, emby_data: dict[str, Any], sync_svc=None) -> SyncResponse:
        """处理 Emby 同步请求（核心逻辑）

        Args:
            emby_data: Emby webhook 报文
            sync_svc: 共享 SyncService 实例。未传入时使用模块级单例。
        """
        if sync_svc is None:
            from ..sync_service import sync_service as sync_svc
        try:
            # 记录接收到的数据
            logger.debug(f"接收到Emby同步请求：{emby_data}")

            # 验证必要字段是否存在
            required_fields = ["Event", "Item", "User"]
            for field in required_fields:
                if field not in emby_data:
                    logger.error(f"Emby请求缺少必要字段: {field}")
                    return SyncResponse(
                        status="error", message=f"请求缺少必要字段: {field}"
                    )

            event = emby_data["Event"]
            emby_item = emby_data["Item"]
            is_movie = str(emby_item.get("Type") or "").lower() == "movie"
            playback_start_movie = event == "playback.start" and is_movie

            if (
                event != "item.markplayed"
                and event != "playback.stop"
                and not playback_start_movie
            ):
                logger.debug(f"事件类型{event}无需同步，跳过")
                return SyncResponse(
                    status="ignored", message=f"事件类型{event}无需同步"
                )

            if is_movie:
                if "Name" not in emby_item:
                    logger.error("Emby 电影 Item 缺少 Name 字段")
                    return SyncResponse(
                        status="error", message="Item缺少必要字段: Name"
                    )
            else:
                item_required_fields = [
                    "Type",
                    "SeriesName",
                    "ParentIndexNumber",
                    "IndexNumber",
                ]
                for field in item_required_fields:
                    if field not in emby_item:
                        logger.error(f"Emby Item缺少必要字段: {field}")
                        return SyncResponse(
                            status="error", message=f"Item缺少必要字段: {field}"
                        )

            # 如果是播放停止事件,只有播放完成才判断为看过
            if event == "playback.stop":
                if (
                    "PlaybackInfo" not in emby_data
                    or "PlayedToCompletion" not in emby_data["PlaybackInfo"]
                ):
                    logger.debug(
                        "播放停止事件缺少PlaybackInfo.PlayedToCompletion字段，跳过"
                    )
                    return SyncResponse(status="ignored", message="播放信息不完整")

                if emby_data["PlaybackInfo"]["PlayedToCompletion"] is not True:
                    if is_movie:
                        logger.debug(
                            f"{emby_item.get('Name', '')} 电影未播放完成，跳过"
                        )
                    else:
                        logger.debug(
                            f"{emby_item['SeriesName']} S{emby_item['ParentIndexNumber']:02d}E{emby_item['IndexNumber']:02d}未播放完成，跳过"
                        )
                    return SyncResponse(status="ignored", message="未播放完成")

            # 提取数据并调用自定义同步
            custom_item = extract_emby_data(emby_data)
            logger.debug(f"Emby重新组装JSON报文：{custom_item}")

            if playback_start_movie:
                return sync_svc.sync_movie_watching(
                    custom_item, source=EMBY_SYNC_SOURCE
                )
            return sync_svc.sync_custom_item(custom_item, source=EMBY_SYNC_SOURCE)
        except Exception as e:
            logger.error(f"Emby同步处理出错: {e}")
            logger.error(traceback.format_exc())
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")


emby_sync_service = EmbySyncService()
