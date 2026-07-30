"""Bangumi 官方常量定义

来源：https://github.com/bangumi/common 仓库的 yaml 定义，转写为 Python 静态常量。
仅保留项目用到的条目/收藏/章节类型、anime/real 关联类型、anime/real 平台类型。
"""

# ===== 条目类型（Subject Type）=====
SUBJECT_TYPE_ANIME = 2  # 动画
SUBJECT_TYPE_REAL = 6  # 三次元（日剧/电影等）

# Archive 导入时仅保留的条目类型（动画 + 三次元）
# 业务层（sync_service / archive_shortcut）查询时仅放行 type∈(2,6)，
# 其他类型（书籍/音乐/游戏）在运行时永远不会被命中，导入时直接丢弃以节省磁盘与导入耗时
ARCHIVE_ALLOWED_SUBJECT_TYPES = frozenset({SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL})

# ===== 收藏类型（Collection Type）=====
COLLECTION_TYPE_WISH = 1  # 想看
COLLECTION_TYPE_DONE = 2  # 看过
COLLECTION_TYPE_DOING = 3  # 在看
COLLECTION_TYPE_ON_HOLD = 4  # 搁置
COLLECTION_TYPE_DROPPED = 5  # 抛弃

# ===== 章节类型（Episode Type）=====
EPISODE_TYPE_NORMAL = 0  # 本篇

# ===== 关联类型（anime/real 共用同一套编号）=====
# 关联 ID 常量（项目用到的）
RELATION_ID_PREQUEL = 2  # 前传
RELATION_ID_SEQUEL = 3  # 续集
RELATION_ID_PARENT_STORY = 12  # 主线故事
RELATION_ID_SPIN_OFF = 11  # 衍生
RELATION_ID_SIDE_STORY = 6  # 番外篇

# 关联类型完整表：id → 中文名
# 使用上面的 ID 常量作为 key，避免重复定义
RELATIONS: dict[int, str] = {
    1: "改编",
    RELATION_ID_PREQUEL: "前传",
    RELATION_ID_SEQUEL: "续集",
    4: "总集篇",
    5: "全集",
    RELATION_ID_SIDE_STORY: "番外篇",
    7: "角色出演",
    8: "相同世界观",
    9: "不同世界观",
    10: "不同演绎",
    RELATION_ID_SPIN_OFF: "衍生",
    RELATION_ID_PARENT_STORY: "主线故事",
    14: "联动",
    99: "其他",
}

# 反向查找：中文 → id（由 RELATIONS 自动推导，避免重复维护）
RELATION_CN_TO_ID: dict[str, int] = {cn: rid for rid, cn in RELATIONS.items()}

# ===== 平台类型 =====
# Anime 平台 ID
PLATFORM_ANIME_TV = 1
PLATFORM_ANIME_OVA = 2
PLATFORM_ANIME_MOVIE = 3
PLATFORM_ANIME_WEB = 5

# Real 平台 ID
PLATFORM_REAL_JP = 1
PLATFORM_REAL_TV = 6001
PLATFORM_REAL_MOVIE = 6002

# Anime 平台表：id → 中文名
ANIME_PLATFORMS: dict[int, str] = {
    0: "其他",
    PLATFORM_ANIME_TV: "TV",
    PLATFORM_ANIME_OVA: "OVA",
    PLATFORM_ANIME_MOVIE: "剧场版",
    4: "短片",
    PLATFORM_ANIME_WEB: "WEB",
    2006: "动态漫画",
}

# Real 平台表：id → 中文名
REAL_PLATFORMS: dict[int, str] = {
    0: "其他",
    PLATFORM_REAL_JP: "日剧",
    2: "欧美剧",
    3: "华语剧",
    PLATFORM_REAL_TV: "电视剧",
    PLATFORM_REAL_MOVIE: "电影",
    6003: "演出",
    6004: "综艺",
}
