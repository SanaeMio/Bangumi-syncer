"""媒体类型检测工具

从标题、URL、item_type 等字段检测媒体类型（OVA/OAD/三次元）。
各驱动共用此模块，避免重复实现关键词匹配逻辑。
"""

from __future__ import annotations

import re

# 剧场版/电影关键词
MOVIE_KEYWORD_RE = re.compile(
    r"剧场版|劇場版|电影|電影|\bMovie\b|\bFilm\b",
    re.IGNORECASE,
)

# OVA/OAD 关键词
OVA_KEYWORD_RE = re.compile(
    r"\bOVA\b|\bOAD\b|特别篇|特別篇|_special|\.SP\b|\bSP\b",
    re.IGNORECASE,
)

# 三次元（日剧/真人版/电影）关键词
REAL_ACTION_KEYWORD_RE = re.compile(
    r"日剧|日劇|真人版|真人|三次元|\bDrama\b|\bLive\s*Action\b|\bJdrama\b",
    re.IGNORECASE,
)

# item_type 中的关键词映射
_ITEM_TYPE_MOVIE_KEYWORDS = ("movie", "film", "电影")
_ITEM_TYPE_EPISODE_KEYWORDS = (
    "episode",
    "series",
    "tv",
    "show",
    "剧集",
    "电视剧",
    "动漫",
    "番剧",
    "综艺",
)
_ITEM_TYPE_REAL_ACTION_KEYWORDS = ("real_action", "drama", "jdrama", "日剧", "真人")


def detect_media_type(
    title: str = "",
    ori_title: str = "",
    url: str = "",
    artist: str = "",
    item_type: str = "",
) -> str:
    """从多个字段检测媒体类型。

    返回值：movie / ova / oad / real_action / episode

    检测优先级：
    1. item_type 中的三次元声明（real_action/drama/日剧/真人）→ real_action
    2. 任意字段命中三次元关键词 → real_action
    3. 任意字段命中 OVA 关键词 → ova
    4. 任意字段命中 OAD 关键词 → oad
    5. 特别篇等泛 OVA 关键词 → ova
    6. item_type 中的电影声明 → movie
    7. 任意字段命中剧场版/电影关键词 → movie
    8. item_type 中的剧集声明 → episode
    9. 默认 → episode

    设计原则：标题中的 OVA/OAD/三次元关键词优先于 item_type 的 movie/episode，
    因为媒体服务器（Plex/Emby）只知道 episode/movie，不知道 OVA/OAD/三次元。
    """
    t = (item_type or "").strip().lower()

    # 收集所有文本字段用于关键词扫描
    texts = [title or "", ori_title or "", url or "", artist or ""]

    # 1. item_type 中的三次元声明
    if t and any(k in t for k in _ITEM_TYPE_REAL_ACTION_KEYWORDS):
        return "real_action"

    # 2. 任意字段命中三次元关键词（优先于 movie，因为真人电影也应走三次元搜索）
    for text in texts:
        if text and REAL_ACTION_KEYWORD_RE.search(text):
            return "real_action"

    # 3. OVA 关键词（精确匹配 OVA）
    for text in texts:
        if text and re.search(r"\bOVA\b", text, re.IGNORECASE):
            return "ova"

    # 4. OAD 关键词（精确匹配 OAD）
    for text in texts:
        if text and re.search(r"\bOAD\b", text, re.IGNORECASE):
            return "oad"

    # 5. 特别篇等泛 OVA 关键词
    for text in texts:
        if text and OVA_KEYWORD_RE.search(text):
            return "ova"

    # 6. item_type 中的电影声明
    if t and any(k in t for k in _ITEM_TYPE_MOVIE_KEYWORDS):
        return "movie"

    # 7. 任意字段命中剧场版/电影关键词
    for text in texts:
        if text and MOVIE_KEYWORD_RE.search(text):
            return "movie"

    # 8. item_type 中的剧集声明
    if t and any(k in t for k in _ITEM_TYPE_EPISODE_KEYWORDS):
        return "episode"

    # 9. item_type 中的 OVA/OAD（较少见）
    if "ova" in t:
        return "ova"
    if "oad" in t:
        return "oad"

    # 10. 默认
    return "episode"
