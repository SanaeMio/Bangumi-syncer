"""_MEDIA_PREFIX_VARIANTS 前缀列表回归测试

锁定"瘦身 + 补映画"后的前缀集合，防止后续漂移回无效 leading 前缀
（OVA/OAD 等），并确保补入的 映画/映画版 真实进入候选池。

数据依据：docs/design/评分体系统一设计.md §13（Archive subject 表 56,539 条实测）。
"""

from __future__ import annotations

from app.utils.bangumi_archive._title_normalize import (
    _MEDIA_PREFIX_VARIANTS,
    MATCH_METHOD_PREFIX_VARIANT,
    build_search_variants,
)

# 经 Archive 真实数据校准后的期望集合（顺序：leading 实际出现量降序）
EXPECTED_PREFIXES = ("劇場版 ", "剧场版 ", "映画 ", "映画版 ")

# 已从列表移除的"结构性失效"leading 前缀（OVA/OAD 仅以 trailing 后缀形式存在，
# 由评分侧 _strip_media_suffix 处理，recall 侧不应再拼 leading 前缀）
REMOVED_PREFIXES = ("OVA ", "OAD ")


class TestMediaPrefixVariantsLocked:
    """前缀集合本身被精确锁定，不允许静默漂移。"""

    def test_exact_membership(self) -> None:
        assert _MEDIA_PREFIX_VARIANTS == EXPECTED_PREFIXES, (
            f"前缀集合漂移：got {_MEDIA_PREFIX_VARIANTS!r}, "
            f"expected {EXPECTED_PREFIXES!r}"
        )

    def test_length_is_four(self) -> None:
        assert len(_MEDIA_PREFIX_VARIANTS) == 4

    def test_no_dead_leading_prefix(self) -> None:
        for dead in REMOVED_PREFIXES:
            assert dead not in _MEDIA_PREFIX_VARIANTS, (
                f"已移除的无效 leading 前缀 {dead!r} 不应回到列表"
            )


class TestMediaPrefixVariantsGenerated:
    """build_search_variants 必须生成补入的 映画/映画版 变体，且不含 OVA/OAD。"""

    def test_eikga_variants_present(self) -> None:
        qs = [v.query for v in build_search_variants("名探偵コナン", "")]
        assert "映画 名探偵コナン" in qs
        assert "映画版 名探偵コナン" in qs

    def test_theater_variants_present(self) -> None:
        qs = [v.query for v in build_search_variants("クドわふたー", "")]
        assert "劇場版 クドわふたー" in qs
        assert "剧场版 クドわふたー" in qs

    def test_no_ova_oad_variants(self) -> None:
        qs = [v.query for v in build_search_variants("クドわふたー", "")]
        assert "OVA クドわふたー" not in qs
        assert "OAD クドわふたー" not in qs

    def test_prefix_count_in_pool(self) -> None:
        # 核心标题不含任何前缀时，候选池应恰好包含 4 个前缀变体
        qs = [v.query for v in build_search_variants("完美世界", "")]
        expected = {f"{p}完美世界" for p in EXPECTED_PREFIXES}
        assert expected.issubset(set(qs))

    def test_prefix_variant_tagged(self) -> None:
        # 前缀变体应携带 prefix_variant 派生方式标注（后端预测性匹配落地）
        variants = build_search_variants("クドわふたー", "")
        pv = [v for v in variants if v.query == "劇場版 クドわふたー"]
        assert pv and pv[0].method == MATCH_METHOD_PREFIX_VARIANT
