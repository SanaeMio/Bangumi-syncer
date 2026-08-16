"""bgm_search 4 个 step 单测（阶段二）"""

from __future__ import annotations

from types import SimpleNamespace

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.steps.api_search import (
    DateExactSearchStep,
    SearchFinalizeStep,
    SearchResetStep,
    VariantFallbackSearchStep,
)
from app.services.sync_service.match_trace import MatchTrace


def _build_ctx(
    title="斗破苍穹年番",
    ori_title=None,
    premiere_date="",
    is_movie=False,
    subject_types=None,
) -> MatchContext:
    bgm = SimpleNamespace(
        search=lambda **kw: [],
        title_diff_ratio=lambda *a, **kw: 0.9,
    )
    return MatchContext(
        item=CustomItem(
            media_type="movie" if is_movie else "episode",
            title=title,
            ori_title=ori_title,
            season=1,
            episode=1,
            release_date=premiere_date,
            user_name="u",
            source="test",
        ),
        bgm=bgm,
        trace=MatchTrace(),
        subject_types=subject_types,
    )


class TestSearchResetStep:
    def test_resets_ctx_state(self):
        ctx = _build_ctx()
        ctx.matched_variant_method = "prefix_variant"  # 上次搜索残留
        outcome = SearchResetStep().execute(ctx)
        assert ctx.bgm_data is None
        assert ctx.matched_variant_method == ""
        assert outcome.status == "skipped"

    def test_strips_season_episode_suffix(self):
        ctx = _build_ctx(title="完美世界 S06E279")
        SearchResetStep().execute(ctx)
        assert ctx.stripped_title == "完美世界"
        assert ctx.stripped_ori == ""

    def test_strips_ori_title(self):
        ctx = _build_ctx(title="斗破苍穹年番", ori_title="完美世界 S06E279")
        SearchResetStep().execute(ctx)
        assert ctx.stripped_title == "斗破苍穹年番"
        assert ctx.stripped_ori == "完美世界"

    def test_no_ori_title(self):
        ctx = _build_ctx(title="斗破苍穹年番", ori_title=None)
        SearchResetStep().execute(ctx)
        assert ctx.stripped_ori == ""

    def test_empty_title(self):
        ctx = _build_ctx(title="", ori_title=None)
        SearchResetStep().execute(ctx)
        assert ctx.stripped_title == ""


class TestDateExactSearchStep:
    def test_skipped_when_no_date(self):
        ctx = _build_ctx(premiere_date="")
        outcome = DateExactSearchStep().execute(ctx)
        assert outcome.status == "skipped"
        assert ctx.bgm_data is None

    def test_skipped_when_short_date(self):
        ctx = _build_ctx(premiere_date="2024")
        outcome = DateExactSearchStep().execute(ctx)
        assert outcome.status == "skipped"

    def test_hit_with_date(self):
        ctx = _build_ctx(premiere_date="2024-01-15")
        ctx.bgm.search = lambda **kw: [{"id": 1, "name": "斗破苍穹年番"}]
        outcome = DateExactSearchStep().execute(ctx)
        assert outcome.status == "hit"
        assert ctx.bgm_data[0]["id"] == 1
        assert ctx.start_date_str == "2024-01-13"
        assert ctx.end_date_str == "2024-01-17"

    def test_miss_when_search_returns_empty(self):
        ctx = _build_ctx(premiere_date="2024-01-15")
        ctx.bgm.search = lambda **kw: []
        outcome = DateExactSearchStep().execute(ctx)
        assert outcome.status == "miss"
        assert ctx.bgm_data is None or ctx.bgm_data == []

    def test_tries_ori_title_first(self):
        ctx = _build_ctx(title="中文", ori_title="original", premiere_date="2024-01-15")
        calls = []

        def fake_search(**kw):
            calls.append(kw.get("title"))
            return [{"id": 1}] if kw.get("title") == "original" else []

        ctx.bgm.search = fake_search
        DateExactSearchStep().execute(ctx)
        assert calls[0] == "original"

    def test_tries_stripped_title_variant(self):
        ctx = _build_ctx(title="完美世界 S06E279", premiere_date="2024-01-15")
        SearchResetStep().execute(ctx)  # 预计算 stripped_title
        calls = []

        def fake_search(**kw):
            calls.append(kw.get("title"))
            # 仅 stripped_title 命中
            return [{"id": 1}] if kw.get("title") == "完美世界" else []

        ctx.bgm.search = fake_search
        outcome = DateExactSearchStep().execute(ctx)
        assert outcome.status == "hit"
        assert "完美世界" in calls

    def test_movie_extends_end_date(self):
        ctx = _build_ctx(
            title="Movie Title",
            ori_title="ori",
            premiere_date="2024-01-15",
            is_movie=True,
        )
        SearchResetStep().execute(ctx)  # 预计算 stripped_title/stripped_ori
        end_dates = []

        def fake_search(**kw):
            end_dates.append(kw.get("end_date"))
            # 仅剧场版扩展窗口命中（end_date 较晚的那次）
            if kw.get("end_date") == "2024-08-02":
                return [{"id": 1}]
            return []

        ctx.bgm.search = fake_search
        outcome = DateExactSearchStep().execute(ctx)
        assert outcome.status == "hit"
        # 剧场版扩展的 end_date 应为 air_date + 200 天 = 2024-08-02
        assert "2024-08-02" in end_dates

    def test_invalid_date_falls_back(self):
        ctx = _build_ctx(premiere_date="invalid-date")
        outcome = DateExactSearchStep().execute(ctx)
        assert outcome.status == "miss"
        assert "降级" in outcome.reason


