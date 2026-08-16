"""自定义映射 step（阶段三）

对应原 _find_subject_id 阶段 1：自定义映射（含季度感知 + 正则规则）。
命中即终止，match_method=custom_mapping。
"""

from __future__ import annotations

from app.services.matching.context import MatchContext
from app.services.matching.steps.base import MatchStepBase, StepOutcome


class CustomMappingStep(MatchStepBase):
    """自定义映射查找

    - 调用 mapping_service.find_mapping
    - 命中时设置 ctx.subject_id + ctx.match_stage=custom_mapping，终止管道
    - 未命中继续下一步
    """

    stage = "custom_mapping"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        from app.services.mapping_service import mapping_service

        mapping_subject_id, match_type, match_reason = mapping_service.find_mapping(
            title=ctx.item.title,
            ori_title=ctx.item.ori_title or "",
            season=ctx.item.season,
        )

        mapping_inputs = {
            "title": ctx.item.title,
            "ori_title": ctx.item.ori_title or "",
            "season": ctx.item.season,
        }

        if mapping_subject_id:
            ctx.subject_id = mapping_subject_id
            ctx.match_stage = "custom_mapping"
            ctx.is_season_matched_id = False  # 自定义映射不视为特定季度ID
            return StepOutcome(
                status="hit",
                subject_id=mapping_subject_id,
                reason=match_reason,
                score=1.0,
                inputs=mapping_inputs,
                outputs={
                    "subject_id": mapping_subject_id,
                    "match_method": match_type or "",
                    "match_reason": match_reason,
                },
                is_terminal=True,
            )

        return StepOutcome(
            status="miss", reason="自定义映射与正则规则均未命中", inputs=mapping_inputs
        )
