"""媒体类型检测工具

## 职责划分（2026-09 重构）

媒体类型在两个**不同场景**下被判定，此前混用一个函数、且优先级倒置：

1. **请求侧**——媒体源推来一条播放记录，问「用户在看电影还是剧集」。
   源（Plex / Emby / Jellyfin / Trakt / 飞牛）**明确知道**答案，这是权威信息。
   → 用 :func:`normalize_source_media_type` **直接采信源声明**。

2. **候选侧**——已拿到 Bangumi 条目，问「这个条目是电影/OVA/剧集」。
   结构化信号优先（``type=6`` → real_action），其余靠标题关键词。
   → 用 :func:`detect_media_type` 做关键词判定，
   由 ``sync_service._detect_candidate_media_type`` 组合调用。

   ⚠️ 候选侧**刻意不读** ``platform`` 字段 —— 尽管数据上可得。
   实测把 ``platform=3`` 兜底为 ``movie`` 会让 L2 黄金集命中率
   98.8% → 91.7%（32 处错配）：它使「请求 episode 但命中 platform=3 条目」
   被判为类型冲突 → 触发跨季改选 → 反而选到集数更多的前作。
   详见 ``sync_service._detect_candidate_media_type`` 的文档。

3. **fongmi**（播放器推送）——**只有文件名**，无类型字段。
   → 由 ``fongmi/client.py`` 按**文件名结构**判定（见那里的
   ``detect_media_type_from_media``），本模块只提供其中的关键词正则。

## 为什么不再从标题关键词判「三次元」

历史实现用 ``真人|日剧|Drama`` 等关键词判 ``real_action``，实测两类误判：
- ``真人快打``（Mortal Kombat Legends）**是动画**，却被判 real_action；
- ``机动战士高达 第08MS小队 三次元的战斗`` 同理。

且 ``real_action`` 会**收窄搜索范围**（只搜 Bangumi type=6），
误判直接导致漏标。现改为由配置 ``sync.enable_real_action`` 决定
是否把 type=6 纳入搜索范围，**不再依赖标题猜测**。
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

# 三次元（日剧/真人版）关键词
#
# ⚠️ **已从本模块移除**（2026-09）：标题关键词不再用于推断 real_action
# —— 实测「真人快打」（动画）等误判，且 real_action 会把搜索范围收窄到
# type=6 导致漏标。三次元改由 `sync.enable_real_action` 配置控制。
# 若将来需要「已知是真人内容时的二次确认」，请在此重新引入常量，
# 不要在请求侧用它覆盖媒体源的声明。

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


def normalize_source_media_type(item_type: str) -> str | None:
    """把**媒体源声明的类型**映射为 ``movie`` / ``episode``。

    媒体源（Plex 的 ``Metadata.type``、Emby/Jellyfin 的 ``Item.Type``、
    Trakt 的 history kind、飞牛的 ``item_type``）明确知道自己推的是电影还是
    剧集 —— 这是权威信息，**不应被标题关键词覆盖**。

    历史缺陷：旧实现把 ``item_type`` 排在三类关键词之后，于是
    ``item_type='movie'`` + 标题含「特别篇」会被改判为 ``ova``，
    源的明确声明被三个字推翻。

    Args:
        item_type: 源声明的类型字符串（大小写不敏感）

    Returns:
        ``"movie"`` / ``"episode"``；无法识别时返回 ``None``（调用方自行兜底）
    """
    t = (item_type or "").strip().lower()
    if not t:
        return None
    # 三次元声明优先（真人电影/日剧：源可能标 movie，也可能标 real_action）
    if any(k in t for k in _ITEM_TYPE_REAL_ACTION_KEYWORDS):
        return "real_action"
    if any(k in t for k in _ITEM_TYPE_MOVIE_KEYWORDS):
        return "movie"
    if any(k in t for k in _ITEM_TYPE_EPISODE_KEYWORDS):
        return "episode"
    if "ova" in t:
        return "ova"
    if "oad" in t:
        return "oad"
    return None


def detect_media_type(
    title: str = "",
    ori_title: str = "",
    url: str = "",
    artist: str = "",
    item_type: str = "",
) -> str:
    """从标题/URL 等文本判断**条目**的媒体类型（候选侧）。

    返回值：movie / ova / oad / episode（不再返回 real_action，见下）

    检测优先级：
    1. 任意字段命中 OVA 关键词（精确 ``\\bOVA\\b``）→ ova
    2. 任意字段命中 OAD 关键词 → oad
    3. 特别篇等泛 OVA 关键词 → ova
    4. 任意字段命中剧场版/电影关键词 → movie
    5. ``item_type`` 声明 → 委托 :func:`normalize_source_media_type`
    6. 默认 → episode

    **不再从标题关键词判 real_action**：实测 ``真人快打``（动画）等
    误判，且误判会把搜索范围收窄到 type=6 导致漏标。三次元改由
    ``sync.enable_real_action`` 配置控制搜索范围。

    ⚠️ 本函数**不读** Bangumi 的 ``platform`` 字段（候选侧的
    ``_detect_candidate_media_type`` 也刻意不读，理由见模块 docstring）。
    请求侧请直接用 :func:`normalize_source_media_type`，不要用本函数。

    Args:
        item_type: 源声明的类型；提供时作为**兜底**（关键词优先），
            因为媒体服务器可能把剧场版放进剧集库，标题更具体。
    """
    texts = [title or "", ori_title or "", url or "", artist or ""]

    # 1. OVA（精确匹配，避免 "OVA" 出现在无关词中）
    for text in texts:
        if text and re.search(r"\bOVA\b", text, re.IGNORECASE):
            return "ova"

    # 2. OAD
    for text in texts:
        if text and re.search(r"\bOAD\b", text, re.IGNORECASE):
            return "oad"

    # 3. 特别篇等泛 OVA 关键词
    for text in texts:
        if text and OVA_KEYWORD_RE.search(text):
            return "ova"

    # 4. 剧场版/电影关键词
    for text in texts:
        if text and MOVIE_KEYWORD_RE.search(text):
            return "movie"

    # 5. 源声明兜底
    normalized = normalize_source_media_type(item_type)
    if normalized:
        return normalized

    # 6. 默认
    return "episode"
