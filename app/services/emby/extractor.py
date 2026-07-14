"""Emby Webhook 数据提取"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ...models.sync import CustomItem


def extract_emby_data(emby_data: dict[str, Any]) -> CustomItem:
    """从Emby数据中提取CustomItem所需的字段"""

    item = emby_data["Item"]
    itype = (item.get("Type") or "episode").lower()

    if itype == "movie":
        release_date = ""
        if item.get("PremiereDate"):
            release_date = item["PremiereDate"][:10]
        else:
            logger.debug("未找到PremiereDate字段，将尝试从bangumi-data获取日期信息")
        title = (item.get("Name") or "").strip()
        ori = item.get("OriginalTitle")
        return CustomItem(
            media_type="movie",
            title=title,
            ori_title=ori if ori and str(ori).strip() else None,
            season=1,
            episode=1,
            release_date=release_date,
            user_name=emby_data["User"]["Name"],
            source="emby",
        )

    release_date = ""
    if item.get("PremiereDate"):
        release_date = item["PremiereDate"][:10]
    else:
        logger.debug("未找到PremiereDate字段，将尝试从bangumi-data获取日期信息")

    return CustomItem(
        media_type=item["Type"].lower(),
        title=item["SeriesName"],
        ori_title=" ",
        season=item["ParentIndexNumber"],
        episode=item["IndexNumber"],
        release_date=release_date,
        user_name=emby_data["User"]["Name"],
        source="emby",
    )
