"""季后缀四套模式的单一登记处契约测试。

四套模式（bangumi_data / caller_title / tmdb_title / filename）**故意不同**，
差异本身是需求。本文件锁住两件事：

1. 每套在自己的输入域上剥离正确（防止改造时把模式搬错域）；
2. 跨域的边界差异仍然存在（防止后人「顺手统一」造成行为漂移）。

等价性另有大语料探针：
``.workbuddy/research/matching-pipeline/probe_season_suffix.py``
（archive 56k 条标题 + 黄金集 + 组合对抗串，逐条比对改造前后）。
"""

from __future__ import annotations

import pytest

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
        # 已知缺陷（保留现状，单独评估修复）：尾部裸数字模式排在 Season/S 之前，
        # 「Season 2」只剥掉数字、留下「Season」。见 REGEX-TIER3-REVIEW.md 附录。
        ("完美世界 Season 2", "完美世界 Season"),
        ("完美世界 S2", "完美世界 S"),
        ("完美世界 2期12話", "完美世界"),
        ("完美世界 II", "完美世界"),
        # 尾部裸数字也是季标（bangumi-data 里「XX 2」很常见）
        ("完美世界 2", "完美世界"),
        # 依次应用全部模式：先剥「 3」再剥「 2」
        ("完美世界 2 3", "完美世界"),
        ("完美世界", "完美世界"),
    ],
)
def test_bangumi_data_strips_season_suffix(title: str, expected: str) -> None:
    assert strip_season_markers(title, SCOPE_BANGUMI_DATA) == expected


def test_bangumi_data_strips_trailing_number_only_in_this_scope() -> None:
    """尾部裸数字与 ASCII 罗马数字只在本 scope 剥。

    这是四套模式最容易被误统一的一处：`86` 是作品名不是季标。bangumi_data
    在 season>1 时才会走这里，且后面还有精确索引兜底；其余三套不能照抄。
    """
    assert strip_season_markers("86", SCOPE_BANGUMI_DATA) == ""
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
        ("完美世界 第2季", "完美世界"),
        ("完美世界 第六季", "完美世界"),
        ("凡人修仙传 第十一季", "凡人修仙传"),
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


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("完美世界 第2季", True),
        ("完美世界 Part 2", True),
        ("完美世界 Ⅱ", True),
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
