"""季后缀四套模式的单一登记处契约测试。

四套模式（bangumi_data / caller_title / tmdb_title / filename）**故意不同**，
差异本身是需求。本文件锁住两件事：

1. 每套在自己的输入域上剥离正确（防止改造时把模式搬错域）；
2. 跨域的边界差异仍然存在（防止后人「顺手统一」造成行为漂移）。

另有等价性探针（archive 56k 条标题 + 黄金集 + 组合对抗串，逐条比对改造前后）：

- ``probe_season_suffix.py``：四套模式收进本模块时的等价性；
- ``probe_season_suffix_atoms.py``：词法原子化（共享 Season N / S N / 第N季 /
  罗马数字）后，相对 HEAD 逐条零差异。
"""

from __future__ import annotations

import re
import unicodedata

import pytest

import app.utils.season_suffix as season_suffix
from app.utils.season_suffix import (
    SCOPE_BANGUMI_DATA,
    SCOPE_CALLER_TITLE,
    SCOPE_FILENAME,
    SCOPE_TMDB_TITLE,
    has_season_marker,
    strip_season_markers,
)

# ===== bangumi_data：season>1 时还原第一季核心标题 =====


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("完美世界 第2季", "完美世界"),
        ("完美世界 第2期", "完美世界"),
        # 显式季词排在数字兜底之前（修复前只会剥掉数字、残留「Season」/「S」）
        ("完美世界 Season 2", "完美世界"),
        ("完美世界 S2", "完美世界"),
        ("完美世界 SEASON2", "完美世界"),
        ("完美世界 2期12話", "完美世界"),
        ("完美世界 II", "完美世界"),
        # 尾部裸数字也是季标（bangumi-data 里「XX 2」很常见）
        ("完美世界 2", "完美世界"),
        # 纯数字尾逐层剥：先剥「 3」再剥「 2」
        ("完美世界 2 3", "完美世界"),
        ("完美世界", "完美世界"),
    ],
)
def test_bangumi_data_strips_season_suffix(title: str, expected: str) -> None:
    assert strip_season_markers(title, SCOPE_BANGUMI_DATA) == expected


def test_bangumi_data_stops_after_explicit_season_word() -> None:
    """剥掉显式季词后必须停手，否则数字兜底会吃掉标题自带的编号。

    历史实现靠「数字兜底在前」侥幸躲过；一旦把 Season/S 提前就必须显式停止，
    否则 ``机动战士敢达00 第2季`` 会被剥成 ``机动战士敢达``，精确索引直接查空。
    """
    assert (
        strip_season_markers("机动战士敢达00 第2季", SCOPE_BANGUMI_DATA)
        == "机动战士敢达00"
    )
    assert (
        strip_season_markers("华纳巨星总动员2011 第1季", SCOPE_BANGUMI_DATA)
        == "华纳巨星总动员2011"
    )
    assert strip_season_markers("特搜9 第7季", SCOPE_BANGUMI_DATA) == "特搜9"
    assert strip_season_markers("MEGAZONE23 III", SCOPE_BANGUMI_DATA) == "MEGAZONE23"
    assert (
        strip_season_markers("モブサイコ100II", SCOPE_BANGUMI_DATA) == "モブサイコ100"
    )


def test_bangumi_data_s_requires_word_boundary() -> None:
    """``S1``/``S0`` 只在词首才算季标，否则 DOLLS1 / cos0 / Genius10 会被截断。"""
    assert (
        strip_season_markers("BLOOD-CLUB DOLLS1", SCOPE_BANGUMI_DATA)
        == "BLOOD-CLUB DOLLS"
    )
    assert strip_season_markers("WATER BOYS2", SCOPE_BANGUMI_DATA) == "WATER BOYS"
    assert strip_season_markers("cos0", SCOPE_BANGUMI_DATA) == "cos"
    assert (
        strip_season_markers("OVA vs Genius10", SCOPE_BANGUMI_DATA) == "OVA vs Genius"
    )


