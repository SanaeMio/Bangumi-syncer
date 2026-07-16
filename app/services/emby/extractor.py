"""Emby Webhook 数据提取"""

from __future__ import annotations

from typing import Any

from ...core.logging import logger
from ...models.sync import CustomItem
from ...utils.media_type_detector import detect_media_type


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
        ori_str = ori if ori and str(ori).strip() else ""
        # 电影也检测是否为真人电影（三次元）
        detected = detect_media_type(title=title, ori_title=ori_str, item_type=itype)
        return CustomItem(
            media_type=detected,
            title=title,
            ori_title=ori_str if ori_str else None,
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

    # 修复：从 OriginalTitle 提取原始标题（不再硬编码空格）
    ori = item.get("OriginalTitle")
    ori_str = str(ori).strip() if ori else ""
    title = item.get("SeriesName") or ""

    # 检测 OVA/OAD/三次元类型
    detected = detect_media_type(title=title, ori_title=ori_str, item_type=itype)

    return CustomItem(
        media_type=detected,
        title=title,
        ori_title=ori_str if ori_str else " ",
        season=item["ParentIndexNumber"],
        episode=item["IndexNumber"],
        release_date=release_date,
        user_name=emby_data["User"]["Name"],
        source="emby",
    )
