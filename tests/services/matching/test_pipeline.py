"""MatchPipeline 编排器测试"""

from __future__ import annotations

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.pipeline import MatchPipeline
from app.services.matching.steps.base import MatchStepBase, StepOutcome
from app.services.sync_service.match_trace import MatchTrace


def _build_ctx() -> MatchContext:
    return MatchContext(
        item=CustomItem(
            media_type="episode",
            title="测试",
            ori_title=None,
            season=1,
            episode=1,
            release_date="",
            user_name="u",
            source="test",
        ),
        bgm=object(),
        trace=MatchTrace(),
    )


class _HitStep(MatchStepBase):
    """命中即终止的 step"""

    stage = "test_hit"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        ctx.subject_id = "111"
        ctx.match_stage = "archive"
        ctx.match_method_detail = "exact"
        return StepOutcome(
            status="hit",
            subject_id="111",
            reason="命中",
            score=1.0,
            is_terminal=True,
        )


class _MissStep(MatchStepBase):
    """未命中，继续下一步"""

    stage = "test_miss"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        return StepOutcome(status="miss", reason="未命中")


class _ErrorStep(MatchStepBase):
    """出错，终止"""

    stage = "test_error"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        return StepOutcome(
            status="error",
            reason="异常",
            error_detail={"type": "ValueError", "message": "test"},
            is_terminal=True,
        )


def test_pipeline_hit_terminates():
    """命中即终止，后续 step 不执行"""
    calls: list[str] = []

    class _RecordHit(MatchStepBase):
        stage = "archive"

        def execute(self, ctx):
            calls.append("archive")
            ctx.subject_id = "111"
            ctx.match_stage = "archive"
            ctx.match_method_detail = "exact"
            return StepOutcome(status="hit", subject_id="111", is_terminal=True)

    class _NeverCall(MatchStepBase):
        stage = "never"

        def execute(self, ctx):
            calls.append("never")
            return StepOutcome(status="hit", subject_id="222", is_terminal=True)

    pipeline = MatchPipeline([_RecordHit(), _NeverCall()])
    ctx = _build_ctx()
    result = pipeline.run(ctx)

    assert calls == ["archive"]
    assert result.subject_id == "111"
    assert ctx.trace.final_subject_id == "111"
    assert ctx.trace.final_match_method == "archive"
    assert ctx.trace.final_match_method_detail == "exact"


def test_pipeline_miss_continues():
    """miss 不终止，继续下一步"""
    calls: list[str] = []

    class _FirstMiss(MatchStepBase):
        stage = "first"

        def execute(self, ctx):
            calls.append("first")
            return StepOutcome(status="miss", reason="未命中")

    class _SecondHit(MatchStepBase):
        stage = "api_search"

        def execute(self, ctx):
            calls.append("api_search")
            ctx.subject_id = "222"
            ctx.match_stage = "api_search"
            ctx.match_method_detail = "prefix_variant"
            return StepOutcome(status="hit", subject_id="222", is_terminal=True)

    pipeline = MatchPipeline([_FirstMiss(), _SecondHit()])
    ctx = _build_ctx()
    result = pipeline.run(ctx)

    assert calls == ["first", "api_search"]
    assert result.subject_id == "222"
    assert ctx.trace.final_subject_id == "222"
    assert ctx.trace.final_match_method == "api_search"
    assert ctx.trace.final_match_method_detail == "prefix_variant"


def test_pipeline_error_terminates():
    """error 终止管道"""
    pipeline = MatchPipeline([_ErrorStep(), _HitStep()])
    ctx = _build_ctx()
    result = pipeline.run(ctx)

    assert result.subject_id is None  # error step 未设 subject_id
    # error step 的 is_terminal=True，HitStep 未执行
    assert ctx.trace.final_subject_id is None


def test_pipeline_trace_steps_recorded():
    """每个 step 都记录到 trace.steps"""
    pipeline = MatchPipeline([_MissStep(), _HitStep()])
    ctx = _build_ctx()
    pipeline.run(ctx)

    stages = [s.stage for s in ctx.trace.steps]
    assert stages == ["test_miss", "test_hit"]
    # 命中 step 的 status 为 hit
    assert ctx.trace.steps[1].status == "hit"
    assert ctx.trace.steps[1].subject_id == "111"


def test_pipeline_low_confidence_no_subject():
    """low_confidence 不设 final_subject_id，只记 score + method"""

    class _LowConfStep(MatchStepBase):
        stage = "api_search"

        def execute(self, ctx):
            ctx.match_stage = "api_search"
            return StepOutcome(
                status="low_confidence",
                score=0.3,
                reason="置信度不足",
                is_terminal=True,
            )

    pipeline = MatchPipeline([_LowConfStep()])
    ctx = _build_ctx()
    pipeline.run(ctx)

    assert ctx.trace.final_subject_id is None
    assert ctx.trace.final_match_method == "api_search"
    assert ctx.trace.final_score == 0.3


def test_pipeline_api_search_archive_shortcut_uses_match_stage():
    """api_search 步骤内 archive 短路命中时，final_match_method 取 ctx.match_stage=archive"""

    class _ApiSearchWithArchiveHit(MatchStepBase):
        stage = "api_search"

        def execute(self, ctx):
            ctx.subject_id = "333"
            # 模拟 archive 短路命中：stage 是 api_search，但 match_stage 标记为 archive
            ctx.match_stage = "archive"
            ctx.match_method_detail = "exact"
            return StepOutcome(status="hit", subject_id="333", is_terminal=True)

    pipeline = MatchPipeline([_ApiSearchWithArchiveHit()])
    ctx = _build_ctx()
    pipeline.run(ctx)

    assert ctx.trace.final_subject_id == "333"
    # 关键：stage=api_search 时使用 ctx.match_stage，而非 stage 本身
    assert ctx.trace.final_match_method == "archive"
    assert ctx.trace.final_match_method_detail == "exact"


def test_pipeline_build_result_maps_ctx():
    """_build_result 正确映射 ctx 到 MatchResult"""
    pipeline = MatchPipeline([_HitStep()])
    ctx = _build_ctx()
    result = pipeline.run(ctx)

    assert result.subject_id == "111"
    assert result.bgm_se_id is None  # episode_resolve 未执行
    assert result.trace is ctx.trace
    assert result.is_ambiguous is False