def test_bangumi_data_retries_season_word_after_numeric_tail() -> None:
    """数字尾剥掉后露出的季词要再剥一次。

    bangumi-data 里有 ``スーパー戦隊“魂” II 2006`` 这类「季词 + 年份」条目：
    第一轮季词因为年份挡在句尾匹配不到，剥完年份必须再试一次，否则精确索引
    只能查「…“魂” II」——查不到本体。
    """
    assert (
        strip_season_markers("スーパー戦隊“魂” II 2006", SCOPE_BANGUMI_DATA)
        == "スーパー戦隊“魂”"
    )
    assert (
        strip_season_markers("スーパー戦隊“魂”III 2008", SCOPE_BANGUMI_DATA)
        == "スーパー戦隊“魂”"
    )


def test_bangumi_data_roman_tail_is_not_a_partial_roman() -> None:
    """``I{2,}`` 不能从 ``VII`` 里摘走尾部 ``II``。

    剥出半个罗马数字（``VII`` → ``V``）比不剥更糟：它是一个不存在的标题，
    精确索引必然 miss，还会让排查者以为「季标已经剥过了」。
    """
    assert (
        strip_season_markers("スーパー戦隊“魂” VII 2013", SCOPE_BANGUMI_DATA)
        == "スーパー戦隊“魂” VII"
    )
    assert (
        strip_season_markers("スーパー戦隊“魂” XII 2018", SCOPE_BANGUMI_DATA)
        == "スーパー戦隊“魂” XII"
    )


def test_bangumi_data_never_returns_empty() -> None:
    """剥成空串就保留上一步：空查询会让下游精确索引必然 miss。"""
    assert strip_season_markers("1923 Season 1", SCOPE_BANGUMI_DATA) == "1923"
    assert strip_season_markers("86", SCOPE_BANGUMI_DATA) == "86"
    assert strip_season_markers("第2季", SCOPE_BANGUMI_DATA) == "第2季"


def test_bangumi_data_strips_trailing_number_only_in_this_scope() -> None:
    """尾部裸数字与 ASCII 罗马数字只在本 scope 剥。

    这是四套模式最容易被误统一的一处：`86` 是作品名不是季标。bangumi_data
    在 season>1 时才会走这里，且后面还有精确索引兜底；其余三套不能照抄。
    """
    assert strip_season_markers("86 2", SCOPE_BANGUMI_DATA) == "86"
    assert strip_season_markers("86", SCOPE_CALLER_TITLE) == "86"
    assert strip_season_markers("86", SCOPE_TMDB_TITLE) == "86"
    # 本 scope 的罗马数字只认 ASCII ``II+``（IV/VI 不剥）；caller_title 认 [IVX]{2,}，
    # tmdb_title 只认 Unicode Ⅱ~Ⅹ。三套写法是既有差异，合并需单独评估。
    assert strip_season_markers("Star Wars II", SCOPE_BANGUMI_DATA) == "Star Wars"
    assert strip_season_markers("Star Wars IV", SCOPE_BANGUMI_DATA) == "Star Wars IV"


