"""Plex Webhook 同步服务

从共享 SyncService 抽离的 Plex 专属同步逻辑。
事件校验、数据提取、分发到 sync_custom_item / sync_movie_watching。
"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ...models.sync import SyncResponse
from .extractor import extract_plex_data

PLEX_SYNC_SOURCE = "plex"


class PlexSyncService:
    """Plex 同步服务

    由共享 SyncService 的 sync_plex_item 方法委托调用。
    异步任务跟踪仍由 SyncService 负责（_executor / _register_task 等）。
    """

    def sync_item(self, plex_data: dict[str, Any], sync_svc=None) -> SyncResponse:
        """处理 Plex 同步请求（核心逻辑）

        Args:
            plex_data: Plex webhook 报文
            sync_svc: 共享 SyncService 实例（用于调用 sync_custom_item / sync_movie_watching）。
                      未传入时使用模块级单例。
        """
        if sync_svc is None:
            from ..sync_service import sync_service as sync_svc
        try:
            ev = plex_data["event"]
            if ev not in ("media.play", "media.scrobble"):
                logger.debug(f"事件类型{ev}无需同步，跳过")
                return SyncResponse(status="ignored", message=f"事件类型{ev}无需同步")

            md = plex_data["Metadata"]
            mtype = (md.get("type") or "").lower()
            if ev == "media.play" and mtype != "movie":
                logger.debug(f"事件类型{ev}非电影，无需同步")
                return SyncResponse(
                    status="ignored",
                    message=f"事件类型{ev}非电影，无需同步",
                )

            if mtype == "movie":
                logger.debug(
                    f"接收到Plex同步请求：{plex_data['event']} "
                    f"{plex_data['Account']['title']} 电影 {md.get('title', '')}"
                )
            else:
                logger.debug(
                    f"接收到Plex同步请求：{plex_data['event']} {plex_data['Account']['title']} "
                    f"S{md['parentIndex']:02d}E{md['index']:02d} {md.get('grandparentTitle', '')}"
                )

            # 提取数据并调用自定义同步
            custom_item = extract_plex_data(plex_data)
            logger.debug(f"Plex重新组装JSON报文：{custom_item}")

            if ev == "media.play":
                return sync_svc.sync_movie_watching(custom_item, source=PLEX_SYNC_SOURCE)
            return sync_svc.sync_custom_item(custom_item, source=PLEX_SYNC_SOURCE)
        except Exception as e:
            logger.error(f"Plex同步处理出错: {e}")
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")


plex_sync_service = PlexSyncService()
