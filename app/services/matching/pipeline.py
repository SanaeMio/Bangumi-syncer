"""匹配管道编排器

按顺序执行 steps，每步记录 trace，命中即终止。
阶段一仅建立骨架，_record_trace 在阶段三逐步迁移各 step 后成为 trace 填充的唯一入口。
"""

from __future__ import annotations

from app.services.matching.context import MatchContext
from app.services.matching.result import MatchResult
from app.services.matching.steps.base import MatchStepBase, StepOutcome


class MatchPipeline:
    """匹配管道编排器"""

    def __init__(self, steps: list[MatchStepBase]):
        self._steps = steps

    def run(self, ctx: MatchContext) -> MatchResult:
        """按顺序执行 steps，命中即终止，最终构建 MatchResult"""
        for step in self._steps:
            outcome = step.execute(ctx)
            self._record_trace(ctx, step.stage, outcome)
            if outcome.is_terminal:
                break
        return self._build_result(ctx)

    def _record_trace(
        self, ctx: MatchContext, stage: str, outcome: StepOutcome
    ) -> None:
        """统一填充 trace（替代当前分散在 6 个点的设置）

        阶段一：仅基础填充（start_step + status + subject_id + reason + score）
        阶段三：逐步迁移各 step 后，此方法成为 trace 填充的唯一入口
        """
        trace = ctx.trace
        step = trace.start_step(stage)
        step.status = outcome.status
        if outcome.subject_id:
            step.subject_id = outcome.subject_id
        if outcome.reason:
            step.reason = outcome.reason
        if outcome.score is not None:
            step.score = outcome.score
        if outcome.candidates:
            step.candidates = outcome.candidates
        if outcome.processed_payload:
            step.processed_payload = outcome.processed_payload
        if outcome.request_params:
            step.request_params = outcome.request_params
        if outcome.api_response_summary:
            step.api_response_summary = outcome.api_response_summary
        if outcome.error_detail:
            step.error_detail = outcome.error_detail
        trace._finish_current_step()

        # 命中时更新 final_*（low_confidence 不设 final_subject_id）
        if outcome.status == "hit" and outcome.subject_id:
            trace.final_subject_id = outcome.subject_id
            trace.final_match_method = (
                stage if stage != "api_search" else ctx.match_stage
            )
            if outcome.score is not None:
                trace.final_score = outcome.score
            if ctx.match_method_detail:
                trace.final_match_method_detail = ctx.match_method_detail
        elif outcome.status == "low_confidence":
            # 低置信度沉淀：final_subject_id 保持 None，只记 score + method
            trace.final_match_method = ctx.match_stage or stage
            if outcome.score is not None:
                trace.final_score = outcome.score

    def _build_result(self, ctx: MatchContext) -> MatchResult:
        """从 ctx 构建最终结果"""
        return MatchResult(
            subject_id=ctx.subject_id,
            bgm_se_id=ctx.bgm_se_id,
            bgm_ep_id=ctx.bgm_ep_id,
            bgm_title=ctx.bgm_title,
            is_season_matched_id=ctx.is_season_matched_id,
            trace=ctx.trace,
            failure_detail=ctx.failure_detail,
            is_ambiguous=ctx.is_ambiguous,
        )