# ===== caller_title：调用方脏标题，截断 + 核心长度保护 =====


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("完美世界 S06E279", "完美世界"),
        ("完美世界 S1E1", "完美世界"),
        # S 与季号之间的空白：``S 06E27`` 是媒体库里的真实写法，
        # 收敛词法时换成 ``S\\d+`` 丢掉了它（旧实现是 ``S\\s*\\d+``）
        ("完美世界 S 06E27", "完美世界"),
        ("完美世界 S 06 E 27", "完美世界"),
        # 裸 S 简写（召回缺口）：媒体库常推「完美世界 S2」，archive 只有本体名
        ("完美世界 S2", "完美世界"),
        ("完美世界 S12", "完美世界"),
        ("Krypton S02", "Krypton"),
        ("Love Live! Superstar!! S2", "Love Live! Superstar!!"),
        ("完美世界 第2季", "完美世界"),
        ("完美世界 第六季", "完美世界"),
        ("凡人修仙传 第十一季", "凡人修仙传"),
        # 无「第」的中文季标：只认前置空白（词内「X季」不是季标）
        ("某剧 二季", "某剧"),
        ("春夏秋冬四季", "春夏秋冬四季"),
        ("日向坂46三期生", "日向坂46三期生"),
        ("捷克的四季", "捷克的四季"),
        ("桥 第三季", "桥 第三季"),  # 核心 1 字 < min_core，回退原样
        ("完美世界 Season 2", "完美世界"),
        ("完美世界 2nd season", "完美世界"),
        ("完美世界 上半", "完美世界"),
        ("完美世界 第2部分", "完美世界"),
        ("完美世界 第3部", "完美世界"),
        ("完美世界 v2", "完美世界"),
        ("完美世界", "完美世界"),
    ],
)
def test_caller_title_strips_season_suffix(title: str, expected: str) -> None:
    assert strip_season_markers(title, SCOPE_CALLER_TITLE) == expected


def test_caller_title_nfkc_fullwidth_season() -> None:
    """全角 `ｓｅａｓｏｎ ２` 必须先折半角，否则季后缀剥不掉、后续打分崩 0。"""
    assert (
        strip_season_markers(
            "Ｏｇｇｙ ｅｔ ｌｅｓ Ｃａｆａｒｄｓ ｓｅａｓｏｎ ２",
            SCOPE_CALLER_TITLE,
        )
        == "Oggy et les Cafards"
    )


def test_caller_title_keeps_core_shorter_than_two_chars() -> None:
    """核心不足 2 字时放弃该次剥离，避免把短标题剥成空串。"""
    assert strip_season_markers("A 第2季", SCOPE_CALLER_TITLE) == "A 第2季"
    assert strip_season_markers("第2季", SCOPE_CALLER_TITLE) == "第2季"


def test_caller_title_extra_patterns_are_appended_after_season_patterns() -> None:
    """archive 侧把续篇后缀接进同一次扫描；季模式本身不认「番外篇」。"""
    import re

    part = (re.compile(r"\s*特别篇$"),)
    assert (
        strip_season_markers("向阳素描 特别篇", SCOPE_CALLER_TITLE) == "向阳素描 特别篇"
    )
    assert (
        strip_season_markers("向阳素描 特别篇", SCOPE_CALLER_TITLE, extra_patterns=part)
        == "向阳素描"
    )


def test_caller_title_does_not_strip_bare_trailing_number() -> None:
    """`86`/年份尾数不是季标；只有 bangumi_data 那套才做裸数字兜底。"""
    assert strip_season_markers("86", SCOPE_CALLER_TITLE) == "86"
    assert strip_season_markers("某剧 2019", SCOPE_CALLER_TITLE) == "某剧 2019"


# ===== tmdb_title：TMDB 季标题归一，单条尾部锚定 =====


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("完美世界 第2季", "完美世界"),
        ("完美世界 第2部", "完美世界"),
        ("完美世界 Season 2", "完美世界"),
        ("完美世界 Part 2", "完美世界"),
        ("完美世界 Act 3", "完美世界"),
        ("完美世界 Phase 2", "完美世界"),
        ("完美世界 Ⅱ", "完美世界"),
        # Unicode 罗马数字要从 Ⅰ 起收满：此前常量从 Ⅱ 开始，漏了第一个，
        # 「烈阳天道Ⅰ」这类第一部标记没剥（语料 3 条，其余 9 条含 Ⅰ 的本来就是
        # 标题中间/带副标题，不构成尾部锚定，不受影响）
        ("完美世界 Ⅰ", "完美世界"),
        ("烈阳天道Ⅰ", "烈阳天道"),
        ("完美世界 (第2クール)", "完美世界"),
        ("完美世界", "完美世界"),
    ],
)
def test_tmdb_title_strips_season_suffix(title: str, expected: str) -> None:
    assert strip_season_markers(title, SCOPE_TMDB_TITLE) == expected


