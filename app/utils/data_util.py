"""
数据提取工具模块
"""
import ast
import traceback
from ..models.sync import CustomItem
from ..core.logging import logger


def extract_plex_json(s):
    # 检查输入是否是字节串，如果不是，则将其转换为字节串
    if isinstance(s, str):
        s = s.encode('utf-8')  # 假设字符串是 UTF-8 编码的

    # 查找起始位置
    start_index = s.find(b'\r\n{')  # 在字节串上使用字节串进行查找
    if start_index == -1:
        return None  # 如果找不到起始位置，则返回 None

    # 查找结束位置
    end_index = s.find(b'}\r\n', start_index)  # 在字节串上使用字节串进行查找
    if end_index == -1:
        return None  # 如果找不到结束位置，则返回 None

    # 截取 JSON 字符串
    json_bytes = s[start_index + 2:end_index + 3]  # 加上起始位置偏移量和长度

    # 将字节串解码为字符串
    json_str = json_bytes.decode('utf-8')  # 假设 JSON 字符串是 UTF-8 编码的

    return json_str


def extract_plex_data(plex_data):
    """从Plex数据中提取CustomItem所需的字段"""
    
    # 获取发行日期，如果不存在则设置为空字符串
    release_date = ""
    if "originallyAvailableAt" in plex_data["Metadata"] and plex_data["Metadata"]["originallyAvailableAt"]:
        release_date = plex_data["Metadata"]["originallyAvailableAt"]
    else:
        logger.debug(f'未找到originallyAvailableAt字段，将尝试从bangumi-data获取日期信息')

    original_title = plex_data["Metadata"].get("originalTitle", " ")

    # 重新组装数据
    return CustomItem(
        media_type=plex_data["Metadata"]["type"],
        title=plex_data["Metadata"]["grandparentTitle"],
        ori_title=original_title,
        season=plex_data["Metadata"]["parentIndex"],
        episode=plex_data["Metadata"]["index"],
        release_date=release_date,
        user_name=plex_data["Account"]["title"],
        source="plex"
    )


def extract_emby_data(emby_data):
    """从Emby数据中提取CustomItem所需的字段"""
    
    # 获取发行日期，如果不存在则设置为空字符串
    release_date = ""
    if "PremiereDate" in emby_data["Item"]:
        release_date = emby_data["Item"]["PremiereDate"][:10]
    else:
        logger.debug(f'未找到PremiereDate字段，将尝试从bangumi-data获取日期信息')
    
    # 重新组装数据
    return CustomItem(
        media_type=emby_data["Item"]["Type"].lower(),
        title=emby_data["Item"]["SeriesName"],
        ori_title=" ",
        season=emby_data["Item"]["ParentIndexNumber"],
        episode=emby_data["Item"]["IndexNumber"],
        release_date=release_date,
        user_name=emby_data["User"]["Name"],
        source="emby"
    )


def extract_jellyfin_data(jellyfin_data):
    """从Jellyfin数据中提取CustomItem所需的字段"""
    
    # 获取发行日期，如果不存在则设置为空字符串
    release_date = ""
    if "release_date" in jellyfin_data and jellyfin_data["release_date"]:
        release_date = jellyfin_data["release_date"]
    else:
        logger.debug(f'未找到release_date字段，将尝试从bangumi-data获取日期信息')

    # 重新组装数据
    return CustomItem(
        media_type=jellyfin_data["media_type"].lower(),
        title=jellyfin_data["title"],
        ori_title=jellyfin_data["ori_title"],
        season=jellyfin_data["season"],
        episode=jellyfin_data["episode"],
        release_date=release_date,
        user_name=jellyfin_data["user_name"],
        source="jellyfin"
    )
