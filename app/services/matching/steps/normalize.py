"""标题归一化 step（阶段三）

对应原 _find_subject_id 阶段 0：标题归一化（去除发布组/分辨率/编码等噪声）。
归一化结果供后续 api_search step 使用。
"""

from __future__ import annotations

from app.services.matching.context import MatchContext
from app.services.matching.steps.base import MatchStepBase, StepOutcome


class NormalizeStep(MatchStepBase):
    """标题归一化

    - 调用 sync_service.normalize_title 去噪
    - 结果写入 ctx.normalized_title 与 trace.normalized_title（前端详情展示）
    - 不参与命中/终止判定（非终端步骤）
    """

    stage = "normalize"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        normalized = ctx.service.normalize_title(ctx.item.title)
        ctx.normalized_title = normalized
        ctx.trace.normalized_title = normalized
        return StepOutcome(
            status="hit",
            reason=f"标题归一化：{ctx.item.title!r} → {normalized!r}",
            inputs={"title": ctx.item.title},
            outputs={"normalized_title": normalized},
        )
