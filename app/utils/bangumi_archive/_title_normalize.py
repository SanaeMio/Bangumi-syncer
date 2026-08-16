"""标题归一化工具

职责：
- 剥离调用方（fongmi/Plex/Jellyfin 等）推送标题中的季数/集数后缀
- 剥离标题外层的书名号/方括号包裹
- 按主副分隔符拆分标题

这些工具用于 Archive 短路匹配前的标题预处理，提升命中率。
同时被 bangumi_api/search.py 在 Archive miss 降级到 API 时复用，
保持 API 与 archive 路径的剥离逻辑一致。

`build_search_variants` 是上述工具的整合入口：生成一份去重、保序的
候选查询标题池，供 Archive 短路（try_search）与 API 兜底（bgm_search）
两条路径共用，从源头消除两路径变体策略的漂移。

与 _fts_query._normalize_key 的区别：
- _normalize_key 是 FTS5 索引构建期的轻量归一化（NFKC + 去标点 + 小写）
- 本模块处理的是调用方推送的脏标题（含季后缀/包裹符/主副分隔），
  属于业务层预处理，archive 自身索引的是干净原始标题，无需这些逻辑。
"""

from __future__ import annotations

import re
from typing import NamedTuple

from rapidfuzz import fuzz

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

# 续篇 / 外传 / 特别篇 / 前传 等「去修饰得核心」尾部后缀（side-story / sequel 维度）
# 经 Archive 真实数据校准（subject 表 56,539 条，name 56,539 / name_cn 40,748，见 §14）：
#   - 特别篇 208（桥接 44）、外传/外傳 48（桥接 16）、番外篇/番外 30（桥接 12）
#   - 前传/前傳 23（桥接 8）、续集/续/續集/續 17（桥接 12）
#   - JP 完結編/完結篇 20（桥接 16）、前編/前篇 47（桥接 7）、後編/後篇 24（桥接 4）
#     、続編/続篇 19（桥接 17）、Part 44
# 这些尾部后缀与季后缀同构（都是"去修饰得核心"），但原 `_strip_season_episode_suffix`
# 只覆盖季数/集数，未覆盖续篇维度 → 导致「向阳素描 特别篇」永远回退不到「向阳素描」。
# 注：评分相似度层 `_TITLE_DECORATORS` 已剥 `特别篇`，此处补齐召回剥离层，两端对齐；
# 顺序：长后缀在前（番外篇 > 番外、续集 > 续），避免误剥。中編/中篇(≤1) 属死维度已排除。
_PART_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*番外篇$"),
    re.compile(r"\s*番外$"),
    re.compile(r"\s*续集$"),
    re.compile(r"\s*續集$"),
    re.compile(r"\s*续$"),
    re.compile(r"\s*續$"),
    re.compile(r"\s*前传$"),
    re.compile(r"\s*前傳$"),
    re.compile(r"\s*外传$"),
    re.compile(r"\s*外傳$"),
    re.compile(r"\s*特别篇$"),
    re.compile(r"\s*完結編$"),
    re.compile(r"\s*完結篇$"),
    re.compile(r"\s*前編$"),
    re.compile(r"\s*前篇$"),
    re.compile(r"\s*後編$"),
    re.compile(r"\s*後篇$"),
    re.compile(r"\s*続編$"),
    re.compile(r"\s*続篇$"),
    re.compile(r"\s*Part\s*\d*$", re.IGNORECASE),
)

