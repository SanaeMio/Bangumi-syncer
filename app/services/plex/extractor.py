"""Plex Webhook 数据提取"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ...models.sync import CustomItem
from ...utils.media_type_detector import normalize_source_media_type


def extract_plex_data(plex_data: dict[str, Any]) -> CustomItem:
    """从Plex数据中提取CustomItem所需的字段"""

    md = plex_data["Metadata"]
    mtype = (md.get("type") or "episode").lower()

    # 驱动原始 payload（保留与解析相关的字段，过滤掉过长的 artwork/key 等）
    raw_payload = {
        "event": plex_data.get("event"),
        "account": plex_data.get("Account", {}).get("title"),
        "metadata": {
            "type": md.get("type"),
            "title": md.get("title"),
            "grandparentTitle": md.get("grandparentTitle"),
            "originalTitle": md.get("originalTitle"),
            "parentIndex": md.get("parentIndex"),
            "index": md.get("index"),
            "librarySectionTitle": md.get("librarySectionTitle"),
            "originallyAvailableAt": md.get("originallyAvailableAt"),
        },
    }

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
        # Plex 明确声明 movie —— 直接采信，不再用标题关键词覆盖
        # （旧实现会因标题含「特别篇」把 movie 改判为 ova）
        detected = normalize_source_media_type(mtype) or "movie"
        return CustomItem(
            media_type=detected,
            title=title,
            ori_title=ori_str if ori_str else None,
            season=1,
            episode=1,
            release_date=release_date,
            user_name=plex_data["Account"]["title"],
            source="plex",
            raw_payload=raw_payload,
        )

    # 获取发行日期，如果不存在则设置为空字符串
    release_date = ""
    if md.get("originallyAvailableAt"):
        release_date = md["originallyAvailableAt"]
    else:
        logger.debug(
            "未找到originallyAvailableAt字段，将尝试从bangumi-data获取日期信息"
        )

    original_title = md.get("originalTitle")
    title = md.get("grandparentTitle") or ""

    # Plex 明确声明 episode —— 直接采信
    detected = normalize_source_media_type(mtype) or "episode"

    return CustomItem(
        media_type=detected,
        title=title,
        ori_title=original_title,
        season=md["parentIndex"],
        episode=md["index"],
        release_date=release_date,
        user_name=plex_data["Account"]["title"],
        source="plex",
        raw_payload=raw_payload,
    )
