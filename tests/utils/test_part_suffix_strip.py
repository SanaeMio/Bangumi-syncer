"""续篇 / 外传 / 特别篇 等 side-story 维度尾部后缀剥离的回归测试。

源自 docs/design/评分体系统一设计.md §14 的 Archive 真实数据调研：原
`_strip_season_episode_suffix` 只覆盖季数/集数，未覆盖续篇维度，导致
「向阳素描 特别篇」永远回退不到「向阳素描」。2026-08-15 将 `_strip_part_suffix`
合并进 `_strip_season_episode_suffix`（单循环遍历 `_SEASON_EPISODE_PATTERNS` +
`_PART_SUFFIX_PATTERNS`），本文件锁住：

- `_strip_season_episode_suffix` 对各类尾部标记（季/续篇、JP/CN、简/繁、带/不带空格）的剥离行为
- `build_search_variants` 生成的候选池包含 part 剥离后的核心标题
- 长度保护（核心 < 2 不剥）与已知弱误报（武林外传 → 武林，召回层无害）
"""

from app.utils.bangumi_archive._title_normalize import (
    _PART_SUFFIX_PATTERNS,
    _strip_season_episode_suffix,
    build_search_variants,
)


def test_part_suffix_patterns_tuple_nonempty_and_anchored():
    # 至少覆盖调研确认的真实维度；末尾锚定 $，避免误剥中间出现
    assert len(_PART_SUFFIX_PATTERNS) >= 15
    # Part 模式应忽略大小写且允许结尾数字

    part_pat = next(p for p in _PART_SUFFIX_PATTERNS if "Part" in p.pattern)
    assert part_pat.search("Harry Potter and the Deathly Hallows: Part 2")
    assert part_pat.search("都市传说之女 part2")


def test_strip_part_suffix_cn_special():
    # 特别篇（CN 侧 208 条，修复「向阳素描 特别篇」回退）
    assert _strip_season_episode_suffix("向阳素描 特别篇") == "向阳素描"
    assert _strip_season_episode_suffix("向阳素描特别篇") == "向阳素描"
    # 外传 / 番外篇 / 前传 / 续集（带与不带空格）
    assert _strip_season_episode_suffix("爱情公寓外传") == "爱情公寓"
    assert _strip_season_episode_suffix("DARKER THAN BLACK -黑之契约者- 外传") == (
        "DARKER THAN BLACK -黑之契约者-"
    )
    assert _strip_season_episode_suffix("一人之下 the outcast 2 番外篇") == (
        "一人之下 the outcast 2"
    )
    assert _strip_season_episode_suffix("航海王：强者天下 前传") == "航海王：强者天下"
    assert _strip_season_episode_suffix("西游记续集") == "西游记"
    assert _strip_season_episode_suffix("我的青春恋爱物语果然有问题 续") == (
        "我的青春恋爱物语果然有问题"
    )


def test_strip_part_suffix_cn_traditional():
    assert _strip_season_episode_suffix("飛狐外傳") == "飛狐"
    assert _strip_season_episode_suffix("明日戰記 前傳") == "明日戰記"
    assert _strip_season_episode_suffix("上海灘續集") == "上海灘"
    assert _strip_season_episode_suffix("萌學園外傳") == "萌學園"


def test_strip_part_suffix_jp_final_chapter_and_sequel():
    # JP 完結編 / 前編 / 後編 / 続編 / Part
    assert _strip_season_episode_suffix("犬夜叉 完結編") == "犬夜叉"
    assert _strip_season_episode_suffix("めぞん一刻 完結篇") == "めぞん一刻"
    assert _strip_season_episode_suffix("3月のライオン 前編") == "3月のライオン"
    assert _strip_season_episode_suffix("64 ロクヨン 後編") == "64 ロクヨン"
    assert (
        _strip_season_episode_suffix("古畑任三郎 VS SMAP 続編") == "古畑任三郎 VS SMAP"
    )
    assert _strip_season_episode_suffix(
        "Harry Potter and the Deathly Hallows: Part 2"
    ) == ("Harry Potter and the Deathly Hallows:")


def test_strip_no_match_unchanged():
    # 无季/续篇后缀的标题原样返回
    assert _strip_season_episode_suffix("完美世界") == "完美世界"
    assert _strip_season_episode_suffix("") == ""
    # 注：「凡人修仙传 第十期」属季数维度（第N期），合并后由
    # `_SEASON_EPISODE_PATTERNS` 正确剥离为「凡人修仙传」，见下条 season 断言。


def test_season_strip_still_preferred():
    # 季数维度仍优先且互不干扰（合并后同一循环、季模式在前）
    assert _strip_season_episode_suffix("完美世界 第六季") == "完美世界"
    assert _strip_season_episode_suffix("凡人修仙传 第十期") == "凡人修仙传"
    assert _strip_season_episode_suffix("完美世界") == "完美世界"


def test_strip_part_suffix_length_guard():
    # 核心 < 2 字不剥（避免「续」这类单字标题被剥空）
    assert _strip_season_episode_suffix("续") == "续"
    # 仅当剥离后核心 >= 2 字才生效；单字核心应保留原样
    assert _strip_season_episode_suffix("X续") == "X续"
    # 双字以上核心正常剥离
    assert _strip_season_episode_suffix("AB续") == "AB"


def test_known_weak_false_positive_wulin():
    # 已知弱误报：武林外传 是独立标题，剥外传 → 武林。
    # 召回层无害（精确「武林外传」仍优先返回，此处仅记录剥离行为稳定）。
    assert _strip_season_episode_suffix("武林外传") == "武林"


def test_build_search_variants_includes_part_core():
    # 候选池应包含 part 剥离后的核心标题，使 archive/api 两条路径都能命中本体
    qs = [v.query for v in build_search_variants("向阳素描 特别篇")]
    assert "向阳素描" in qs
    # 原始标题也在池中（去重后顺序：原始优先），且携带派生方式标注
    variants = build_search_variants("向阳素描 特别篇")
    assert variants[0].query == "向阳素描 特别篇"
