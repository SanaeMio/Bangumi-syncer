r"""季/部后缀剥离的单一登记处（一份词表 + 四套匹配几何）。

项目里有四处需要「把季标记从标题里剥掉」：目的相同、输入域不同。此前四份模式
各住在调用方模块，改一处漏三处是既成事实漂移源 —— ``S\d+`` 缺词边界这个坑先后
在 ``bangumi_data`` 与 ``tmdb_title`` 各犯一次（``BLOOD-CLUB DOLLS1`` / ``cos0``）。

本模块把「能合并的」和「不能合并的」分开：

- **词法层**：``Season N`` / ``S N`` / ``第N季`` / 罗马数字等「同一个概念」只写
  一份（见「共享原子」），四套按需引用。这一层过去重复最多、也最容易漏改。
- **几何层**：每个 scope 一套匹配方式（截断 / 尾部锚定 / 全文替换 / 两段式），
  由输入域决定，**不合并**；差异写在各 scope 的拼装处，不再散落在调用方。

| scope            | 输入                       | 剥什么                                       | 匹配几何                             | 结果                         |
|------------------|----------------------------|----------------------------------------------|--------------------------------------|------------------------------|
| ``bangumi_data`` | bangumi-data 条目标题      | 显式季词 + 纯数字尾                          | 两段：季词命中即停；数字尾按序各一次，之后可再试一次季词 | 全部替换为空；剥空则保留原样 |
| ``caller_title`` | fongmi/媒体库推送的脏标题  | 季/集标 + ``上半``/``第N部`` + 调用方额外后缀 | 按序 ``search``，首条命中截断        | 截断；核心 <2 字则放弃该条   |
| ``tmdb_title``   | bangumi-data 的 TMDB 季标题 | 季/部/篇 + ``Part/Act/Phase`` + クール        | 单条尾部锚定                         | 删匹配 + strip               |
| ``filename``     | 文件名（集号兜底扫描前）   | 仅显式「第N季」/``Season N>1``               | 两条全文替换                         | 替换为空格                   |

## 词法矩阵（✓ = 该 scope 认这个概念）

| 原子                                  | bangumi_data  | caller_title        | tmdb_title  | filename              |
|---------------------------------------|:-------------:|:-------------------:|:-----------:|:---------------------:|
| ``Season N``                          | ✓（阶段 1）   | ✓ 含 ``2nd season`` | ✓           | ✓ 仅 N>1（捕获季号）  |
| ``S N``                               | ✓（阶段 1）   | ✓（句尾 + ``SxxExx`` 容空白） | ✓           | ✗                     |
| ``第N季`` / ``第N期``（阿拉伯）      | ✓（阶段 2）   | ✓                   | ✓           | ✓ 仅 N>1              |
| ``第N季`` / ``第N期``（中文数字）    | ✗（匹配层兜底） | ✓（无「第」需空白） | ✓           | ✓ 仅 N>1              |
| ``第N部``                             | ✗             | ✓（仅句尾）         | ✓（并入单位类） | ✗                 |
| ``第N期`` / ``第N話`` / ``第N集`` 尾巴 | ✓             | ✗                   | ✗           | ✗                     |
| 裸数字尾（``XX 2``）                  | ✓             | ✗                   | ✗           | ✗                     |
| ``Part/Act/Phase N``、``(第Nクール)`` | ✗             | ✗                   | ✓           | ✗                     |
| ``上/下半``、``第N部分``              | ✗             | ✓                   | ✗           | ✗                     |
| ``v2`` / ``ver.2`` / ``vol.2``        | ✗             | ✓                   | ✗           | ✗                     |
| 罗马数字（三层粒度）                  | ``II+``       | ``[IVX]{2,}``       | ``Ⅰ~Ⅹ``     | ✗                     |

## 四处「看着像冗余、其实不能合并」

1. **``caller_title`` 的中文季标是两条而不是一条。** 「有第」与「无第」是两种
   语义：``第2季`` / ``第二季``（阿拉伯或中文数字）在任何位置都算季标，
   ``二季`` 只在前面有空白时才算 —— 后者是 ``春夏秋冬四季`` 这类词内「X季」的
   唯一防线。两者不能并成一条 alternation：无「第」那支带 ``\s+`` 前置边界，
   有「第」那支不能有这个约束（``完美世界第2季`` 中间没有空格）。
2. **``第`` 是否必需。** ``bangumi_data``/``tmdb_title`` 认裸 ``2季``（条目名
   常这么写），``caller_title`` 只认 ``第2季``。放开会把 ``103期Fes×ReC``
   这类活动名截断（语料实测 151 条受影响），是错标风险，不能顺手统一。
   无「第」的中文形式（``某剧 二季``）保留，但**要求前置空白**：
   ``春夏秋冬四季``、``日向坂46三期生``、``捷克的四季`` 里的「X季」是词的一部分，
   中文没有词边界，只能靠空白挡（语料实测这类误剥 30 条）。
3. **词边界有三处，都是「宁可漏剥不可错剥」。** ``S\\d`` 必须
   ``(?<![A-Za-z])``（否则 ``DOLLS1`` / ``cos0`` 被当季标，bangumi_data 与
   tmdb_title 各犯过一次）；``filename`` 的 ``Season`` 同理（``Preseason2`` →
   ``Pre ``）；中文「X季」靠前置空白（见上一条）。三处的代价都是少剥，
   换来的是不会把作品名切一半。

4. **罗马数字三层粒度。** ``bangumi_data`` 只认 ``II+``（``I{2,}``）、
   ``caller_title`` 认 ``[IVX]{2,}``、``tmdb_title`` 认 Unicode ``Ⅰ~Ⅹ``。
   前两层是严格子集（``II+`` ⊂ ``[IVX]{2,}``），Unicode 层是同一批数字的另一种
   编码，三层只改粒度。合并成 ``[IVXⅠ~Ⅹ]{2,}`` 会让 ``Star Wars IV`` 这类
   正片名被当季标剥掉，需单独评估。

## 为什么不能直接用超集

「窄的模式本来就是超集的子集，那写超集不就覆盖了？」—— 覆盖是覆盖了，但四套的
差异恰恰是**刻意不覆盖**。把窄原子换成长超集的实测（9.4 万条语料，脚本
``.workbuddy/research/matching-pipeline/eval_season_superset.py``）：

| 替换 | 改动 | 典型伤害 |
|------|-----:|----------|
| bangumi_data ``II+`` → ``[IVX]{2,}`` | 66 | ``DARLING in the FRANXX`` → ``…FRAN``、``Henry IV`` → ``Henry`` |
| bangumi_data ``[季期]`` → ``[季期部]`` | 24 | ``ステラ女学院高等科C3部`` → ``…C``、``凡人修仙传 第3部`` 被剥 |
| caller_title ``[季期]`` → ``[季期部]`` | 212 | ``007第26部电影`` → ``007``、``86 第三部门`` → ``86`` |
| tmdb_title ``[Ⅱ~Ⅹ]+`` → ``[IVXⅡ~Ⅹ]+`` | 908 | ``IGNORECASE`` 下 ``Miyazaki`` → ``Miyazak``（尾部 i→I） |
| caller_title ``Season`` → ``Season\s*0*`` | 0 | — 这条已按超集写，只留 STEM |

规则：**只有实测 0 改动时才删窄原子**；其余窄原子是需求，注释里留着这些数字，
省得下次又有人「顺手统一」。

## ``bangumi_data`` 为什么是两段

单张按序表无论怎么排都有反例：数字尾在前会把 ``Season 2`` 剥成 ``Season``；
季词在前再跑数字尾，又会把标题自带的编号吃掉（``机动战士敢达00 第2季`` →
``机动战士敢达``）。两段语义是：**见到显式季词就整段剥掉并停止；只有纯数字尾
才逐层剥离**（``XX 2期12話`` → ``XX 2期`` → ``XX``）。任一步剥成空串都回退
上一步 —— 宁可留着季标，也不给下游一个空查询。

数字尾剥完后**允许再试一次季词**（只一次，不循环）：bangumi-data 里有
``スーパー戦隊“魂” II 2006`` 这类「季词 + 年份」的条目，年份被剥掉后 ``II`` 才
暴露到句尾。不补这一步就只能剥掉年份、留下 ``II``，精确索引查不到本体
（语料 5 条）。不循环的理由同上：循环会把标题自带的编号当成季标继续吃。

**中文数字季标（``第二季``）刻意不在这里剥。** 它在 bangumi-data 条目名里
是「条目身份的一部分」，剥掉会改变精确索引的键；对请求侧也无用 —— 剥成
基础标题后仍会被 ``_filter_candidates_by_season`` 按季号筛掉（季号来自候选
条目自身的声明，不来自查询）。这类请求由 ``MatchingMixin._fallback_to_base_entry``
在匹配层兜底：请求自带季标 + 该季条目不存在 + 日期吻合 → 落回基础条目。

新增/修改规则：**先在本文件对应 scope 里改，词法优先复用已有原子**，再跑
``tests/utils/test_season_suffix.py``；不要在调用方「顺手补一条」，那正是本模块
要消灭的漂移。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

SeasonSuffixScope = Literal["bangumi_data", "caller_title", "tmdb_title", "filename"]

SCOPE_BANGUMI_DATA: SeasonSuffixScope = "bangumi_data"
SCOPE_CALLER_TITLE: SeasonSuffixScope = "caller_title"
SCOPE_TMDB_TITLE: SeasonSuffixScope = "tmdb_title"
SCOPE_FILENAME: SeasonSuffixScope = "filename"

# ===== 共享原子（词法层）=====
# 四套的差异在「匹配几何」，词法大量重复。`Season 2`、`S2`、`第2季`、罗马数字
# 这类「同一个概念」此前四处各写一遍，`S\d+` 的词边界就漏改过两次。这里只写一份，
# 各 scope 按需引用；差异写在下方各自的拼装处。

# --- 数字类 ---
# 中文字符集只写一份：`_CN_NUMERALS`（一..十、十一..十九）与 `_SINGLE_NUMERAL`
# （单个数字）是同一个字符集的两种量词，后者另接受 1~9 的阿拉伯数字。
_CN_DIGITS = "一二三四五六七八九十"
_CN_NUMERALS = rf"[{_CN_DIGITS}]+"
_NUMERAL = rf"(?:\d+|{_CN_NUMERALS})"  # 阿拉伯或中文数字
_SINGLE_NUMERAL = rf"[{_CN_DIGITS}2-9]"  # 单个数字；filename 只认 N>1

# --- 季/期/部 单位 ---
# 带「部」的版本是 `_SEASON_UNIT` 的超集，从同一字符集派生，不再各写一遍。
_SEASON_UNITS = "季期"
_SEASON_UNIT = rf"[{_SEASON_UNITS}]"
_SEASON_UNIT_WITH_PART = rf"[{_SEASON_UNITS}部]"

# --- 英文季标：Season 2 / ｓｅａｓｏｎ ２（NFKC 后）/ Season 02 ---
# 词干带 `0*` 是为了 filename 能对 `Season 02` 捕获到 2；对其余 scope 与
# `Season\s*` 完全等价（`0*` 已被 `\d+` 覆盖）。所以这里只留 STEM 一个原子：
# 需要 `Season` 的地方直接用它，实测 0 改动
# （``.workbuddy/research/matching-pipeline/eval_season_superset.py`` 候选 5）。
_SEASON_EN_STEM = r"Season\s*0*"

# --- 简写季标：S2 / S06 ---
# 词边界必须有：否则 DOLLS1 / cos0 / Genius10 会被当成 S1/S0 剥掉。
# 这里漏过一次（tmdb_title），别再复制粘贴出第二份。
_SEASON_S = r"(?<![A-Za-z])S\d+"

# SxxExx 专用：S 与季号之间容许空白（媒体库里有 ``S 06E27`` 写法）。
# 比句尾裸 S 宽松是安全的 —— 后面必须跟 ``E\d+``，词内 ``cos0``
# 那类歧义根本不可能命中；旧实现本来就有这个容错，收敛时丢了。
_SEASON_S_LOOSE = r"(?<![A-Za-z])S\s*\d+"

# --- 集号尾巴：与季号拼成 SxxExx ---
_EPISODE_TAIL = r"\s*E\s*\d+"

# --- 罗马数字：一个概念的三层粒度，各 scope 只认自己那一层（既有差异）---
# ASCII 层是严格子集链：``II+`` ⊂ ``[IVX]{2,}``；Unicode 层是同一批数字的
# 另一种编码（``Ⅱ`` NFKC 折叠即 ``II``），三层只改粒度。
# 合并成 ``[IVXⅠ~Ⅹ]{2,}`` 实测改坏 908 条（``Miyazaki``→``Miyazak``），
# 详见头注「为什么不能直接用超集」。
# 字符集直接写在模式里：三层的字符集各只被引用一次，抽成中间常量只是转手。
# Unicode 层收满 U+2160~U+2169（``Ⅰ`` 一度漏掉，``烈阳天道Ⅰ`` 这类没剥）。
# 第 1 层的 ``(?<![IVX])`` 是「不能是更长罗马数字的尾巴」：``I{2,}$`` 会从
# ``VII`` 里摘走尾部 ``II``、留下 ``V``（``スーパー戦隊“魂” VII 2013`` 实测），
# 剥出半个罗马数字比不剥更糟。
_ROMAN_ASCII_II = r"(?<![IVX])I{2,}"  # 第 1 层（= ``II+``）：bangumi_data
_ROMAN_ASCII_IVX = r"[IVX]{2,}"  # 第 2 层：caller_title
_ROMAN_UNICODE = r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+"  # 第 3 层：tmdb_title

# ===== bangumi_data：season>1 时把「XX 第2季 / XX Season 2 / XX 2期12話 / XX II」
# 剥回第一季核心标题，供 find_bangumi_id 重新走精确索引。
#
# 阶段 1 = 显式季词，命中即整段剥掉并**停止**（否则数字兜底会继续吃掉
# 「机动战士敢达00」的 00）；阶段 2 = 纯数字尾，固定顺序各作用一次，
# 让「2期12話」这种两层尾巴逐层剥；阶段 2 改变了文本时再补一次阶段 1，
# 处理「II 2006」这类季词被年份挡住的写法。理由见头注「为什么是两段」。
_BANGUMI_DATA_SPECIFIC: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\s*{_SEASON_EN_STEM}\d+$", re.IGNORECASE),
    re.compile(rf"{_SEASON_S}$", re.IGNORECASE),
    re.compile(rf"\s*{_ROMAN_ASCII_II}$"),
)
_BANGUMI_DATA_NUMERIC: tuple[re.Pattern[str], ...] = (
    # 第2期12話 / 2期 / 第3話 / 第4集（含「話」的繁简两形）
    re.compile(r"\s*第?\s*\d+\s*期?[話话集]?$"),
    # 裸数字尾。与上一条**看似重复、其实是第二层**：模式按序各作用一次，
    # 不是重复执行同一条，所以再写一遍等价规则 = 多一层剥离机会
    # （``完美世界 2 3`` → ``完美世界 2`` → ``完美世界``）。删掉它会让
    # 「多层裸数字」只剥掉一层，别当死模式清理。
    re.compile(r"\s*\d+$"),
    re.compile(rf"\s*第?\s*\d+\s*{_SEASON_UNIT}$"),
)

# ===== caller_title：fongmi/Plex/Jellyfin 等推送的「标题 S06E279」「标题 第N季」
# 「标题 第二季」等。archive 里存的是第一季本体名，剥完再查 archive。
# 几何：按序 search，首条命中即从匹配处截断（模式自带 `.*$` 才能到句尾）。
_CALLER_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # S06E279 / S6E27 / S01E01 / S 06E27（S 与季号之间可有空白）
    re.compile(rf"\s*{_SEASON_S_LOOSE}\s*{_EPISODE_TAIL}.*$", re.IGNORECASE),
    # 裸 S2 / S06（仅句尾）：媒体库常推「完美世界 S2」，archive 里只有本体名。
    # 只认句尾 —— 句中 S2 可能是作品名的一部分，SxxExx 上面已覆盖。
    re.compile(rf"\s*{_SEASON_S}$", re.IGNORECASE),
    # 第N季 / 第N期（阿拉伯或中文数字）—— 见头注第 2 条：这里「第」必需
    re.compile(rf"\s*第\s*{_NUMERAL}\s*{_SEASON_UNIT}.*$"),
    # 「某剧 二季」这类无「第」的写法：要求前面是空白。
    # 中文没有词边界，「春夏秋冬四季」「日向坂46三期生」的「X季」是词的一部分，
    # 不设边界会把标题从词中间截断（语料实测 30 条误剥；宁可漏剥不可错剥）。
    re.compile(rf"\s+{_CN_NUMERALS}\s*{_SEASON_UNIT}.*$"),
    # Season N / season N
    re.compile(rf"\s*{_SEASON_EN_STEM}\d+.*$", re.IGNORECASE),
    # 2nd season / Nth season
    re.compile(rf"\s*\d+(?:st|nd|rd|th)?\s*{_SEASON_EN_STEM}.*$", re.IGNORECASE),
    # 上半 / 下半 / 第2部分
    re.compile(r"\s*(?:上|下)半.*$"),
    re.compile(r"\s*第\s*\d+\s*部分.*$"),
    # 第N部（仅句尾，避免误剥「第三部门」这类复合词）
    re.compile(r"\s*第\s*\d+\s*部$"),
    re.compile(rf"\s*第\s*{_CN_NUMERALS}\s*部$"),
    # 罗马数字版本号后缀（II/III/IV/VI/...，仅句尾，避免误剥「Star Wars: Episode IV」）
    re.compile(rf"\s+{_ROMAN_ASCII_IVX}$"),
    # vN / ver.N / version N / vol.N 末尾版本号
    re.compile(r"\s+(?:v|ver|version|vol)\.?\s*\d+$", re.IGNORECASE),
)

# ===== tmdb_title：同一 TMDB id 下多条 bangumi-data 记录时的「基标题」归一。
# 几何：单条尾部锚定，一处命中即截断。``has_season_marker`` 复用它。
_TMDB_TITLE_PATTERN = re.compile(
    rf"\s*("
    # 第2季 / 第二季 / 第2部 / 2部（简繁体中文数字 + 阿拉伯数字）
    rf"第?\s*{_NUMERAL}\s*{_SEASON_UNIT_WITH_PART}"
    # Season N
    rf"|{_SEASON_EN_STEM}\d+"
    # S2（词边界）/ Part N / Act N / Phase N
    rf"|{_SEASON_S}|Part\s*\d+|Act\s*\d+|Phase\s*\d+"
    # Unicode 罗马数字 ⅡⅢⅣⅤⅥⅦⅧⅨⅩ（连续出现视为一个整体）
    rf"|{_ROMAN_UNICODE}"
    # (第Nクール)
    rf"|\([^)]*クール[^)]*\)"
    rf")\s*$",
    re.IGNORECASE,
)

# ===== filename：集号兜底扫描前剔除季标记。比标题解析窄得多：输入是文件名，
# 结尾数字多为分辨率/集号（``05 1080p.mp4``），故只认带「季/期」或 Season 字样
# 的标记，且只接受 N>1（S01/无标记一律视为第一季，fongmi 方案 B）。
# 捕获组形态同时供 ``episode_title.detect_filename_season`` 读取季号。
# Season 前必须有词边界：``Preseason2`` / ``ShowSeason2`` / ``Tenseason2`` 的
# 「season」是词的一部分，不设边界会替换成 ``Pre `` / ``Show `` / ``Ten ``。
FILENAME_SEASON_CN_RE = re.compile(rf"第\s*({_SINGLE_NUMERAL})\s*{_SEASON_UNIT}")
FILENAME_SEASON_EN_RE = re.compile(
    rf"(?<![A-Za-z]){_SEASON_EN_STEM}([2-9]\d?)", re.IGNORECASE
)

_FILENAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    FILENAME_SEASON_CN_RE,
    FILENAME_SEASON_EN_RE,
)


@dataclass(frozen=True)
class _Profile:
    """单套剥离流程的参数。字段组合已覆盖全部四套的历史行为与修复语义。"""

    patterns: tuple[re.Pattern[str], ...] = ()
    # truncate：按序 search，命中即从头截断（模式自带 ``.*$`` 才能到句尾）
    # remove：对每条模式依次 sub（替换值见 replacement）
    # staged：阶段 1 = patterns（命中即剥并停止）；阶段 2 = numeric_patterns
    mode: Literal["truncate", "remove", "staged"] = "truncate"
    replacement: str = ""
    # 截断后核心标题的最小长度；不足则放弃该条模式，继续尝试下一条
    min_core: int = 0
    # 输入先做 NFKC（全角 ``ｓｅａｓｏｎ ２`` → 半角）并 strip
    nfkc: bool = False
    # 无匹配时的返回值先 strip（tmdb_title 的历史语义：sub + strip）
    strip_result: bool = False
    # staged 专用：纯数字尾模式，按顺序各作用一次（剥空则回退上一步）
    numeric_patterns: tuple[re.Pattern[str], ...] = ()


_PROFILES: dict[SeasonSuffixScope, _Profile] = {
    SCOPE_BANGUMI_DATA: _Profile(
        patterns=_BANGUMI_DATA_SPECIFIC,
        mode="staged",
        numeric_patterns=_BANGUMI_DATA_NUMERIC,
    ),
    SCOPE_CALLER_TITLE: _Profile(
        patterns=_CALLER_TITLE_PATTERNS,
        mode="truncate",
        min_core=2,
        nfkc=True,
    ),
    SCOPE_TMDB_TITLE: _Profile(
        patterns=(_TMDB_TITLE_PATTERN,),
        mode="truncate",
        # 剥成空串时回退原文：TMDB 基标题不能变成空串（"Season 2" 这类纯季标）
        min_core=1,
        strip_result=True,
    ),
    SCOPE_FILENAME: _Profile(
        patterns=_FILENAME_PATTERNS,
        mode="remove",
        replacement=" ",
    ),
}


def strip_season_markers(
    text: str,
    scope: SeasonSuffixScope,
    *,
    extra_patterns: tuple[re.Pattern[str], ...] = (),
) -> str:
    """按 ``scope`` 的规则剥离季标记，返回处理后文本。

    ``extra_patterns`` 追加在该 profile 的模式之后（archive 路径用它把
    ``_PART_SUFFIX_PATTERNS`` 接进同一次截断扫描，保持原有的优先级顺序）。
    空输入原样返回；无匹配时 ``caller_title`` 返回 NFKC+strip 后的文本，
    ``tmdb_title`` 返回 strip 后的文本，其余返回原文本。
    ``bangumi_data`` 走两段式（见 :func:`_strip_staged`），且任何一步剥成空串
    都保留上一步结果 —— 宁可留着季标，也不给下游一个空查询。
    """
    if not text:
        return text

    profile = _PROFILES[scope]
    cleaned = unicodedata.normalize("NFKC", text).strip() if profile.nfkc else text
    patterns = profile.patterns + tuple(extra_patterns)

    if profile.mode == "remove":
        for pattern in patterns:
            cleaned = pattern.sub(profile.replacement, cleaned)
        return cleaned

    if profile.mode == "staged":
        return _strip_staged(cleaned, patterns, profile.numeric_patterns)

    for pattern in patterns:
        match = pattern.search(cleaned)
        if match is None:
            continue
        candidate = cleaned[: match.start()].strip()
        if len(candidate) >= profile.min_core:
            return candidate

    return cleaned.strip() if profile.strip_result else cleaned


def _strip_staged(
    text: str,
    specific: tuple[re.Pattern[str], ...],
    numeric: tuple[re.Pattern[str], ...],
) -> str:
    """两段式剥离：显式季词命中即停，纯数字尾逐层剥离。

    - 阶段 1 命中后**不再进入阶段 2**：``机动战士敢达00 第2季`` 剥掉「第2季」后
      停在 ``机动战士敢达00``，不会被裸数字规则继续吃掉 ``00``。
    - 阶段 2 每条模式各作用一次（固定顺序），既能让 ``2期12話`` 逐层剥成
      ``XX``，又不会在剥完「第N季」后再吃标题自带年份（``华纳巨星总动员2011``）。
    - 阶段 2 改变了文本时再试一次阶段 1（只一次）：``XX II 2006`` → ``XX II``
      → ``XX``。不循环，否则等于「反复剥」，会吃掉标题自带编号。
    - 任一阶段剥成空串都回退到上一步（``1923 Season 1`` → ``1923`` 而非 ``""``）。
    """
    stripped = _strip_specific(text, specific)
    if stripped:
        return stripped

    out = text
    for pattern in numeric:
        candidate = pattern.sub("", out)
        if candidate.strip():
            out = candidate

    # 纯数字尾剥掉后可能露出新的显式季词：``XX II 2006`` → ``XX II`` → ``XX``。
    # 只补这一次 —— 再循环下去就是「反复剥」，会把标题自带的编号也吃掉。
    if out != text:
        stripped = _strip_specific(out, specific)
        if stripped:
            return stripped
    return out


def _strip_specific(text: str, specific: tuple[re.Pattern[str], ...]) -> str:
    """按序剥掉第一条命中的显式季词；无命中（或剥成空串）返回 ``""``。"""
    for pattern in specific:
        if pattern.search(text) is None:
            continue
        # S 简写的词边界让匹配从 S 起算、不吃前面的空格，这里补一次 strip
        candidate = pattern.sub("", text).strip()
        if candidate:
            return candidate
    return ""


def has_season_marker(text: str, scope: SeasonSuffixScope) -> bool:
    """是否含该 ``scope`` 认得的季标记（不剥离，只判定）。"""
    if not text:
        return False
    profile = _PROFILES[scope]
    cleaned = unicodedata.normalize("NFKC", text).strip() if profile.nfkc else text
    patterns = profile.patterns + profile.numeric_patterns
    return any(pattern.search(cleaned) for pattern in patterns)