# 媒体前缀变体（leading-prefix）：当核心标题精确命中但被 type 过滤，或需要桥接
# 「本体 ↔ 剧场版/映画」时，给核心标题加这些前缀再匹配（如查询「クドわふたー」
# 精确命中同名游戏、type 过滤后空，尝试「劇場版 クドわふたー」精确命中剧场版动画）。
#
# 列表经 Archive 真实数据校准（subject 表 56,539 条，见 docs/design/评分体系统一设计.md §13）：
#   - 劇場版(name) 317、剧场版(name_cn) 240 → 保留（最常见，桥接同作本体）
#   - 映画(name) 111、映画版(name) 1       → 补入（原列表漏收的真实目标）
#   - OVA/OAD 已由评分侧 _strip_media_suffix 处理（trailing 形式 OVA 243 / OAD 283 条真实存在，
#     leading 形式仅 OVA 3 / OAD 0 条，结构性失效），故移除，避免生成永远落空的前缀查询。
# 顺序按 Archive leading 实际出现量降序：劇場版 > 剧场版 > 映画 > 映画版。
_MEDIA_PREFIX_VARIANTS: tuple[str, ...] = (
    "劇場版 ",
    "剧场版 ",
    "映画 ",
    "映画版 ",
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


# 以下为“匹配相似度”归一化（供系统 B / title_diff_ratio 打分使用），
# 与上方“召回预处理”归一化（季后缀/包裹/主段）同属标题字符串处理，
# 一并集中在此模块，便于未来 scorer.py 直接依赖、并消除跨模块漂移。
# 原先定义在 bangumi_api/search.py，于 2026-08-15 搬迁至此。

# 媒体类型后缀：搜索标题常带此类后缀（如"遮天动画版"），而 Bangumi 条目标题
# 通常不含或以不同形式包含（如"遮天 第四季"）。匹配时应剥离后缀比较核心标题。
# 注意：长后缀必须排在短后缀前面，确保优先匹配更具体的后缀。
#
# 经 §15 Archive 真实数据校准（subject 表 56,539 条）：
#   - 动画版(4)/真人版(14 cn)/电影版(69 cn)/TV版(5 cn) 确有桥接，保留；
#   - 动漫版/动漫(0) 为死码、`动画`(9 name/156 cn) 误剥风险高（"动画"是词非后缀，
#     桥接≈0，会剥成"我在日本做"）→ 移除；
#   - OVA/OAD（trailing 243/283 条）按 §15.3 B 结论"大小写归一后删"：因本函数恒在
#     空白折叠后的标题上调用，"Terra Nova"→"terranova" 末尾恰为 ova，无论子串还是
#     末尾剥离都会误剥成 "terr"，无法在折叠语境下做词边界安全剥离 → 不入此表，
#     改由后续词边界感知的归一化层处理（见 §15.3 待办）。
_TITLE_SUFFIXES = ("动画版", "真人版", "电影版", "TV版")

# 标题修饰词：核心标题之外附加的播出形态/编排说明（如"年番""番外""特别篇"）。
# 这些词常在媒体库与 Bangumi 条目间不一致（"斗破苍穹年番" vs "斗破苍穹 年番"），
# 但不影响作品身份，匹配相似度计算前应先去除，避免仅因空格/修饰词差异被误判低分。
#
# 经 §15 Archive 真实数据校准（subject 表 56,539 条）：
#   - 年番(21 name/3 cn)/外传(12 name/108 cn)/特别篇(31 name/275 cn)/总集篇(43 cn)
#     真实且必要，保留；
#   - 半年番/季番(0) 死码，删除；
#   - ova/oad(小写) 漏匹配 90%+ 且子串撞 Terra Nova/El Dorado → 移入 _TITLE_SUFFIXES
#     做末尾剥离（统一大小写不敏感，见上）；
#   - "剧场版 "/"OVA "/"OAD " 带尾空格锚定：归一化已折叠空白，空格锚定永不命中，
#     属死码，删除（"剧场版" 维度由召回前缀 _MEDIA_PREFIX_VARIANTS 覆盖）。
#   - 番外 改为尾部锚定（见 _TITLE_DECORATORS_TRAILING），避免吃掉「番外地」。
_TITLE_DECORATORS = (
    "年番",
    "外传",
    "特别篇",
    "总集篇",
)

# 尾部锚定修饰词：仅当出现在标题末尾时才去除，避免误剥中间出现的同名片段。
# 如「番外地」(独立真实标题) 中的"番外"在中间，不应剥成「地」；
# 而「X 番外」(真实外传条目) 末尾的"番外"应剥除以比对本体。
_TITLE_DECORATORS_TRAILING = ("番外",)

_RE_WHITESPACE = re.compile(r"\s+")


def _normalize_title_for_match(text: str) -> str:
    """匹配相似度用的标题归一化。

    折叠空白（含全/半角空格、连续空格），并去除首尾及内部的修饰词
    （"年番""番外"等，不区分有无空格），返回用于比较的规范化标题。
    不改变原始 title_diff_ratio 调用方的入参。
    """
    if not text:
        return ""
    # 1. 折叠所有空白字符为无
    norm = _RE_WHITESPACE.sub("", text)
    # 2. 去除修饰词（处理"斗破苍穹年番"与"斗破苍穹 年番"两种写法）
    for dec in _TITLE_DECORATORS:
        # 修饰词前后可能带空格（已被折叠为空，此处直接去词）
        norm = norm.replace(dec, "")
    # 3. 尾部锚定修饰词：仅去除标题末尾的"番外"，避免吃掉中间的「番外地」
    for dec in _TITLE_DECORATORS_TRAILING:
        if norm.endswith(dec):
            norm = norm[: -len(dec)]
    return norm.strip()


def _strip_media_suffix(text: str) -> str:
    """剥离标题末尾的媒体类型后缀，返回核心标题。

    仅当剥离后仍有实质内容（长度 >= 2）时才执行剥离，
    避免将"动画"等短标题误剥离为空串。
    """
    if not text:
        return text
    for suffix in _TITLE_SUFFIXES:
        if text.endswith(suffix):
            core = text[: -len(suffix)].strip()
            if len(core) >= 2:
                return core
    return text


def _strip_season_episode_suffix(title: str) -> str:
    """剥离标题末尾的季数/集数后缀与续篇/外传/特别篇等 side-story 后缀，返回核心标题。

    场景：fongmi/媒体库传入「完美世界 S06E279」「完美世界 第六季」「向阳素描 特别篇」等，
    archive 中存的是「完美世界」「向阳素描」，直接匹配会因 fuzzy 阈值过低而 miss。
    剥离后用核心标题再试，命中率显著提升。

    两组尾部模式分别见：
    - `_SEASON_EPISODE_PATTERNS`：季数/集数维度（第N季/第N期/SxxExx/Season N 等）
    - `_PART_SUFFIX_PATTERNS`：续篇/外传/特别篇维度（经 §14 Archive 真实数据校准）
    原 `_strip_part_suffix` 已合并进本函数（2026-08-15），统一在单循环内尝试两类后缀，
    季后缀优先、互不干扰，覆盖「向阳素描 特别篇」→「向阳素描」这类原季后缀剥离漏掉的召回维度。

    保留策略：仅剥离能识别的后缀，无法识别时原样返回（不影响精确匹配）。
    顺序：长后缀在前（番外篇 > 番外、续集 > 续），避免误剥。

    仅当剥离后仍有实质内容（长度 >= 2）时才剥离，避免把短标题误剥为空。
    """
    if not title:
        return title
    cleaned = title.strip()
    for pattern in (*_SEASON_EPISODE_PATTERNS, *_PART_SUFFIX_PATTERNS):
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


# ===== 深度归一化装饰剥离（供 _fts_query._normalize_key_deep 复用） =====
# 这些helper在「原始标题文本」上作业（括号/标点仍在），把媒体库与 archive 中
# 因装饰差异而写法不同的标题归一到同一形态，使精确匹配能跨装饰命中。
# 与 _strip_title_wrappers（仅剥最外层一对包裹符）不同，这里剥除括号及其
# 内部内容、片假名长音/小书假名、数字编号、年份后缀——覆盖反向诊断
# （tests/test_archive_real_benchmark.py）定位到的 R4/R5/R7/R9 真实缺口。

# 括号（含全半角）及其内部内容：如「X（TV）」→「X」、「ラジオ「内包」」→「ラジオ」
_DECOR_BRACKET_RE = re.compile(
    r"\([^)]*\)|\[[^\]]*\]|（[^）]*）|【[^】]*】|「[^」]*」|『[^』]*』"
)

# 片假名长音记号与小书假名（媒体常归一化掉）：如「ストーリー」→「ストリー」
_DECOR_KANA_LONG = "ー"
_DECOR_SMALL_KANA = frozenset("ァィゥェォッャュョヮヵヶ")

# 数字编号：第N / S# / #N 等（区别于季数后缀，这里覆盖更宽）
_DECOR_NUMERAL_RE = re.compile(r"第\s*[0-9一二三四五六七八九十百千万]+|S\d{1,2}|#\d+")

# 年份后缀：19xx / 20xx（含括号包裹），如「Name 2021」→「Name」
_DECOR_YEAR_RE = re.compile(r"[(（]?(?:19|20)\d{2}[)）]?")


def strip_bracket_content(text: str) -> str:
    """剥除标题中的括号及其内部内容（深度归一化用）"""
    if not text:
        return text
    return _DECOR_BRACKET_RE.sub("", text)


def strip_kana_variant(text: str) -> str:
    """去掉片假名长音记号与小书假名（模拟媒体归一化）"""
    if not text:
        return text
    return "".join(
        c for c in text if c != _DECOR_KANA_LONG and c not in _DECOR_SMALL_KANA
    )


def strip_numeral_variant(text: str) -> str:
    """剥除数字编号（第N / S# / #N）"""
    if not text:
        return text
    return _DECOR_NUMERAL_RE.sub("", text)


def strip_year_suffix(text: str) -> str:
    """剥除年份后缀（19xx / 20xx）"""
    if not text:
        return text
    return _DECOR_YEAR_RE.sub("", text)


# 预测性匹配的"派生方式"标注：build_search_variants 为每个候选打标，
# 召回层（try_search）命中时把派生方式写入 ShortcutResult.match_method，
# 使后端能如实反映一次匹配到底是"精确命中"还是"靠剥离/前缀预测推导出来的"，
# 对应设计文档 §11/§13.3 的 final_match_method 预测性标记（不掩盖真实行为）。
MATCH_METHOD_EXACT = "exact"
MATCH_METHOD_FUZZY = "fuzzy"
MATCH_METHOD_PREFIX_VARIANT = "prefix_variant"
MATCH_METHOD_SEASON_STRIPPED = "season_stripped"
MATCH_METHOD_MEDIA_SUFFIX_STRIPPED = "media_suffix_stripped"
MATCH_METHOD_UNWRAPPED = "unwrapped"
MATCH_METHOD_MAIN_SEGMENT = "main_segment"


class SearchVariant(NamedTuple):
    """一个搜索候选标题及其派生方式。

    Attributes:
        query: 实际用于查询的候选标题字符串。
        method: 该候选是如何从原始标题派生出来的（见 MATCH_METHOD_* 常量），
            用于命中时标注匹配方式（后端预测性匹配落地，见 §13.3）。
    """

    query: str
    method: str


def build_search_variants(title: str, ori_title: str = "") -> list[SearchVariant]:
    """生成多策略匹配的候选查询标题（去重、保序），并为每个候选标注派生方式。

    供 Archive 短路（try_search）与 API 兜底（bgm_search）两条匹配路径
    共用，确保变体策略（剥离季后缀 / 书名号剥离 / 标题分割主段 /
    媒体前缀变体 / 媒体后缀+装饰词剥离的核心形态）永不漂移。

    顺序（优先级从高到低，严格对齐原 bgm_search 内联实现）：
    1. 原始标题（ori_title 在前，title 在后）→ method=exact
    2. 剥离季数/集数/续篇后缀（ori / title）→ method=season_stripped
    2.5 剥离媒体类型后缀 + 装饰词 → 核心标题 → method=media_suffix_stripped
    3. 书名号/方括号包裹剥离（作用于原始与剥离后）→ method=unwrapped
    4. 标题主段分割（主段长度 >= 4 才采用）→ method=main_segment
    5. 媒体前缀变体（劇場版/剧场版/映画/映画版 + 核心标题）→ method=prefix_variant

    返回的 SearchVariant.query 即原 list[str] 语义；method 供命中时标注匹配方式。
    """
    if not title:
        return []

    stripped_title = _strip_season_episode_suffix(title)
    stripped_ori = _strip_season_episode_suffix(ori_title) if ori_title else ""
    # 与评分层 _strip_media_suffix / _normalize_title_for_match 对齐的"核心形态"：
    # 评分已对「遮天动画版」「斗破苍穹 年番」给高分，召回此前却搜不到其本体，
    # 导致"评分说匹配、召回找不到"的偏移。此处补齐，使高分候选由真实召回产生。
    # （评分逻辑本身保持不变，仅召回侧对称地生成同一核心变体。）
    core_title = _strip_media_suffix(_normalize_title_for_match(title)) if title else ""
    core_ori = (
        _strip_media_suffix(_normalize_title_for_match(ori_title)) if ori_title else ""
    )

    seen_titles: set[str] = set()
    search_variants: list[SearchVariant] = []

    def add(q: str, method: str) -> None:
        if q and q.strip() and q not in seen_titles:
            seen_titles.add(q)
            search_variants.append(SearchVariant(q, method))

    # 1. 原始标题（ori 在前，title 在后）
    add(ori_title, MATCH_METHOD_EXACT)
    add(title, MATCH_METHOD_EXACT)

    # 2. 剥离季数/集数/续篇后缀
    add(stripped_ori, MATCH_METHOD_SEASON_STRIPPED)
    add(stripped_title, MATCH_METHOD_SEASON_STRIPPED)

    # 2.5 剥离媒体类型后缀 + 装饰词 → 核心标题（与评分层对齐，修复偏移）
    #     「遮天动画版」→「遮天」、「斗破苍穹 年番」→「斗破苍穹」
    add(core_ori, MATCH_METHOD_MEDIA_SUFFIX_STRIPPED)
    add(core_title, MATCH_METHOD_MEDIA_SUFFIX_STRIPPED)

    # 3. 书名号/方括号包裹剥离（作用于原始与剥离后）
    for t in (ori_title, title, stripped_ori, stripped_title):
        if t:
            unwrapped = _strip_title_wrappers(t)
            if unwrapped != t:
                add(unwrapped, MATCH_METHOD_UNWRAPPED)
                # 包裹剥离后再做媒体/装饰词剥离，确保「遮天动画版」也能落到「遮天」
                add(
                    _strip_media_suffix(_normalize_title_for_match(unwrapped)),
                    MATCH_METHOD_MEDIA_SUFFIX_STRIPPED,
                )

    # 4. 标题主段分割（主段长度 >= 4）
    split_bases: list[str] = []
    for t in (stripped_ori, stripped_title):
        if t:
            split_bases.append(t)
            unwrapped = _strip_title_wrappers(t)
            if unwrapped != t:
                split_bases.append(unwrapped)
    for t in split_bases:
        if t:
            segments = _split_title_segments(t)
            if segments and len(segments[0]) >= 4:
                add(segments[0], MATCH_METHOD_MAIN_SEGMENT)

    # 5. 媒体前缀变体（核心标题不含前缀时才拼接）
    media_prefix_bases: list[str] = []
    for base in (stripped_ori, stripped_title):
        if base:
            media_prefix_bases.append(base)
            unwrapped = _strip_title_wrappers(base)
            if unwrapped != base:
                media_prefix_bases.append(unwrapped)
            segments = _split_title_segments(unwrapped)
            if segments and len(segments[0]) >= 4:
                media_prefix_bases.append(segments[0])
    for base in media_prefix_bases:
        if base and not any(base.startswith(p) for p in _MEDIA_PREFIX_VARIANTS):
            for prefix in _MEDIA_PREFIX_VARIANTS:
                add(f"{prefix}{base}", MATCH_METHOD_PREFIX_VARIANT)

    return search_variants


# ============================================================================
# 评分层：统一相似度融合（G2）+ 统一阈值（G3）
# ----------------------------------------------------------------------------
# 原先 Archive 模糊打分（_fts_query._score_candidate）与 API 标题相似度
# （bangumi_api/search.title_diff_ratio）各自实现了一套 rapidfuzz 融合逻辑，
# 权重与防误判策略漂移。下方 fuse_title_similarity 是两者的唯一实现，
# 通过权重参数保留各路径已校准的切点，彻底消除重复代码。
#
# 归一化说明（刻意保留的路径差异，非疏漏）：
# - API 路径入参已用 _normalize_title_for_match 归一化（富修饰词/空格折叠）；
# - Archive 路径入参已用 _normalize_key 归一化（与 FTS5 索引空间对齐）。
# 融合算法本身单一，归一化差异由各自索引空间决定，不在本函数内强行统一
# （统一需重建 FTS5 索引，风险高、收益低）。
# ============================================================================

# G3：评分/阈值集中管理，消除 Archive(0~100) 与 API(0~1) 散落的魔数。
# 两者共用同一融合算法（fuse_title_similarity），切点差异反映召回角色不同：
# Archive 模糊是「最后兜底广网」（放宽到 0.8），API 主搜索是「精确命中」（严苛 0.5）。
ARCHIVE_FUZZY_THRESHOLD = 80  # Archive 模糊召回阈值（0~100，_score_candidate×100）
API_SIMILARITY_PRIMARY = 0.5  # API 主搜索严苛阈值（title_diff_ratio > 该值才接受）
API_SIMILARITY_FALLBACK = 0.3  # API 兜底放宽阈值（保留更多低相似候选供 trace 回传）


def fuse_title_similarity(
    norm_query: str,
    norm_ori: str,
    cand_name: str,
    cand_name_cn: str | None = None,
    cand_aliases: list[str] | None = None,
    *,
    partial_weight: float = 0.7,
    token_set_weight: float = 0.0,
    core_contains_weight: float = 0.9,
    media_suffix_guard: bool = True,
    substring_boost: bool = False,
) -> float:
    """标签相似度融合（0~1），Archive 模糊打分与 API title_diff_ratio 的唯一实现。

    入参 norm_query / norm_ori / cand_* 必须是已归一化字符串（调用方决定用
    _normalize_title_for_match 还是 _normalize_key，见模块顶部说明）。
    候选 (name, name_cn, aliases) 取最高分，完全匹配返回 1.0。

    权重参数保留各路径已校准的切点：
    - API 路径：partial_weight=0.7, token_set_weight=0.0,
                core_contains_weight=0.9, media_suffix_guard=True, substring_boost=False
    - Archive 路径：partial_weight=0.9, token_set_weight=0.95,
                core_contains_weight=0.0, media_suffix_guard=False, substring_boost=True
    """
    candidates: list[str] = [c for c in (cand_name, cand_name_cn) if c]
    if cand_aliases:
        candidates.extend(cand_aliases)
    if not candidates:
        return 0.0

    search_core = _strip_media_suffix(norm_query)
    search_stripped = search_core != norm_query
    ori_core = _strip_media_suffix(norm_ori) if norm_ori != norm_query else norm_query
    ori_stripped = ori_core != norm_ori

    max_ratio = 0.0
    for cand in candidates:
        if not cand:
            continue
        # 子串包含直接满分（Archive 历史行为；API 路径关闭以避免误抬升）
        if substring_boost and (norm_query in cand or cand in norm_query):
            return 1.0
        norm_cand = cand
        ratio_title = fuzz.ratio(norm_cand, norm_query) / 100.0
        ratio_ori = (
            fuzz.ratio(norm_cand, norm_ori) / 100.0 if norm_ori != norm_query else 0.0
        )
        score = max(ratio_title, ratio_ori)

        # 维度 2：核心标题包含检查（剥离媒体后缀后）
        cand_core = _strip_media_suffix(norm_cand)
        if core_contains_weight and search_stripped and len(search_core) >= 2:
            if search_core in norm_cand or norm_cand in search_core:
                score = max(score, core_contains_weight)
            elif search_core in cand_core or cand_core in search_core:
                score = max(score, core_contains_weight)
        if (
            core_contains_weight
            and ori_stripped
            and len(ori_core) >= 2
            and ori_core != norm_ori
        ):
            if ori_core in norm_cand or norm_cand in ori_core:
                score = max(score, core_contains_weight)
            elif ori_core in cand_core or cand_core in ori_core:
                score = max(score, core_contains_weight)

        # 维度 3：partial_ratio 打折（捕捉部分匹配）
        partial_title = (
            fuzz.partial_ratio(norm_cand, norm_query) / 100.0 * partial_weight
        )
        partial_ori = (
            fuzz.partial_ratio(norm_cand, norm_ori) / 100.0 * partial_weight
            if norm_ori != norm_query
            else 0.0
        )
        score = max(score, partial_title, partial_ori)

        # token_set 融合（Archive 路径保留，捕捉词序差异）
        if token_set_weight and token_set_weight > 0:
            ts = fuzz.token_set_ratio(norm_cand, norm_query) / 100.0 * token_set_weight
            score = max(score, ts)

        # 防误判：双方都含媒体后缀但核心标题不相关时，限制得分上限
        if media_suffix_guard:
            cand_stripped = cand_core != norm_cand
            if search_stripped and cand_stripped:
                core_sim = fuzz.ratio(search_core, cand_core) / 100.0
                core_related = (
                    core_sim >= 0.4
                    or search_core in cand_core
                    or cand_core in search_core
                )
                if not core_related and norm_ori != norm_query:
                    ori_core_sim = fuzz.ratio(ori_core, cand_core) / 100.0
                    core_related = (
                        ori_core_sim >= 0.4
                        or ori_core in cand_core
                        or cand_core in ori_core
                    )
                if not core_related:
                    score = min(score, 0.4)

        max_ratio = max(max_ratio, score)
        if max_ratio >= 1.0:
            return 1.0

    return max_ratio