def test_tmdb_title_requires_tail_anchor() -> None:
    """出现季标后还有内容时不动 —— 这套是「基标题归一」不是「去季标」。"""
    assert (
        strip_season_markers("完美世界 第2季 剩余", SCOPE_TMDB_TITLE)
        == "完美世界 第2季 剩余"
    )


def test_tmdb_title_does_not_strip_bare_trailing_number() -> None:
    assert strip_season_markers("某剧 2019", SCOPE_TMDB_TITLE) == "某剧 2019"


def test_tmdb_title_s_requires_word_boundary() -> None:
    """``S\\d+`` 无词边界会把名称尾部的 s+数字 当季标（与 bangumi_data 同坑）。"""
    assert (
        strip_season_markers("BLOOD-CLUB DOLLS1", SCOPE_TMDB_TITLE)
        == "BLOOD-CLUB DOLLS1"
    )
    assert strip_season_markers("WATER BOYS2", SCOPE_TMDB_TITLE) == "WATER BOYS2"
    assert strip_season_markers("cos0", SCOPE_TMDB_TITLE) == "cos0"
    assert strip_season_markers("完美世界 S2", SCOPE_TMDB_TITLE) == "完美世界"


def test_tmdb_title_never_returns_empty() -> None:
    """纯季标标题保留原文：TMDB 基标题为空会污染映射。"""
    assert strip_season_markers("Season 2", SCOPE_TMDB_TITLE) == "Season 2"
    assert strip_season_markers("第2季", SCOPE_TMDB_TITLE) == "第2季"
    assert strip_season_markers("S2", SCOPE_TMDB_TITLE) == "S2"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("完美世界 第2季", True),
        ("完美世界 Part 2", True),
        ("完美世界 Ⅱ", True),
        ("完美世界 S2", True),
        ("BLOOD-CLUB DOLLS1", False),  # 词内 S1 不是季标
        ("cos0", False),
        ("完美世界 第2季 剩余", False),  # 未锚定到句尾
        ("完美世界 2019", False),
        ("完美世界", False),
        ("", False),
    ],
)
def test_has_season_marker(title: str, expected: bool) -> None:
    assert has_season_marker(title, SCOPE_TMDB_TITLE) is expected


# ===== filename：文件名集号兜底前的季标记剔除 =====


def test_filename_replaces_season_marker_with_space() -> None:
    assert (
        strip_season_markers("Show 第2季 05 1080p.mkv", SCOPE_FILENAME)
        == "Show   05 1080p.mkv"
    )


def test_filename_season_requires_word_boundary() -> None:
    """词内的 season 不是季标。

    ``Preseason2`` / ``ShowSeason2`` / ``Tenseason2`` 缺词边界时会被替换成
    ``Pre `` / ``Show `` / ``Ten ``，把作品名切一半。
    """
    for name in (
        "Preseason2 05.mkv",
        "Preseason 2 05.mkv",
        "ShowSeason2.mkv",
        "Tenseason2 01.mkv",
    ):
        assert strip_season_markers(name, SCOPE_FILENAME) == name
    # 有空白分隔的正常写法仍然剥（本 scope 只认空白，`Show_Season_2` 不剥）
    assert strip_season_markers("Show Season 2 05", SCOPE_FILENAME) == "Show   05"


def test_filename_only_accepts_season_greater_than_one() -> None:
    """方案 B：S01 / Season 1 / Season 0 一律不剥（视为第一季）。"""
    assert (
        strip_season_markers("Show Season 1 05", SCOPE_FILENAME) == "Show Season 1 05"
    )
    assert (
        strip_season_markers("Show Season 0 05", SCOPE_FILENAME) == "Show Season 0 05"
    )
    assert strip_season_markers("Show 第1季 05", SCOPE_FILENAME) == "Show 第1季 05"
    assert strip_season_markers("Show 第2季 05", SCOPE_FILENAME) == "Show   05"


