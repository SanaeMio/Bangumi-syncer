"""Plex Webhook 数据提取"""

from ...core.logging import logger
from ...models.sync import CustomItem


def extract_plex_data(plex_data):
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
        return CustomItem(
            media_type="movie",
            title=title,
            ori_title=ori if ori and str(ori).strip() else None,
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

    return CustomItem(
        media_type=md.get("type", "episode"),
        title=md["grandparentTitle"],
        ori_title=original_title,
        season=md["parentIndex"],
        episode=md["index"],
        release_date=release_date,
        user_name=plex_data["Account"]["title"],
        source="plex",
    )
