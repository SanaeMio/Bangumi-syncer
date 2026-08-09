"""标题归一化工具

职责：
- 剥离调用方（fongmi/Plex/Jellyfin 等）推送标题中的季数/集数后缀
- 剥离标题外层的书名号/方括号包裹
- 按主副分隔符拆分标题

这些工具用于 Archive 短路匹配前的标题预处理，提升命中率。
同时被 bangumi_api/search.py 在 Archive miss 降级到 API 时复用，
保持 API 与 archive 路径的剥离逻辑一致。

与 _fts_query._normalize_key 的区别：
- _normalize_key 是 FTS5 索引构建期的轻量归一化（NFKC + 去标点 + 小写）
- 本模块处理的是调用方推送的脏标题（含季后缀/包裹符/主副分隔），
  属于业务层预处理，archive 自身索引的是干净原始标题，无需这些逻辑。
"""

from __future__ import annotations

import re

# 季数/集数后缀剥离模式（按优先级排序，长模式在前）
# 处理 fongmi/Plex/Jellyfin 等传入的「标题 S06E279」「标题 第N季」「标题 第二季」等格式，
# 剥离后用核心标题去 archive 匹配（archive 中存的是第一季本体名，不含季后缀）
_SEASON_EPISODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # S06E279 / S6E27 / S01E01（含可选空格与分隔符）
    re.compile(r"\s*S\s*\d+\s*E\s*\d+.*$", re.IGNORECASE),
    # 第N季 / 第N期（阿拉伯数字）
    re.compile(r"\s*第\s*\d+\s*[期季].*$"),
    # 第N季 / 第N期（中文数字，含"十一"~"十九"）
    re.compile(r"\s*第\s*[一二三四五六七八九十]+\s*[期季].*$"),
    # 第二季 / 第二期（直接拼接，无"第"字前缀的少见情况，保留兼容）
    re.compile(r"\s*[一二三四五六七八九十]+\s*[期季].*$"),
    # Season N / 2nd season / Nth season
    re.compile(r"\s*season\s*\d+.*$", re.IGNORECASE),
    re.compile(r"\s*\d+(?:st|nd|rd|th)?\s*season.*$", re.IGNORECASE),
    # 上半 / 下半 / 第2部分
    re.compile(r"\s*(?:上|下)半.*$"),
    re.compile(r"\s*第\s*\d+\s*部分.*$"),
    # 第N部 / 第N章 / 第N篇（中文数字，含"十一"~"十九"）
    # 注意：仅剥离末尾的"第N部"等后缀，避免误剥"第三部门"等复合词
    re.compile(r"\s*第\s*\d+\s*部$"),
    re.compile(r"\s*第\s*[一二三四五六七八九十]+\s*部$"),
    # 罗马数字版本号后缀（末尾空格 + II/III/IV/VI/VII/VIII/IX/XI 等 2 字符以上）
    # 仅匹配末尾，避免误剥"Star Wars: Episode IV"（中间 IV）
    # 单字符 I 不剥离（容易误剥人名/缩写如 "Henry I"）
    re.compile(r"\s+[IVX]{2,}$"),
    # vN / ver.N / version N / vol.N 末尾版本号
    re.compile(r"\s+(?:v|ver|version|vol)\.?\s*\d+$", re.IGNORECASE),
)

# 媒体前缀变体：当核心标题精确命中但全部被 type 过滤时，
# 尝试给核心标题加这些前缀再匹配（如查询「クドわふたー」精确命中同名游戏，
# type 过滤后空，尝试「劇場版 クドわふたー」精确命中剧场版动画）。
# 顺序按常见度排序，劇場版最常见。
_MEDIA_PREFIX_VARIANTS: tuple[str, ...] = (
    "劇場版 ",
    "剧场版 ",
    "OVA ",
    "OAD ",
)