# ===== 词法原子：一份词表，四套按需引用 =====


def test_season_s_patterns_all_keep_word_boundary() -> None:
    r"""凡是用到 S 简写的模式必须带词边界。

    ``S\d`` 缺 ``(?<![A-Za-z])`` 这个坑先后在 bangumi_data 与 tmdb_title 各犯
    一次（``BLOOD-CLUB DOLLS1`` 被当成 S1）。词法收敛后由 ``_SEASON_S`` 承担，
    这条断言保证没有第二处内联写法把它绕过去。
    """
    for scope, profile in season_suffix._PROFILES.items():
        patterns = (*profile.patterns, *profile.numeric_patterns)
        for pattern in patterns:
            text = pattern.pattern
            # 两种写法都要覆盖：句尾裸 S（``S\\d+``）与 SxxExx 的宽松版
            # （``S\\s*\\d+``，S 与季号之间可有空白）。
            if r"S\d" not in text and r"S\s*\d" not in text:
                continue
            assert r"(?<![A-Za-z])" in text, (scope, text)


def test_season_s_atom_is_shared_by_three_scopes() -> None:
    """S 简写在 bangumi_data / caller_title / tmdb_title 共用同一个原子。"""
    users = {
        scope
        for scope, profile in season_suffix._PROFILES.items()
        if any(
            r"(?<![A-Za-z])S\d" in pattern.pattern
            for pattern in (*profile.patterns, *profile.numeric_patterns)
        )
    }
    assert users == {SCOPE_BANGUMI_DATA, SCOPE_CALLER_TITLE, SCOPE_TMDB_TITLE}


def test_roman_atoms_are_nested_levels() -> None:
    """罗马数字三层是子集链，不是三套独立写法。

    ``II+``（= ``I{2,}``）⊂ ``[IVX]{2,}``；Unicode 层是同一批数字的另一种编码。
    这条断言把「层级」钉住：要改粒度就改这里，别再各写一份字面量。
    """
    ii = season_suffix._ROMAN_ASCII_II
    ivx = season_suffix._ROMAN_ASCII_IVX
    wide = season_suffix._ROMAN_UNICODE

    # 第 1 层 ⊂ 第 2 层
    for token in ("II", "III", "IIII"):
        assert re.fullmatch(ii, token)
        assert re.fullmatch(ivx, token)
    # 第 2 层比第 1 层宽：IV / VI / XI 只归第 2 层
    for token in ("IV", "VI", "XI", "XIX"):
        assert re.fullmatch(ivx, token)
        assert re.fullmatch(ii, token) is None
    # 第 3 层只认 Unicode（含 Ⅰ），NFKC 折叠后与 ASCII 同值
    for wide_token, ascii_token in (
        ("Ⅰ", "I"),
        ("Ⅱ", "II"),
        ("Ⅳ", "IV"),
        ("Ⅹ", "X"),
    ):
        assert re.fullmatch(wide, wide_token)
        assert unicodedata.normalize("NFKC", wide_token) == ascii_token
        assert re.fullmatch(wide, ascii_token) is None


