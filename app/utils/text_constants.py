"""文本处理相关的常量映射表

集中管理项目内多处复用的中文数字、标点、平台权重等映射常量。
消除原 episodes.py / season_info.py / fongmi/client.py 中 _CN_NUM 的三处重复定义。
"""

from .bangumi_constants import (
    ANIME_PLATFORMS,
    PLATFORMS_BY_TYPE,
    REAL_PLATFORMS,
)

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

# ⚠ 脆弱耦合警示（2026-09-09）：archive 路径的 _sort_candidates_by_platform
# 当前【未接入】resolve_platform_name 解码——platform 原始值（数字编码字符串）
# 在下方权重表中必然查不到 → 恒回落 DEFAULT_PLATFORM_WEIGHT=50 → 稳定排序保持
# archive 自身相关性序（"歪打正着"）。任何依赖"排序结果"的行为都隐式依赖这一
# 不变量：修改基线权重表、weight() 内部或贸然接入解码都会破坏它（实测接入会让
# 240 L2 命中率 97.9% → 97.5%）。接入前先用
# tests/utils/test_platform_weight.py 与 scripts/golden_check.py 量化影响。

# 由基线权重 × 全量平台表自动推导：Bangumi 新增平台时自动回落到 DEFAULT_PLATFORM_WEIGHT
PLATFORM_WEIGHT_TV_MODE: dict[str, int] = {
    p: _TV_MODE_BASE_WEIGHTS.get(p, DEFAULT_PLATFORM_WEIGHT) for p in _ALL_PLATFORMS
}

PLATFORM_WEIGHT_MOVIE_MODE: dict[str, int] = {
    p: _MOVIE_MODE_BASE_WEIGHTS.get(p, DEFAULT_PLATFORM_WEIGHT) for p in _ALL_PLATFORMS
}


def resolve_platform_name(platform: object, subject_type: object = None) -> str:
    """把 ``platform`` 归一化为**中文名**，供上面的权重表查表。

    两条数据来源的取值形态不同，必须在这里统一：

    - **API 路径**：platform 已是中文名（``"TV"`` / ``"WEB"`` / ``"华语剧"`` …），原样返回。
    - **Archive 路径**：platform 存的是**字符串形式的数字编码**
      （``"1"`` / ``"5"`` / ``"6002"`` …），且**按 subject.type 分域**：
        | subject.type | 编码含义                                                  |
        |---|---|
        | 2（动画）     | 0=其他 1=TV 2=OVA 3=剧场版 4=短片 5=WEB 2006=动态漫画          |
        | 6（三次元）   | 0=其他 1=日剧 2=欧美剧 3=华语剧 6001=电视剧 6002=电影 6003=演出 6004=综艺 |

      即**同一个数字在不同 type 下含义不同**（``3`` 在动画是「剧场版」，在三次元是
      「华语剧」），必须按 type 选表解码。

    若不做这层解码，数字编码在中文名权重表里必然查不到而回落到
    ``DEFAULT_PLATFORM_WEIGHT`` —— 排序**静默失效**（所有候选同权 → 稳定排序
    保持原序），平台权重的设计意图在 archive 路径上从未生效，且与 API 路径
    行为不一致。

    Args:
        platform: 原始 platform 取值（中文名 / 数字 / 数字字符串 / None）。
        subject_type: 条目的 subject.type（2=动画 / 6=三次元），用于选解码表。
            无法判定时按动画表解码（本仓库主体场景为动画）。

    ⚠ Archive 路径调用【必须】传入 subject_type：type 未知时默认按动画表
      解码，三次元编码（6001~6004）会解码为空串、2/3 会被误解码为
      OVA/剧场版（跨域错码比死码更危险）。

    Returns:
        中文平台名；无法识别时返回空串（由调用方回落到默认权重）。
    """
    if platform is None:
        return ""

    # --- 已是中文名（API 路径）→ 原样返回 ---
    if isinstance(platform, str):
        text = platform.strip()
        if not text:
            return ""
        if not text.isdecimal():
            # isdecimal 而非 isdigit：'²'/'①' 等 isdigit 为 True 但 int() 会抛
            # ValueError；isdecimal 仅放行 int() 一定可解析的十进制数字
            # （含全角 '１２３'）。其余（含 '-1'）按中文名原样透传 → 权重表
            # 查不到 → 回落默认权重，与未知编码行为一致。
            return text
        code = int(text)
    elif isinstance(platform, bool):
        return ""
    elif isinstance(platform, int):
        code = platform
    else:
        return ""

    # --- 数字编码（Archive 路径）→ 按 subject.type 选表解码 ---
    try:
        stype = int(subject_type)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        stype = None
    table = PLATFORMS_BY_TYPE.get(stype, ANIME_PLATFORMS)
    return table.get(code, "")
