r"""季/部后缀剥离的单一登记处（四套输入域，一份模式表）。

项目里有四处需要「把季标记从标题里剥掉」，它们**目的相同、输入域不同**。
此前四份模式各自住在调用方模块里，改一处、漏三处是既成事实漂移源；
本模块把四套模式并排放在一起，并共用同一套剥离流程（预归一 → 匹配 → 截断/替换
→ 最小核心长度保护）。**模式集合仍然有意不同**，差异见下表，合并任一侧都会
改变另一侧的边界行为。

| scope            | 输入                              | 剥什么                                       | 匹配方式                     | 结果                       |
|------------------|-----------------------------------|----------------------------------------------|------------------------------|----------------------------|
| ``bangumi_data`` | bangumi-data 条目标题             | 季标 + 尾部裸数字 + ASCII 罗马数字 ``II+``   | 6 条尾部锚定，逐条 ``sub``   | 全部替换为空               |
| ``caller_title`` | fongmi/媒体库推送的脏标题         | 季/集标 + ``上半``/``第N部`` + 调用方额外后缀 | 按序 ``search``，首条命中截断 | 截断；核心 <2 字则放弃该条 |
| ``tmdb_title``   | bangumi-data 的 TMDB 季标题       | 季/部/篇 + ``Part/Act/Phase`` + クール        | 单条尾部锚定                 | 删匹配 + strip             |
| ``filename``     | 文件名（集号兜底扫描前）          | 仅显式「第N季」/``Season N>1``               | 两条全文替换                 | 替换为空格                 |

**不能合并的四处差异**（改之前先读）：

1. ``bangumi_data`` 会剥**尾部任意数字**（``\s*\d+$``）与 ``II+`` —— 它服务的是
   bangumi-data 里「XX 2」「XX II」这类无「季」字但按季拆分的条目。换到
   ``caller_title`` 会把「86」「0083」这类作品编号尾数误剥。
2. ``caller_title`` 的模式带 ``.*$``（``S06E279`` 后面还有集号，必须整段截断），
   另外三套都是尾部锚定或精确替换；且它额外接受调用方传入的续篇后缀
   （``_PART_SUFFIX_PATTERNS``，见 ``bangumi_archive/_title_normalize``）。
3. ``tmdb_title`` 覆盖 ``クール``/``Part``/``Act``/``Phase`` 与 Unicode 罗马数字
   ``Ⅱ~Ⅹ``；``filename`` 只认 N>1（fongmi 方案 B：S01/无标记一律视为第一季，
   见 ``services/fongmi/client.py`` 头注）。
4. 罗马数字当前是**三套写法**：``bangumi_data`` 只认 ASCII ``II+``、
   ``caller_title`` 认 ``[IVX]{2,}``、``tmdb_title`` 只认 Unicode ``Ⅱ~Ⅹ``。
   这是既有差异，合并会改变真实标题的剥离结果，需单独评估（见
   ``.workbuddy/research/matching-pipeline/REGEX-TIER3-REVIEW.md``）。

新增/修改规则：**先在本文件对应 profile 里改**，再跑
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

# ===== bangumi_data：season>1 时把「XX 第2季 / XX Season 2 / XX 2期12話 / XX II」
# 剥回第一季核心标题，供 find_bangumi_id 重新走精确索引。 =====
_BANGUMI_DATA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*[第]?\s*\d+\s*期?[話话集]?$"),
    re.compile(r"\s*Season\s*\d+$", re.IGNORECASE),
    re.compile(r"\s*S\d+$", re.IGNORECASE),
    re.compile(r"\s*\d+$"),
    re.compile(r"\s*II+$"),
    re.compile(r"\s*[第]?\s*\d+\s*[期季]$"),
)

# ===== caller_title：fongmi/Plex/Jellyfin 等推送的「标题 S06E279」「标题 第N季」
# 「标题 第二季」等。archive 里存的是第一季本体名，剥完再查 archive。 =====
_CALLER_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
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

# ===== tmdb_title：同一 TMDB id 下多条 bangumi-data 记录时的「基标题」归一。
# 覆盖：中文简繁体数字季期部、英文 Season/S/Part/Act/Phase、Unicode 罗马数字、
# 日文クール。用单条尾部锚定模式，检出自带 ``search`` 语义。 =====
_TMDB_TITLE_PATTERN = re.compile(
    r"\s*("
    # 第X季/第X期/第X部（简繁体中文数字 + 阿拉伯数字）
    r"[第]?\s*(?:\d+|[一二三四五六七八九十]+)\s*[季期部]"
    # Season X（大小写无关）
    r"|Season\s*\d+"
    # SX / Part X / Act X / Phase X
    r"|S\d+|Part\s*\d+|Act\s*\d+|Phase\s*\d+"
    # Unicode 罗马数字 ⅡⅢⅣⅤⅥⅦⅧⅨⅩ（连续出现视为一个整体）
    r"|[ⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+"
    # (第Xクール)
    r"|\([^)]*クール[^)]*\)"
    r")\s*$",
    re.IGNORECASE,
)

# ===== filename：集号兜底扫描前剔除季标记。比标题解析窄得多：输入是文件名，
# 结尾数字多为分辨率/集号（``05 1080p.mp4``），故只认带「季/期」或 Season 字样
# 的标记，且只接受 N>1（S01/无标记一律视为第一季，fongmi 方案 B）。
# 捕获组形态同时供 ``episode_title.detect_filename_season`` 读取季号。 =====
FILENAME_SEASON_CN_RE = re.compile(r"第\s*([一二三四五六七八九十2-9])\s*[季期]")
FILENAME_SEASON_EN_RE = re.compile(r"[Ss]eason\s*0*([2-9]\d?)", re.IGNORECASE)

_FILENAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    FILENAME_SEASON_CN_RE,
    FILENAME_SEASON_EN_RE,
)


@dataclass(frozen=True)
class _Profile:
    """单套剥离流程的参数。四个字段组合已覆盖全部四套的历史行为。"""

    patterns: tuple[re.Pattern[str], ...] = ()
    # truncate：按序 search，命中即从头截断（模式自带 ``.*$`` 才能到句尾）
    # remove：对每条模式依次 sub（替换值见 replacement）
    mode: Literal["truncate", "remove"] = "truncate"
    replacement: str = ""
    # 截断后核心标题的最小长度；不足则放弃该条模式，继续尝试下一条
    min_core: int = 0
    # 输入先做 NFKC（全角 ``ｓｅａｓｏｎ ２`` → 半角）并 strip
    nfkc: bool = False
    # 无匹配时的返回值先 strip（tmdb_title 的历史语义：sub + strip）
    strip_result: bool = False


_PROFILES: dict[SeasonSuffixScope, _Profile] = {
    SCOPE_BANGUMI_DATA: _Profile(
        patterns=_BANGUMI_DATA_PATTERNS,
        mode="remove",
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

    for pattern in patterns:
        match = pattern.search(cleaned)
        if match is None:
            continue
        candidate = cleaned[: match.start()].strip()
        if len(candidate) >= profile.min_core:
            return candidate

    return cleaned.strip() if profile.strip_result else cleaned


def has_season_marker(text: str, scope: SeasonSuffixScope) -> bool:
    """是否含该 ``scope`` 认得的季标记（不剥离，只判定）。"""
    if not text:
        return False
    profile = _PROFILES[scope]
    cleaned = unicodedata.normalize("NFKC", text).strip() if profile.nfkc else text
    return any(pattern.search(cleaned) for pattern in profile.patterns)
