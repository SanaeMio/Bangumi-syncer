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

# ===== 章节类型（Episode Type，官方 bangumi/common 编号）=====
EPISODE_TYPE_NORMAL = 0  # 本篇
EPISODE_TYPE_SP = 1  # 特别篇
EPISODE_TYPE_OP = 2  # 片头
EPISODE_TYPE_ED = 3  # 片尾

# ===== 关联类型（anime/real 共用同一套编号）=====
# 来源：bangumi/common subject_relations.yml（官方 web API 与库 dump 同一编号
# 体系，2026-09-09 实证考证见 app/utils/bangumi_archive/_store.py 注释）。
RELATION_ID_ADAPTATION = 1  # 改编
RELATION_ID_PREQUEL = 2  # 前传
RELATION_ID_SEQUEL = 3  # 续集
RELATION_ID_SUMMARY = 4  # 总集篇
RELATION_ID_FULL_STORY = 5  # 全集
RELATION_ID_SIDE_STORY = 6  # 番外篇
RELATION_ID_CHARACTER = 7  # 角色出演
RELATION_ID_SAME_SETTING = 8  # 相同世界观
RELATION_ID_ALTERNATIVE_SETTING = 9  # 不同世界观
RELATION_ID_ALTERNATIVE_VERSION = 10  # 不同演绎
RELATION_ID_SPIN_OFF = 11  # 衍生
RELATION_ID_PARENT_STORY = 12  # 主线故事
RELATION_ID_COLLABORATION = 14  # 联动
RELATION_ID_OTHER = 99  # 其他

# 关联类型完整表：id → 中文名
# 使用上面的 ID 常量作为 key，避免重复定义
RELATIONS: dict[int, str] = {
    RELATION_ID_ADAPTATION: "改编",
    RELATION_ID_PREQUEL: "前传",
    RELATION_ID_SEQUEL: "续集",
    RELATION_ID_SUMMARY: "总集篇",
    RELATION_ID_FULL_STORY: "全集",
    RELATION_ID_SIDE_STORY: "番外篇",
    RELATION_ID_CHARACTER: "角色出演",
    RELATION_ID_SAME_SETTING: "相同世界观",
    RELATION_ID_ALTERNATIVE_SETTING: "不同世界观",
    RELATION_ID_ALTERNATIVE_VERSION: "不同演绎",
    RELATION_ID_SPIN_OFF: "衍生",
    RELATION_ID_PARENT_STORY: "主线故事",
    RELATION_ID_COLLABORATION: "联动",
    RELATION_ID_OTHER: "其他",
}

# 反向查找：中文 → id（由 RELATIONS 自动推导，避免重复维护）
RELATION_CN_TO_ID: dict[str, int] = {cn: rid for rid, cn in RELATIONS.items()}

# ===== 平台类型 =====
# 平台 ID 常量（来源：bangumi/common subject_platforms.yml）
# Anime 平台 ID
PLATFORM_ANIME_NONE = 0  # 其他
PLATFORM_ANIME_TV = 1
PLATFORM_ANIME_OVA = 2
PLATFORM_ANIME_MOVIE = 3  # 剧场版
PLATFORM_ANIME_SHORT_FILM = 4  # 短片
PLATFORM_ANIME_WEB = 5
PLATFORM_ANIME_COMIC = 2006  # 动态漫画

# Real 平台 ID
PLATFORM_REAL_NONE = 0  # 其他
PLATFORM_REAL_JP = 1  # 日剧
PLATFORM_REAL_EN = 2  # 欧美剧
PLATFORM_REAL_CN = 3  # 华语剧
PLATFORM_REAL_TV = 6001  # 电视剧
PLATFORM_REAL_MOVIE = 6002  # 电影
PLATFORM_REAL_LIVE = 6003  # 演出
PLATFORM_REAL_SHOW = 6004  # 综艺

# Anime 平台表：id → 中文名（与官方 subject_platforms.yml 逐项对齐；
# 上游新增/改动平台时 tests/utils/test_platform_constants.py 的快照守卫会失败）
ANIME_PLATFORMS: dict[int, str] = {
    PLATFORM_ANIME_NONE: "其他",
    PLATFORM_ANIME_TV: "TV",
    PLATFORM_ANIME_OVA: "OVA",
    PLATFORM_ANIME_MOVIE: "剧场版",
    PLATFORM_ANIME_SHORT_FILM: "短片",
    PLATFORM_ANIME_WEB: "WEB",
    PLATFORM_ANIME_COMIC: "动态漫画",
}

# Real 平台表：id → 中文名
REAL_PLATFORMS: dict[int, str] = {
    PLATFORM_REAL_NONE: "其他",
    PLATFORM_REAL_JP: "日剧",
    PLATFORM_REAL_EN: "欧美剧",
    PLATFORM_REAL_CN: "华语剧",
    PLATFORM_REAL_TV: "电视剧",
    PLATFORM_REAL_MOVIE: "电影",
    PLATFORM_REAL_LIVE: "演出",
    PLATFORM_REAL_SHOW: "综艺",
}

# 按 subject.type 选平台解码表：resolve_platform_name 与守卫测试共用
PLATFORMS_BY_TYPE: dict[int, dict[int, str]] = {
    SUBJECT_TYPE_ANIME: ANIME_PLATFORMS,
    SUBJECT_TYPE_REAL: REAL_PLATFORMS,
}

# ===== 同 IP / 同系列关系图闭包（franchise）采用的关系类型集合 =====
# 成员（编号后为真实库 a.db 边数）——均属「同一作品 / IP 宇宙」边：
#   1 改编 2132 / 2 前传 13249 / 3 续集 13281 / 4 总集篇 1189 /
#   8 相同世界观 3084 / 10 不同演绎 6531 / 12 主线故事 3864
# 排除噪声边（会把无关条目连进闭包 → franchise 兜底错标）：
#   7 角色出演 3903：「相同角色、没有关联的故事」——角色宇宙型 IP（假面骑士等）
#     兄弟系列会互拉进闭包，实测仮面ライダーW 2 跳闭包 93 → 去 7/9 后 17。
#   9 不同世界观 1352：相同主演角色、不同时间线，非同一故事宇宙。
#   5 全集 1190 / 6 番外篇 2815 / 11 衍生 2794：章节体系与本篇不同
#     （11 衍生即 #17「鬼灭广播」错标根因，离线侧本就排除）。
#   14 联动 465 / 99 其他 3745：联动活动/现实活动等无关边。
FRANCHISE_RELATION_TYPES: tuple[int, ...] = (
    RELATION_ID_ADAPTATION,
    RELATION_ID_PREQUEL,
    RELATION_ID_SEQUEL,
    RELATION_ID_SUMMARY,
    RELATION_ID_SAME_SETTING,
    RELATION_ID_ALTERNATIVE_VERSION,
    RELATION_ID_PARENT_STORY,
)

# 在线降级（Bangumi 官方 web API）对应的「同 IP 宇宙」relation 中文名集合。
# 直接由 FRANCHISE_RELATION_TYPES 经 RELATIONS 推导，结构上杜绝离线/在线
# 两侧语义漂移（等价于 {改编, 前传, 续集, 总集篇, 相同世界观, 不同演绎, 主线故事}）。
# ⚠ 历史注记：本集合曾人工维护——「衍生」曾误入（2026-09-09 #17 修复移除）；
#   「番外篇」曾与离线集合不一致。改推导生成后两侧永久对齐。
FRANCHISE_RELATION_CN_SET: frozenset[str] = frozenset(
    RELATIONS[code] for code in FRANCHISE_RELATION_TYPES
)
