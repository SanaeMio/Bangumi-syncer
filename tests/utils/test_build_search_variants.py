"""build_search_variants 单元测试

验证从 bgm_search 抽取出的共享变体生成函数与重构前的
内联逻辑逐字节一致（顺序、去重、变体集均相同），且每个候选的
派生方式（method）与 build_search_variants 一致；
两条匹配路径（Archive 短路 / API 兜底）共用同一实现。
"""

from __future__ import annotations

from app.utils.bangumi_archive._title_normalize import (
    _MEDIA_PREFIX_VARIANTS,
    MATCH_METHOD_EXACT,
    MATCH_METHOD_MAIN_SEGMENT,
    MATCH_METHOD_MEDIA_SUFFIX_STRIPPED,
    MATCH_METHOD_PREFIX_VARIANT,
    MATCH_METHOD_SEASON_STRIPPED,
    MATCH_METHOD_UNWRAPPED,
    SearchVariant,
    _normalize_title_for_match,
    _split_title_segments,
    _strip_media_suffix,
    _strip_season_episode_suffix,
    _strip_title_wrappers,
    build_search_variants,
)


def _oracle(title: str, ori_title: str = "") -> list[SearchVariant]:
    """重构前 bgm_search 内联变体生成逻辑的精确复刻（oracle），携带派生方式。

    用于断言 build_search_variants 与重构前行为完全一致（含 method 标注）。
    """
    if not title:
        return []

    stripped_title = _strip_season_episode_suffix(title)
    stripped_ori = _strip_season_episode_suffix(ori_title) if ori_title else ""
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

    # 1. 原始标题
    add(ori_title, MATCH_METHOD_EXACT)
    add(title, MATCH_METHOD_EXACT)

    # 2. 剥离季数/集数/续篇后缀
    add(stripped_ori, MATCH_METHOD_SEASON_STRIPPED)
    add(stripped_title, MATCH_METHOD_SEASON_STRIPPED)

    # 2.5 媒体类型后缀 + 装饰词剥离 → 核心标题
    add(core_ori, MATCH_METHOD_MEDIA_SUFFIX_STRIPPED)
    add(core_title, MATCH_METHOD_MEDIA_SUFFIX_STRIPPED)

    # 3. 书名号/方括号包裹剥离（含包裹后再剥媒体/装饰词）
    for t in (ori_title, title, stripped_ori, stripped_title):
        if t:
            unwrapped = _strip_title_wrappers(t)
            if unwrapped != t:
                add(unwrapped, MATCH_METHOD_UNWRAPPED)
                add(
                    _strip_media_suffix(_normalize_title_for_match(unwrapped)),
                    MATCH_METHOD_MEDIA_SUFFIX_STRIPPED,
                )

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


class TestBuildSearchVariantsFaithful:
    """与重构前内联逻辑逐字节一致（含 method 标注）"""

    @staticmethod
    def _cases() -> list[tuple[str, str]]:
        return [
            ("完美世界", ""),
            ("完美世界 S06E279", ""),
            ("「君の名は。」", ""),
            ("魔法少女小圆：叛逆的物语", ""),
            ("A:副标题", ""),
            ("クドわふたー", ""),
            ("劇場版 クドわふたー", ""),
            ("测试", "测试"),
            ("中文译名", "日本語原名"),
            ("『魔法少女小圆：叛逆的物语』 S02E10", ""),
            ("『复杂标题：副标题』 第二季", "复杂原标题：副标题"),
            ("", ""),
        ]

    def test_matches_oracle_for_all_cases(self) -> None:
        for title, ori in self._cases():
            assert build_search_variants(title, ori) == _oracle(title, ori), (
                f"variant mismatch for title={title!r} ori={ori!r}"
            )

    def test_empty_title_returns_empty(self) -> None:
        assert build_search_variants("") == []
        assert build_search_variants("", "x") == []


