"""裁决层单元测试（P3）

覆盖门控三分支、margin 三态、跨源聚合与权重、配置解析回退。
裁决层是纯函数（只读候选与 policy），这里不依赖任何外部数据。
"""

from __future__ import annotations

import pytest

from app.services.matching.arbiter import (
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_SCORE,
    SOURCE_API_SEARCH,
    SOURCE_ARCHIVE,
    Arbiter,
    Decision,
    MatchPolicy,
    policy_from_config,
)
from app.services.sync_service.match_trace import MatchCandidate

VERDICT_AUTO = "auto_confirm"
VERDICT_REVIEW = "needs_review"
VERDICT_REJECT = "reject"


def cand(subject_id: str, score: float, source: str = SOURCE_ARCHIVE):
    return MatchCandidate(
        subject_id=subject_id,
        name=f"name-{subject_id}",
        score=score,
        source=source,
    )


class _Cfg:
    """config_manager 替身：只实现 get(section, key, fallback)"""

    def __init__(self, data: dict | None = None, explode: bool = False):
        self._data = data or {}
        self.explode = explode

    def get(self, section: str, key: str, fallback=None):
        if self.explode:
            raise RuntimeError("config backend down")
        return self._data.get((section, key), fallback)


class TestGating:
    """门控：先判分数够不够，再看领先多少"""

    def test_high_score_and_clear_margin_auto_confirms(self):
        d = Arbiter().decide([cand("1", 0.95), cand("2", 0.60)], MatchPolicy())
        assert d.verdict == VERDICT_AUTO
        assert d.accepted is True
        assert d.subject_id == "1"
        assert d.margin == pytest.approx(0.35)

    def test_score_below_min_rejects(self):
        d = Arbiter().decide([cand("1", 0.50), cand("2", 0.20)], MatchPolicy())
        assert d.verdict == VERDICT_REJECT
        assert d.accepted is False
        # reject 时仍回传 top1 供 trace 记录，但调用方不应采用
        assert d.subject_id == "1"

    def test_tied_top_marks_ambiguous(self):
        """分差 < ambiguous_margin：条目本身有歧义，不是「领先不足」"""
        d = Arbiter().decide([cand("1", 0.90), cand("2", 0.89)], MatchPolicy())
        assert d.verdict == VERDICT_REVIEW
        assert d.is_ambiguous is True
        assert "歧义" in d.reason

    def test_insufficient_margin_needs_review_without_ambiguous_flag(self):
        """分差落在 [ambiguous_margin, min_margin) 区间：领先不足但不算歧义"""
        d = Arbiter().decide([cand("1", 0.90), cand("2", 0.85)], MatchPolicy())
        assert d.verdict == VERDICT_REVIEW
        assert d.margin == pytest.approx(0.05)
        assert d.is_ambiguous is False
        assert "领先" in d.reason

    def test_margin_exactly_at_threshold_confirms(self):
        """边界：分差恰好等于 min_margin 应通过（不是 <）"""
        pol = MatchPolicy(min_margin=0.10)
        d = Arbiter().decide([cand("1", 0.90), cand("2", 0.80)], pol)
        assert d.margin == pytest.approx(0.10)
        assert d.verdict == VERDICT_AUTO

    def test_empty_candidates_rejects(self):
        d = Arbiter().decide([], MatchPolicy())
        assert d.verdict == VERDICT_REJECT
        assert d.subject_id is None
        assert d.score is None
        assert d.margin is None

    def test_candidates_without_subject_id_are_dropped(self):
        d = Arbiter().decide([cand("", 0.99), cand("2", 0.50)], MatchPolicy())
        assert d.subject_id == "2"


class TestMarginTristate:
    """margin 三态：None = 无从比较，0 = 并列，>0 = 领先

    三态不能塌缩成两态 —— 把「只有一条候选」当成「并列第一」会让
    单候选分支永远判歧义，而 archive 短路正是单候选高发区。
    """

    def test_single_candidate_margin_is_none(self):
        d = Arbiter().decide([cand("1", 0.95)], MatchPolicy())
        assert d.margin is None
        # 缺少对比信息不等于有歧义，分数达标即可采用
        assert d.verdict == VERDICT_AUTO

    def test_exact_tie_margin_is_zero(self):
        d = Arbiter().decide([cand("1", 0.90), cand("2", 0.90)], MatchPolicy())
        assert d.margin == 0.0
        assert d.is_ambiguous is True

    def test_all_zero_scores_treated_as_no_signal(self):
        """全部 0 分 = 没有打分信息，不是「并列第一」"""
        d = Arbiter().decide([cand("1", 0.0), cand("2", 0.0)], MatchPolicy())
        assert d.margin is None
        assert d.verdict == VERDICT_REJECT


