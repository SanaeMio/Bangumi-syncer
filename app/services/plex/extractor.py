"""Plex Webhook 数据提取"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ...models.sync import CustomItem
from ...utils.media_type_detector import detect_media_type


def extract_plex_data(plex_data: dict[str, Any]) -> CustomItem:
    """从Plex数据中提取CustomItem所需的字段"""

    md = plex_data["Metadata"]
    mtype = (md.get("type") or "episode").lower()

    if mtype == "movie":
        release_date = ""
        if md.get("originallyAvailableAt"):
            release_date = md["originallyAvailableAt"]
        else:
            logger.debug(
                "未找到originallyAvailableAt字段，将尝试从bangumi-data获取日期信息"
            )
        title = (md.get("title") or "").strip()
        ori = md.get("originalTitle")
        ori_str = ori if ori and str(ori).strip() else ""
        # 电影也检测是否为真人电影（三次元）
        detected = detect_media_type(title=title, ori_title=ori_str, item_type=mtype)
        return CustomItem(
            media_type=detected,
            title=title,
            ori_title=ori_str if ori_str else None,
            season=1,
            episode=1,
            release_date=release_date,
            user_name=plex_data["Account"]["title"],
            source="plex",
        )

    # 获取发行日期，如果不存在则设置为空字符串
    release_date = ""
    if md.get("originallyAvailableAt"):
        release_date = md["originallyAvailableAt"]
    else:
        logger.debug(
            "未找到originallyAvailableAt字段，将尝试从bangumi-data获取日期信息"
        )

    original_title = md.get("originalTitle", " ")
    title = md.get("grandparentTitle") or ""

    # 检测 OVA/OAD/三次元类型
    detected = detect_media_type(
        title=title,
        ori_title=str(original_title) if original_title else "",
        item_type=mtype,
    )

    return CustomItem(
        media_type=detected,
        title=title,
        ori_title=original_title,
        season=md["parentIndex"],
        episode=md["index"],
        release_date=release_date,
        user_name=plex_data["Account"]["title"],
        source="plex",
    )