class TestBuildSearchVariantsProperties:
    """变体生成的关键不变量"""

    def test_priority_order_simple_title(self) -> None:
        qs = [v.query for v in build_search_variants("完美世界 S06E279")]
        assert qs[0] == "完美世界 S06E279"
        assert "完美世界" in qs
        assert qs.index("完美世界") > qs.index("完美世界 S06E279")

    def test_full_sequence_order(self) -> None:
        qs = [
            v.query
            for v in build_search_variants("『魔法少女小圆：叛逆的物语』 S02E10")
        ]
        idx_original = qs.index("『魔法少女小圆：叛逆的物语』 S02E10")
        idx_stripped = qs.index("『魔法少女小圆：叛逆的物语』")
        idx_unwrapped = qs.index("魔法少女小圆：叛逆的物语")
        idx_main_seg = qs.index("魔法少女小圆")
        idx_prefix = qs.index("劇場版 魔法少女小圆")
        assert idx_original < idx_stripped < idx_unwrapped < idx_main_seg < idx_prefix

    def test_dedup(self) -> None:
        assert [v.query for v in build_search_variants("测试", "测试")].count(
            "测试"
        ) == 1

    def test_existing_prefix_not_doubled(self) -> None:
        qs = [v.query for v in build_search_variants("劇場版 クドわふたー")]
        assert "劇場版 劇場版 クドわふたー" not in qs


class TestBuildSearchVariantsMediaStripping:
    """召回补剥离：召回侧生成与评分层一致的"核心形态"变体（修复偏移）"""

    def test_media_suffix_stripped_variant_present(self) -> None:
        """「遮天动画版」应生成核心变体「遮天」，使召回能命中本体。"""
        qs = [v.query for v in build_search_variants("遮天动画版")]
        assert "遮天动画版" in qs
        assert "遮天" in qs
        # 核心变体位于原始/季剥离之后（step 2.5）
        assert qs.index("遮天") > qs.index("遮天动画版")

    def test_decorator_normalized_variant_present(self) -> None:
        """「斗破苍穹 年番」应生成核心变体「斗破苍穹」（装饰词去除）。"""
        qs = [v.query for v in build_search_variants("斗破苍穹 年番")]
        assert "斗破苍穹 年番" in qs
        assert "斗破苍穹" in qs

    def test_wrapped_media_suffix_still_stripped(self) -> None:
        """包裹「遮天动画版」时，包裹剥离后再剥媒体后缀，仍落到「遮天」。"""
        qs = [v.query for v in build_search_variants("「遮天动画版」")]
        assert "遮天" in qs

    def test_core_variant_dedup_with_season_strip(self) -> None:
        """核心变体与季剥离变体去重，不产生重复。"""
        qs = [v.query for v in build_search_variants("完美世界 S06E279")]
        assert qs.count("完美世界") == 1

    def test_scoring_recall_parity(self) -> None:
        """召回生成的变体集合包含评分层会打高分的核心形态（偏移修复的本质）。"""
        from app.utils.bangumi_archive._title_normalize import (
            _normalize_title_for_match as _ntm,
            _strip_media_suffix as _sms,
        )

        title = "遮天动画版"
        qs = [v.query for v in build_search_variants(title)]
        # 评分层对同标题会用来比对的核心形态
        scoring_core = _sms(_ntm(title))
        assert scoring_core in qs


class TestBuildSearchVariantsMethodTagging:
    """每个候选应携带正确的派生方式标注（后端预测性匹配落地）"""

    def test_method_tags_present(self) -> None:
        variants = build_search_variants("「遮天动画版」 第二季")
        methods = {v.method for v in variants}
        assert MATCH_METHOD_EXACT in methods
        assert MATCH_METHOD_SEASON_STRIPPED in methods
        assert MATCH_METHOD_MEDIA_SUFFIX_STRIPPED in methods
        assert MATCH_METHOD_UNWRAPPED in methods

    def test_core_variant_tagged_media_suffix_stripped(self) -> None:
        variants = build_search_variants("遮天动画版")
        core = [v for v in variants if v.query == "遮天"]
        assert core and core[0].method == MATCH_METHOD_MEDIA_SUFFIX_STRIPPED

    def test_prefix_variant_tagged(self) -> None:
        variants = build_search_variants("クドわふたー")
        pv = [v for v in variants if v.query == "劇場版 クドわふたー"]
        assert pv and pv[0].method == MATCH_METHOD_PREFIX_VARIANT