class TestCrossSourceAggregation:
    """跨源聚合：同一条目被多来源命中时取 max(权重 × 分数)"""

    def test_same_subject_merged_with_all_sources(self):
        d = Arbiter().decide(
            [
                cand("1", 0.70, SOURCE_ARCHIVE),
                cand("1", 0.90, SOURCE_API_SEARCH),
                cand("2", 0.60, SOURCE_ARCHIVE),
            ],
            MatchPolicy(),
        )
        assert d.subject_id == "1"
        # 取加权后最高的一条，而非平均（平均会被低分源稀释成 0.8）
        assert d.score == pytest.approx(0.90)
        assert set(d.rankings[0].sources) == {SOURCE_ARCHIVE, SOURCE_API_SEARCH}
        assert d.rankings[0].source_count == 2

    def test_weight_changes_the_winner(self):
        """archive 权重压到 0.1 后，api_search 的候选应反超"""
        pol = MatchPolicy(
            weights={
                "archive": 0.1,
                "api_search": 1.0,
                "custom_mapping": 1.0,
                "bangumi_data": 1.0,
            },
            min_score=0.5,
            min_margin=0.0,
        )
        d = Arbiter().decide(
            [cand("1", 0.90, SOURCE_ARCHIVE), cand("2", 0.80, SOURCE_API_SEARCH)],
            pol,
        )
        assert d.subject_id == "2"
        assert d.score == pytest.approx(0.80)

    def test_unknown_source_defaults_to_weight_one(self):
        """未知来源不应因缺配置而整体失效"""
        pol = MatchPolicy(weights={"archive": 0.5}, min_score=0.5, min_margin=0.0)
        d = Arbiter().decide([cand("1", 0.90, "some_new_source")], pol)
        assert d.score == pytest.approx(0.90)

    def test_non_numeric_score_treated_as_zero(self):
        """mock / 脏数据的非数值分数不应让排序抛异常"""
        bad = MatchCandidate(subject_id="1", score=None, source=SOURCE_ARCHIVE)
        d = Arbiter().decide(
            [bad, cand("2", 0.95)], MatchPolicy(min_score=0.5, min_margin=0.0)
        )
        assert d.subject_id == "2"


class TestDecisionShape:
    def test_properties(self):
        auto = Decision(verdict=VERDICT_AUTO, subject_id="1", score=0.9)
        assert auto.accepted is True
        assert auto.needs_review is False

        review = Decision(verdict=VERDICT_REVIEW, subject_id="1", score=0.9)
        assert review.accepted is False
        assert review.needs_review is True

    def test_to_dict_is_json_friendly(self):
        d = Arbiter().decide([cand("1", 0.95), cand("2", 0.60)], MatchPolicy())
        payload = d.to_dict()
        assert payload["verdict"] == VERDICT_AUTO
        assert payload["subject_id"] == "1"
        assert isinstance(payload["score"], float)
        assert payload["rankings"][0]["subject_id"] == "1"