def test_wide_atoms_derive_from_narrow_ones() -> None:
    """超集原子从子集原子的字符集派生，而不是另抄一遍。

    ``_SINGLE_NUMERAL`` ⊃ ``_CN_NUMERALS`` 的字符集、``_SEASON_UNIT_WITH_PART``
    ⊃ ``_SEASON_UNIT``、``_SEASON_EN_STEM`` ⊃ ``_SEASON_EN_WORD`` —— 三层关系
    都落在常量的拼装上，改字符集只改一处。
    """
    assert season_suffix._CN_NUMERALS == f"[{season_suffix._CN_DIGITS}]+"
    assert season_suffix._SINGLE_NUMERAL == f"[{season_suffix._CN_DIGITS}2-9]"
    assert season_suffix._SEASON_UNIT == f"[{season_suffix._SEASON_UNITS}]"
    assert season_suffix._SEASON_UNIT_WITH_PART == f"[{season_suffix._SEASON_UNITS}部]"
    # 英文季标只留 STEM 一个原子（实测把 `Season` 换成它 0 改动）
    assert season_suffix._SEASON_EN_STEM == r"Season\s*0*"
    assert season_suffix._ROMAN_ASCII_II == r"(?<![IVX])I{2,}"
    # SxxExx 的宽松版只是把 S 与季号之间的空白放开，词边界与句尾版一致
    assert season_suffix._SEASON_S_LOOSE == r"(?<![A-Za-z])S\s*\d+"
    assert season_suffix._ROMAN_ASCII_IVX == "[IVX]{2,}"
    assert season_suffix._ROMAN_UNICODE == "[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+"


@pytest.mark.parametrize(
    ("title", "scope", "expected"),
    [
        # 这些是「窄原子不能换超集」的实测反例，数字见模块头注的表
        ("DARLING in the FRANXX", SCOPE_BANGUMI_DATA, "DARLING in the FRANXX"),
        ("Henry IV", SCOPE_BANGUMI_DATA, "Henry IV"),
        ("ステラ女学院高等科C3部", SCOPE_BANGUMI_DATA, "ステラ女学院高等科C3部"),
        ("007第26部电影", SCOPE_CALLER_TITLE, "007第26部电影"),
        ("86 第三部门", SCOPE_CALLER_TITLE, "86 第三部门"),
        (
            "10 years with Hayao Miyazaki",
            SCOPE_TMDB_TITLE,
            "10 years with Hayao Miyazaki",
        ),
        ("#who am I", SCOPE_TMDB_TITLE, "#who am I"),
    ],
)
def test_narrow_atoms_are_not_replaced_by_supersets(
    title: str, scope: str, expected: str
) -> None:
    """``II+``/``[季期]``/``[Ⅱ~Ⅹ]+`` 看着是子集，但换成超集就是错剥。

    ``[IVX]{2,}`` 会吃掉 ``FRANXX`` 的 ``XX``、``Henry IV`` 的 ``IV``；
    ``[季期部]`` 会剥掉「第三部门」这种词；``IGNORECASE`` 下 ASCII 罗马字符还会
    匹配任意词尾的 i/v/x（``Miyazaki`` → ``Miyazak``）。改动前先看头注的实测表。
    """
    assert strip_season_markers(title, scope) == expected  # type: ignore[arg-type]


