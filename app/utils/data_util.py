"""
数据提取工具模块
"""

from ..core.logging import logger
from ..models.sync import CustomItem


def extract_plex_json(s):
    # 检查输入是否是字节串，如果不是，则将其转换为字节串
    if isinstance(s, str):
        s = s.encode("utf-8")  # 假设字符串是 UTF-8 编码的

    # 查找起始位置
    start_index = s.find(b"\r\n{")  # 在字节串上使用字节串进行查找
    if start_index == -1:
        return None  # 如果找不到起始位置，则返回 None

    # 查找结束位置
    end_index = s.find(b"}\r\n", start_index)  # 在字节串上使用字节串进行查找
    if end_index == -1:
        return None  # 如果找不到结束位置，则返回 None

    # 截取 JSON 字符串
    json_bytes = s[start_index + 2 : end_index + 3]  # 加上起始位置偏移量和长度

    # 将字节串解码为字符串
    json_str = json_bytes.decode("utf-8")  # 假设 JSON 字符串是 UTF-8 编码的

    return json_str


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


def extract_emby_data(emby_data):
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
        return CustomItem(
            media_type="movie",
            title=title,
            ori_title=None,
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


def extract_jellyfin_data(jellyfin_data):
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
