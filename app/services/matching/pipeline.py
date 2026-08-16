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
        try:
            for step in self._steps:
                outcome = step.execute(ctx)
                self._record_trace(ctx, step.stage, outcome)
                if outcome.is_terminal:
                    break
        finally:
            # 确保 trace 始终 finish（即使 step 抛异常也保证 finish 被调用）
            ctx.trace.finish()
        return self._build_result(ctx)

    def _record_trace(
        self, ctx: MatchContext, stage: str, outcome: StepOutcome
    ) -> None:
        """统一填充 trace（替代当前分散在 6 个点的设置）

        阶段一：仅基础填充（start_step + status + subject_id + reason + score）
        阶段三：逐步迁移各 step 后，此方法成为 trace 填充的唯一入口
        """
        trace = ctx.trace
        # trace.step.stage 优先取 outcome.stage_override（archive 短路命中时为 "archive"），
        # 否则用 step.stage。final_match_method 优先取 stage_override，再取 ctx.match_stage。
        effective_stage = outcome.stage_override or stage
        step = trace.start_step(effective_stage)
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
            # final_match_method 优先级：stage_override > ctx.match_stage > stage
            trace.final_match_method = (
                outcome.stage_override or ctx.match_stage or stage
            )
            if outcome.score is not None:
                trace.final_score = outcome.score
            # 细粒度匹配方式：APISearchStep 命中时设置 ctx.match_method_detail
            # （优先取 bgm_search step 写入的 matched_variant_method，archive 命中时为 exact）
            if ctx.match_method_detail:
                trace.final_match_method_detail = ctx.match_method_detail
        elif outcome.status == "low_confidence":
            # 低置信度沉淀：final_subject_id 保持 None，只记 score + method
            trace.final_match_method = ctx.match_stage or stage
            if outcome.score is not None:
                trace.final_score = outcome.score
            # 低置信度也记录细粒度匹配方式（供前端展示）
            if ctx.match_method_detail:
                trace.final_match_method_detail = ctx.match_method_detail

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
