"""SearchResetStep / SearchFinalizeStep 单测（阶段二第一步）"""

from __future__ import annotations

from types import SimpleNamespace

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.steps.api_search import (
    SearchFinalizeStep,
    SearchResetStep,
)
from app.services.sync_service.match_trace import MatchTrace


def _build_ctx(title="斗破苍穹年番", ori_title=None) -> MatchContext:
    bgm = SimpleNamespace(last_hit_source="archive", last_match_method="exact")
    return MatchContext(
        item=CustomItem(
            media_type="episode",
            title=title,
            ori_title=ori_title,
            season=1,
            episode=1,
            release_date="",
            user_name="u",
            source="test",
        ),
        bgm=bgm,
        trace=MatchTrace(),
    )


class TestSearchResetStep:
    def test_resets_bgm_state(self):
        ctx = _build_ctx()
        outcome = SearchResetStep().execute(ctx)
        assert ctx.bgm.last_hit_source == ""
        assert ctx.bgm.last_match_method == ""
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


class TestSearchFinalizeStep:
    def test_miss_when_no_data(self):
        ctx = _build_ctx()
        ctx.bgm_data = None
        outcome = SearchFinalizeStep().execute(ctx)
        assert outcome.status == "miss"
        assert outcome.is_terminal is True
        assert ctx.bgm.last_match_method == ""
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
        ctx.bgm.last_match_method = "prefix_variant"
        ctx.bgm_data = [{"id": 12345, "name": "斗破苍穹年番"}]
        SearchFinalizeStep().execute(ctx)
        assert ctx.bgm.last_match_method == "prefix_variant"
        assert ctx.matched_variant_method == "prefix_variant"