class TestVariantFallbackSearchStep:
    def test_skipped_when_exact_hit_high_confidence(self):
        ctx = _build_ctx(premiere_date="2024-01-15")
        ctx.bgm_data = [{"id": 1, "name": "斗破苍穹年番"}]
        ctx.bgm.title_diff_ratio = lambda *a, **kw: 0.95
        outcome = VariantFallbackSearchStep().execute(ctx)
        assert outcome.status == "skipped"

    def test_runs_when_exact_miss(self):
        ctx = _build_ctx(title="测试", premiere_date="")
        ctx.bgm_data = None
        ctx.bgm.search = lambda **kw: [{"id": 10, "name": "测试"}]
        ctx.bgm.title_diff_ratio = lambda *a, **kw: 0.9
        outcome = VariantFallbackSearchStep().execute(ctx)
        assert outcome.status == "hit"
        assert ctx.bgm_data[0]["id"] == 10

    def test_runs_when_low_confidence(self):
        ctx = _build_ctx(title="测试", premiere_date="")
        ctx.bgm_data = [{"id": 1, "name": "其他"}]  # 精确搜索残留（低相似度）

        # 精确搜索残留 → 0.2（低于 PRIMARY，触发兜底）
        # 兜底搜索候选 → 0.9（高于 FALLBACK，命中）
        def fake_ratio(title, ori_title, bgm_data):
            return 0.2 if bgm_data.get("id") == 1 else 0.9

        ctx.bgm.title_diff_ratio = fake_ratio
        ctx.bgm.search = lambda **kw: [{"id": 10, "name": "测试"}]
        outcome = VariantFallbackSearchStep().execute(ctx)
        assert outcome.status == "hit"

    def test_miss_when_all_variants_fail(self):
        ctx = _build_ctx(title="测试", premiere_date="")
        ctx.bgm_data = None
        ctx.bgm.search = lambda **kw: []
        outcome = VariantFallbackSearchStep().execute(ctx)
        assert outcome.status == "miss"
        assert ctx.bgm_data is None

    def test_sets_matched_variant_method_on_hit(self):
        ctx = _build_ctx(title="完美世界 S06E279", premiere_date="")
        ctx.bgm_data = None

        def fake_search(**kw):
            # 仅 stripped_title 变体命中
            if kw.get("title") == "完美世界":
                return [{"id": 1, "name": "完美世界"}]
            return []

        ctx.bgm.search = fake_search
        ctx.bgm.title_diff_ratio = lambda *a, **kw: 0.9
        VariantFallbackSearchStep().execute(ctx)
        assert ctx.matched_variant_method != ""

    def test_preserves_bgm_data_on_full_miss(self):
        """全 miss 时保留 DateExactSearchStep 的低相似度候选（P1-3 修复）

        修复前：全 miss 时 ctx.bgm_data = None，丢弃精确搜索候选，
        导致 APISearchStep 无候选可沉淀为 pending_candidate。
        修复后：全 miss 时保留 ctx.bgm_data，让 SearchFinalizeStep 返回 hit，
        APISearchStep 能拿到候选执行置信度检查并沉淀。
        """
        ctx = _build_ctx(title="测试", premiere_date="")
        ctx.bgm_data = [{"id": 1, "name": "其他"}]  # 精确搜索残留
        ctx.bgm.title_diff_ratio = lambda *a, **kw: 0.1  # 低于 PRIMARY 触发兜底
        ctx.bgm.search = lambda **kw: []  # 兜底也全 miss
        VariantFallbackSearchStep().execute(ctx)
        # 保留低相似度候选供下游沉淀
        assert ctx.bgm_data == [{"id": 1, "name": "其他"}]


class TestSearchFinalizeStep:
    def test_miss_when_no_data(self):
        ctx = _build_ctx()
        ctx.bgm_data = None
        outcome = SearchFinalizeStep().execute(ctx)
        assert outcome.status == "miss"
        assert outcome.is_terminal is True
        assert ctx.matched_variant_method == ""

    def test_miss_when_empty_list(self):
        ctx = _build_ctx()
        ctx.bgm_data = []
        outcome = SearchFinalizeStep().execute(ctx)
        assert outcome.status == "miss"
        assert outcome.is_terminal is True

    def test_hit_with_subject_id(self):
        ctx = _build_ctx()
        ctx.bgm_data = [{"id": 12345, "name": "斗破苍穹年番"}]
        outcome = SearchFinalizeStep().execute(ctx)
        assert outcome.status == "hit"
        assert outcome.is_terminal is True
        assert outcome.subject_id == "12345"

    def test_hit_does_not_clear_match_method(self):
        """命中时不清空 matched_variant_method（由 variant_fallback 设置）"""
        ctx = _build_ctx()
        ctx.matched_variant_method = "prefix_variant"
        ctx.bgm_data = [{"id": 12345, "name": "斗破苍穹年番"}]
        SearchFinalizeStep().execute(ctx)
        assert ctx.matched_variant_method == "prefix_variant"