class TestAnchorSemantics:
    """改选优先语义：用「被采用条目」自己的分数门控

    这是裁决层接入真实管线的语义 —— post_search 改选（季度 / 媒体类型 /
    关联条目）是领域逻辑，裁决不改选，只判断改选结果够不够格。
    用候选池最高分去门控改选队首会造成「拿 A 的分数决定要不要采用 B」，
    实测后果是 min_score 门控完全失效（错命中一条拦不住，只误伤正确命中）。
    """

    def test_anchor_is_top1_matches_pure_ranking(self):
        cands = [cand("1", 0.95), cand("2", 0.60)]
        assert Arbiter().decide(cands, MatchPolicy(), "1").score == (
            Arbiter().decide(cands, MatchPolicy()).score
        )

    def test_low_score_anchor_rejected_even_if_pool_has_high_score(self):
        """候选池里有 0.97 的其他条目，也不能抬高 0.72 的 anchor 使其通过"""
        d = Arbiter().decide(
            [cand("1", 0.72, SOURCE_ARCHIVE), cand("2", 0.97, SOURCE_API_SEARCH)],
            MatchPolicy(),
            anchor_subject_id="1",
        )
        assert d.subject_id == "1"
        assert d.score == pytest.approx(0.72)
        assert d.verdict == VERDICT_REJECT

    def test_anchor_behind_best_other_yields_negative_margin(self):
        """anchor 落后于候选池最高分 → margin 为负 → 沉淀待审

        注意分数要先过 min_score（这里 0.90 ≥ 0.85），才会走到 margin 门控；
        低于 min_score 的 anchor 会先在第一步被 reject。
        """
        d = Arbiter().decide(
            [cand("1", 0.90, SOURCE_ARCHIVE), cand("2", 0.95, SOURCE_API_SEARCH)],
            MatchPolicy(),
            anchor_subject_id="1",
        )
        assert d.margin == pytest.approx(-0.05)
        assert d.verdict == VERDICT_REVIEW
        assert d.is_ambiguous is True

    def test_anchor_missing_from_pool_falls_back_to_top1(self):
        """anchor 不在候选池时退回纯排序，不至于崩掉或误拒"""
        d = Arbiter().decide(
            [cand("1", 0.95), cand("2", 0.60)],
            MatchPolicy(),
            anchor_subject_id="999",
        )
        assert d.subject_id == "1"
        assert d.verdict == VERDICT_AUTO

    def test_same_subject_merged_before_anchor_lookup(self):
        """同一条目跨源命中：聚合后 anchor 的加权分取 max(权重 × 分数)"""
        d = Arbiter().decide(
            [
                cand("1", 0.70, SOURCE_ARCHIVE),
                cand("1", 0.90, SOURCE_API_SEARCH),
                cand("2", 0.60, SOURCE_ARCHIVE),
            ],
            MatchPolicy(),
            anchor_subject_id="1",
        )
        assert d.subject_id == "1"
        assert d.score == pytest.approx(0.90)
        assert d.verdict == VERDICT_AUTO


class TestPolicyFromConfig:
    def test_defaults_when_section_missing(self):
        pol = policy_from_config(_Cfg({}))
        assert pol.enabled is False  # 默认关闭：保持改造前行为
        assert pol.min_score == DEFAULT_MIN_SCORE
        assert pol.min_margin == DEFAULT_MIN_MARGIN

    def test_reads_enabled_and_thresholds(self):
        cfg = _Cfg(
            {
                ("matching", "arbiter_enabled"): "true",
                ("matching", "min_score"): "0.7",
                ("matching", "min_margin"): "0.2",
                ("matching", "ambiguous_margin"): "0.02",
                ("matching", "weight_archive"): "0.5",
            }
        )
        pol = policy_from_config(cfg)
        assert pol.enabled is True
        assert pol.min_score == pytest.approx(0.7)
        assert pol.min_margin == pytest.approx(0.2)
        assert pol.ambiguous_margin == pytest.approx(0.02)
        assert pol.weights["archive"] == pytest.approx(0.5)

    def test_invalid_values_fall_back(self):
        """配置写错不应让匹配整体失效，也不应静默改变判定"""
        cfg = _Cfg(
            {
                ("matching", "arbiter_enabled"): "yes",
                ("matching", "min_score"): "not-a-number",
                ("matching", "weight_archive"): "abc",
            }
        )
        pol = policy_from_config(cfg)
        assert pol.enabled is True  # yes 视为真
        assert pol.min_score == DEFAULT_MIN_SCORE
        assert pol.weights["archive"] == 1.0

    def test_none_manager_returns_defaults(self):
        assert policy_from_config(None).enabled is False

    def test_backend_error_returns_defaults(self):
        """配置后端异常时降级为默认策略，而不是让匹配崩掉"""
        pol = policy_from_config(_Cfg(explode=True))
        assert pol.enabled is False
        assert pol.min_score == DEFAULT_MIN_SCORE
