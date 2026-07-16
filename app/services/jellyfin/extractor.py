"""Jellyfin Webhook 数据提取"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ...models.sync import CustomItem
from ...utils.media_type_detector import detect_media_type


def extract_jellyfin_data(jellyfin_data: dict[str, Any]) -> CustomItem:
    """从Jellyfin数据中提取CustomItem所需的字段"""

    release_date = ""
    if jellyfin_data.get("release_date"):
        release_date = jellyfin_data["release_date"]
    else:
        logger.debug("未找到release_date字段，将尝试从bangumi-data获取日期信息")

    title = (jellyfin_data.get("title") or "").strip()
    ori_title = jellyfin_data.get("ori_title") or ""
    ori_str = str(ori_title).strip() if ori_title else ""
    mtype = (jellyfin_data.get("media_type") or "episode").lower()

    if mtype == "movie":
        # 电影也检测是否为真人电影（三次元）
        detected = detect_media_type(title=title, ori_title=ori_str, item_type=mtype)
        return CustomItem(
            media_type=detected,
            title=title,
            ori_title=ori_str if ori_str else None,
            season=1,
            episode=1,
            release_date=release_date,
            user_name=jellyfin_data["user_name"],
            source="jellyfin",
        )

    # 检测 OVA/OAD/三次元类型
    detected = detect_media_type(title=title, ori_title=ori_str, item_type=mtype)

    return CustomItem(
        media_type=detected,
        title=title,
        ori_title=jellyfin_data["ori_title"],
        season=jellyfin_data["season"],
        episode=jellyfin_data["episode"],
        release_date=release_date,
        user_name=jellyfin_data["user_name"],
        source="jellyfin",
    )
