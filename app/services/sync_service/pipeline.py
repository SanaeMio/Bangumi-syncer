"""执行阶段管道（同步详情第二段管线）

与匹配管道（app/services/matching/pipeline.py）对接：编排器先跑
MatchPipeline 得到 subject_id，再装配 SyncPipeline 顺序执行
episode_resolve → cross_season → sync_action → result。

与匹配管道的语义差异：
- 不沿用"命中即终止"：顺序执行全部 step，跳过逻辑由 step 内部 gate
  （如 CrossSeasonStep 在 prev 已有 episode_id 时返回 skipped）
- 结果链传参：step 通过 execute(ctx, prev) 直接拿到上游产物，产出经
  outcome.outputs 由本管线 merge 进当前有效产物（同键覆盖、空值不覆盖，
  跨季改选即依赖覆盖语义），并记录到 ctx.step_outputs（线性管线每步恰好
  执行一次，重复执行时覆盖），step 不直接写 ctx 输出字段
- 终态由 is_terminal 表达（queued 无 result step / error 短路），
  _record_trace 只填 step 字段、不自动覆写 final_* —— final_* 由
  ResultStep 统一结算（跨季改选覆写也在其中），避免与匹配阶段
  MatchPipeline._record_trace 的覆写逻辑冲突
- total_elapsed_ms 由编排器在终态收尾时统一 finish（含 receive/result 全流程）
"""

from __future__ import annotations

from typing import Any

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.steps.base import ExecutionStepBase

# 结果链 merge 时跳过的空值（None / 空串），避免 miss 产物的空字段
# 覆盖上游非空产物（如 cross_season miss 不清掉 episode_resolve 的 subject_id）
_EMPTY_VALUES = (None, "")


class SyncPipeline:
    """执行阶段管道编排器"""

    def __init__(self, steps: list[ExecutionStepBase]) -> None:
        self._steps = steps

    def run(self, ctx: ExecutionContext) -> tuple[str, StepOutcome] | None:
        """顺序执行 steps，返回提前终止的 (stage, outcome)；全部执行完返回 None。

        step 抛异常时记录 error step 后重抛，由编排器统一异常处理。
        """
        current: dict[str, Any] = {}
        for step in self._steps:
            try:
                outcome = step.execute(ctx, current or None)
            except Exception as e:
                self._record_error_trace(ctx, step.stage, e)
                raise
            self._record_trace(ctx, step.stage, outcome)
            if outcome.outputs:
                current = {**current, **self._merge_outputs(outcome.outputs)}
                ctx.step_outputs[step.stage] = dict(outcome.outputs)
            if outcome.is_terminal or outcome.status == "error":
                ctx.current_outputs = current
                return step.stage, outcome
        ctx.current_outputs = current
        return None

    @staticmethod
    def _merge_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
        """过滤空值（None/空串），避免 miss 产物覆盖上游有效值"""
        return {k: v for k, v in outputs.items() if v not in _EMPTY_VALUES}

    @staticmethod
    def _record_trace(ctx: ExecutionContext, stage: str, outcome: StepOutcome) -> None:
        """填充 trace step（不更新 final_* 汇总字段，由 ResultStep 统一结算）

        step 级字段填充委托 MatchTrace.record_step（与匹配管线共用同一实现）。
        执行阶段 step 不产生 candidates，关闭以避免空列表覆盖上游候选。
        """
        ctx.trace.record_step(stage, outcome, with_candidates=False)

    @staticmethod
    def _record_error_trace(ctx: ExecutionContext, stage: str, e: Exception) -> None:
        """step 抛异常时记录 error step（status=error + error_detail）后重抛"""
        ctx.trace.record_error_step(stage, e)
