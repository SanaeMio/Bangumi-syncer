"""Jellyfin Webhook 数据提取"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ...models.sync import CustomItem


def extract_jellyfin_data(jellyfin_data: dict[str, Any]) -> CustomItem:
    """从Jellyfin数据中提取CustomItem所需的字段"""

    release_date = ""
    if jellyfin_data.get("release_date"):
        release_date = jellyfin_data["release_date"]
    else:
        logger.debug("未找到release_date字段，将尝试从bangumi-data获取日期信息")

    mtype = (jellyfin_data.get("media_type") or "episode").lower()
    if mtype == "movie":
        return CustomItem(
            media_type="movie",
            title=jellyfin_data["title"],
            ori_title=jellyfin_data.get("ori_title"),
            season=1,
            episode=1,
            release_date=release_date,
            user_name=jellyfin_data["user_name"],
            source="jellyfin",
        )

    return CustomItem(
        media_type=jellyfin_data["media_type"].lower(),
        title=jellyfin_data["title"],
        ori_title=jellyfin_data["ori_title"],
        season=jellyfin_data["season"],
        episode=jellyfin_data["episode"],
        release_date=release_date,
        user_name=jellyfin_data["user_name"],
        source="jellyfin",
    )
