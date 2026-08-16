"""执行阶段管道（同步详情第二段管线）

与匹配管道（app/services/matching/pipeline.py）对接：编排器先跑
MatchPipeline 得到 subject_id，再装配 SyncPipeline 顺序执行
episode_resolve → cross_season → sync_action → result。

与匹配管道的语义差异：
- 不沿用"命中即终止"：顺序执行全部 step，跳过逻辑由 step 内部 gate
  （如 CrossSeasonStep 在 bgm_ep_id 非空时返回 skipped）
- 终态由 is_terminal 表达（queued 无 result step / error 短路），
  _record_trace 只填 step 字段、不自动覆写 final_* —— final_* 由
  ResultStep 统一结算（跨季改选覆写也在其中），避免与匹配阶段
  MatchPipeline._record_trace 的覆写逻辑冲突
- total_elapsed_ms 由编排器在终态收尾时统一 finish（含 receive/result 全流程）
"""

from __future__ import annotations

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.steps.base import ExecutionStepBase


class SyncPipeline:
    """执行阶段管道编排器"""

    def __init__(self, steps: list[ExecutionStepBase]) -> None:
        self._steps = steps

    def run(self, ctx: ExecutionContext) -> tuple[str, StepOutcome] | None:
        """顺序执行 steps，返回提前终止的 (stage, outcome)；全部执行完返回 None。

        step 抛异常时记录 error step 后重抛，由编排器统一异常处理。
        """
        for step in self._steps:
            try:
                outcome = step.execute(ctx)
            except Exception as e:
                self._record_error_trace(ctx, step.stage, e)
                raise
            self._record_trace(ctx, step.stage, outcome)
            if outcome.is_terminal or outcome.status == "error":
                return step.stage, outcome
        return None

    @staticmethod
    def _record_trace(ctx: ExecutionContext, stage: str, outcome: StepOutcome) -> None:
        """填充 trace step（不更新 final_* 汇总字段，由 ResultStep 统一结算）"""
        step = ctx.trace.start_step(stage)
        step.status = outcome.status
        if outcome.subject_id:
            step.subject_id = outcome.subject_id
        if outcome.reason:
            step.reason = outcome.reason
        if outcome.score is not None:
            step.score = outcome.score
        if outcome.processed_payload:
            step.processed_payload = outcome.processed_payload
        if outcome.request_params:
            step.request_params = outcome.request_params
        if outcome.api_response_summary:
            step.api_response_summary = outcome.api_response_summary
        if outcome.error_detail:
            step.error_detail = outcome.error_detail
        ctx.trace._finish_current_step()

    @staticmethod
    def _record_error_trace(ctx: ExecutionContext, stage: str, e: Exception) -> None:
        """step 抛异常时记录 error step（status=error + error_detail）后重抛"""
        step = ctx.trace.start_step(stage)
        step.status = "error"
        step.reason = str(e)
        step.error_detail = {
            "type": type(e).__name__,
            "message": str(e),
        }
        ctx.trace._finish_current_step()