# 词法归属矩阵：同一行是「同一个概念」，四列是各 scope 剥完的结果。
# 这张表就是「最大差集」的可执行版本 —— 谁是公共集、谁是某个 scope 的私有差异，
# 一眼可见；改动任何一格都说明某套的词法边界动了。
@pytest.mark.parametrize(
    ("title", "bangumi_data", "caller_title", "tmdb_title"),
    [
        # Season N：四套都认（filename 另见 N>1 的用例）
        ("完美世界 Season 2", "完美世界", "完美世界", "完美世界"),
        # S N：caller 认句尾裸 S2（召回补齐）；filename 不认（文件名里 S2 太容易是别的意思）
        ("完美世界 S2", "完美世界", "完美世界", "完美世界"),
        # 第N季：三套都认
        ("完美世界 第2季", "完美世界", "完美世界", "完美世界"),
        # 裸「2期」：caller 要求「第」存在（见模块头注第 2 条）
        ("完美世界 2期", "完美世界", "完美世界 2期", "完美世界"),
        # 第N期12話 这种数字尾只有 bangumi_data 逐层剥
        ("完美世界 2期12話", "完美世界", "完美世界 2期12話", "完美世界 2期12話"),
        # 「第N部」只有 caller / tmdb 认（bangumi_data 阶段 2 只剥季/期）
        ("完美世界 第3部", "完美世界 第3部", "完美世界", "完美世界"),
        # クール 只有 tmdb 认
        (
            "完美世界 (第2クール)",
            "完美世界 (第2クール)",
            "完美世界 (第2クール)",
            "完美世界",
        ),
        # 上半/下半 只有 caller 认
        ("完美世界 上半", "完美世界 上半", "完美世界", "完美世界 上半"),
        # 罗马数字三套写法：Unicode 归 caller+tmdb，ASCII 归 bangumi+caller
        ("完美世界 Ⅱ", "完美世界 Ⅱ", "完美世界", "完美世界"),
        ("完美世界 II", "完美世界", "完美世界", "完美世界 II"),
        ("完美世界 IV", "完美世界 IV", "完美世界", "完美世界 IV"),
        # 裸数字尾只有 bangumi_data 剥（「XX 2」在 bangumi-data 里就是第 2 季）
        ("完美世界 2019", "完美世界", "完美世界 2019", "完美世界 2019"),
        # v2 版本号只有 caller 认；bangumi_data 只吃到裸数字，留个 v
        ("完美世界 v2", "完美世界 v", "完美世界", "完美世界 v2"),
        # SxxExx 只有 caller 会整段截断；bangumi_data 只剥尾部数字
        ("完美世界 S06E279", "完美世界 S06E", "完美世界", "完美世界 S06E279"),
    ],
)
def test_atom_ownership_matrix(
    title: str, bangumi_data: str, caller_title: str, tmdb_title: str
) -> None:
    assert strip_season_markers(title, SCOPE_BANGUMI_DATA) == bangumi_data
    assert strip_season_markers(title, SCOPE_CALLER_TITLE) == caller_title
    assert strip_season_markers(title, SCOPE_TMDB_TITLE) == tmdb_title


def test_caller_title_bare_chinese_season_requires_leading_space() -> None:
    """无「第」的中文季标必须前置空白，否则会把词切一半。

    中文没有词边界，「四季」「三期生」「五期生」里的「X季」是词的一部分。
    旧实现无边界时，「春夏秋冬四季」→「春夏秋冬」、「日向坂46三期生」→
    「日向坂46」，30 条语料误剥。这条钉住：宁可漏剥，不可错剥。
    """
    # 前置空白 + 中文数字 + 季/期：认
    assert strip_season_markers("某剧 二季", SCOPE_CALLER_TITLE) == "某剧"
    assert strip_season_markers("某剧 第三期", SCOPE_CALLER_TITLE) == "某剧"
    # 词内「X季」不认
    assert strip_season_markers("春夏秋冬四季", SCOPE_CALLER_TITLE) == "春夏秋冬四季"
    assert (
        strip_season_markers("日向坂46三期生", SCOPE_CALLER_TITLE) == "日向坂46三期生"
    )
    assert strip_season_markers("捷克的四季", SCOPE_CALLER_TITLE) == "捷克的四季"
    # 「第」在时不受空白约束，但核心不足 2 字仍回退原样（min_core）
    assert strip_season_markers("完美世界第2季", SCOPE_CALLER_TITLE) == "完美世界"
    assert strip_season_markers("桥第三季", SCOPE_CALLER_TITLE) == "桥第三季"


# ===== 通用契约 =====


def test_empty_input_is_returned_as_is() -> None:
    for scope in (
        SCOPE_BANGUMI_DATA,
        SCOPE_CALLER_TITLE,
        SCOPE_TMDB_TITLE,
        SCOPE_FILENAME,
    ):
        assert strip_season_markers("", scope) == ""
        assert has_season_marker("", scope) is False


def test_unknown_scope_fails_loudly() -> None:
    with pytest.raises(KeyError):
        strip_season_markers("完美世界 第2季", "no_such_scope")  # type: ignore[arg-type]