# 标题主副分隔符：用于把「魔法少女小圆：叛逆的物语」拆成主段+副段。
# 优先级按常见度排序：冒号（含全角）最常见，破折号次之。
# 波浪号（～/〜/~）在日文标题中常作为副标题分隔，与「ー」长音符号不同。
# 注意：〜 (U+301C WAVE DASH，日文标准) 与 ～ (U+FF5E 全角) 是不同字符，
# 日文 archive 标题常用 U+301C，需同时包含。
_TITLE_SPLIT_SEPARATORS: tuple[str, ...] = (
    "：",
    ":",
    "～",  # 全角波浪号 U+FF5E
    "〜",  # 日文波浪号 U+301C（archive 中常见）
    "~",  # 半角波浪号 U+007E
    "—",  # 全角破折号
    "－",  # 全角连字符
    "-",  # 半角连字符（最后，避免误分割复合词如 Spider-Man）
)

# 标题包裹符：用于剥离标题外层的书名号/方括号。
# 如「「君の名は。」」→「君の名は。」
_TITLE_WRAPPER_PAIRS: tuple[tuple[str, str], ...] = (
    ("「", "」"),
    ("『", "』"),
    ("【", "】"),
    ("《", "》"),
    ("〈", "〉"),
    ("[", "]"),
    ("(", ")"),
)


def _strip_season_episode_suffix(title: str) -> str:
    """剥离标题末尾的季数/集数后缀，返回核心标题。

    场景：fongmi/媒体库传入「完美世界 S06E279」「完美世界 第六季」等，
    archive 中存的是「完美世界」，直接匹配会因 fuzzy 阈值过低而 miss。
    剥离后用核心标题再试，命中率显著提升。

    保留策略：仅剥离能识别的后缀，无法识别时原样返回（不影响精确匹配）。
    """
    if not title:
        return title
    cleaned = title.strip()
    for pattern in _SEASON_EPISODE_PATTERNS:
        m = pattern.search(cleaned)
        if m:
            stripped = cleaned[: m.start()].strip()
            # 剥离后需保留实质内容（长度 >= 2），避免误剥成空串
            if len(stripped) >= 2:
                return stripped
    return cleaned


def _strip_title_wrappers(title: str) -> str:
    """剥离标题外层的书名号/方括号包裹

    场景：媒体库推送「「君の名は。」」「『進撃の巨人』」时，
    archive 中存的是无包裹的「君の名は。」「進撃の巨人」。
    剥离外层包裹符让精确匹配能命中。

    注意：仅剥离最外层一对，避免误剥嵌套结构。
    标题内部仍含包裹符时（如「Re:ゼロ」）保持原样。
    """
    if not title:
        return title
    cleaned = title.strip()
    for open_w, close_w in _TITLE_WRAPPER_PAIRS:
        if len(cleaned) >= 2 and cleaned[0] == open_w and cleaned[-1] == close_w:
            inner = cleaned[1:-1].strip()
            # 剥离后需保留实质内容
            if len(inner) >= 2:
                return inner
    return cleaned


def _split_title_segments(title: str) -> list[str]:
    """按主副分隔符拆分标题，返回非空段列表（已 strip）

    场景：媒体库推送「魔法少女小圆：叛逆的物语」时，
    archive 中可能仅存主标题「魔法少女小圆」。
    拆分后用主段精确匹配，避免依赖低分模糊匹配。

    拆分策略：
    1. 优先按冒号（含全角）拆分，最常见的主副分隔
    2. 次按破折号、波浪号拆分
    3. 拆分后过滤空段和过短段（< 2 字符，避免误匹配）

    Returns:
        拆分后的段列表（按出现顺序），首段是主标题。
        无分隔符时返回 [title]（单元素列表）。
    """
    if not title:
        return []
    cleaned = title.strip()
    # 找出标题中首次出现的主副分隔符位置（按优先级）
    # 一次拆分即可，避免过度拆分丢失语义
    for sep in _TITLE_SPLIT_SEPARATORS:
        idx = cleaned.find(sep)
        if idx > 0:  # 0 表示分隔符在开头（如「:Re:从零...」），不算主副分隔
            main = cleaned[:idx].strip()
            sub = cleaned[idx + len(sep) :].strip()
            if len(main) >= 2:
                # 返回主段 + 副段（副段可空，调用方按需使用）
                if sub:
                    return [main, sub]
                return [main]
    return [cleaned]
