"""文本处理相关的常量映射表

集中管理项目内多处复用的中文数字、标点、平台权重等映射常量。
消除原 episodes.py / season_info.py / fongmi/client.py 中 _CN_NUM 的三处重复定义。
"""

from .bangumi_constants import ANIME_PLATFORMS, REAL_PLATFORMS

# ===== 中文数字映射（1-10，支持"十一"~"十九"组合）=====
CN_NUM: dict[str, int] = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

# ===== 中文标点 → 半角/标准形式映射 =====
# 调用方使用 str.maketrans(PUNCTUATION_MAP) 构造翻译表
PUNCTUATION_MAP: dict[str, str] = {
    "：": ":",
    "；": ";",
    "，": ",",
    "。": ".",
    "？": "?",
    "！": "!",
    "（": "(",
    "）": ")",
    "【": "[",
    "】": "]",
    "《": "<",
    "》": ">",
    "「": "'",
    "」": "'",
    "『": "'",
    "』": "'",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "～": "~",
    "・": "·",
    "･": "·",
    "—": "-",
    "–": "-",
    "―": "-",
}

# ===== 平台权重表（标题归一化用）=====
# 默认权重（未识别的 platform / 未在下方基线表中显式配置的平台）
DEFAULT_PLATFORM_WEIGHT = 50

# Bangumi 真实可能返回的所有 platform 中文名（来自 ANIME_PLATFORMS + REAL_PLATFORMS 去重保留顺序）
# 复用官方平台表，避免权重表与 API 取值脱节
_ALL_PLATFORMS: list[str] = list(
    dict.fromkeys([*ANIME_PLATFORMS.values(), *REAL_PLATFORMS.values()])
)

# TV 模式权重基线：剧集优先 → 电影类降权 → 其他类型衰减
_TV_MODE_BASE_WEIGHTS: dict[str, int] = {
    "TV": 100,
    "WEB": 90,
    "日剧": 85,
    "欧美剧": 85,
    "华语剧": 85,
    "电视剧": 85,
    "OVA": 70,
    "剧场版": 50,
    "电影": 50,
    "短片": 30,
    "动态漫画": 20,
    "演出": 10,
    "综艺": 10,
    "其他": 0,
}

# Movie 模式权重基线：剧场版/电影优先 → 剧集降权
_MOVIE_MODE_BASE_WEIGHTS: dict[str, int] = {
    "剧场版": 100,
    "电影": 100,
    "OVA": 70,
    "短片": 60,
    "TV": 40,
    "WEB": 40,
    "日剧": 30,
    "欧美剧": 30,
    "华语剧": 30,
    "电视剧": 30,
    "动态漫画": 20,
    "演出": 10,
    "综艺": 10,
    "其他": 0,
}

# 由基线权重 × 全量平台表自动推导：Bangumi 新增平台时自动回落到 DEFAULT_PLATFORM_WEIGHT
PLATFORM_WEIGHT_TV_MODE: dict[str, int] = {
    p: _TV_MODE_BASE_WEIGHTS.get(p, DEFAULT_PLATFORM_WEIGHT) for p in _ALL_PLATFORMS
}

PLATFORM_WEIGHT_MOVIE_MODE: dict[str, int] = {
    p: _MOVIE_MODE_BASE_WEIGHTS.get(p, DEFAULT_PLATFORM_WEIGHT) for p in _ALL_PLATFORMS
}
