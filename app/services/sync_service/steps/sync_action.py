"""同步动作 step：调用 _retry_mark_episode 标记剧集为看过

输入（prev 上游产物）：subject_id / episode_id（episode_resolve 或
cross_season 改选后的当前有效产物）
产出（outputs）：mark_status（0/1/2；-1=MARK_QUEUED 表示 API 不可达已入队）

queued 与认证失败均为终态（is_terminal=True）：
- queued：无 result step，由编排器走 _handle_queued 收尾
- 认证失败：由编排器直接返回 error 响应
"""

from __future__ import annotations

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.retry import MARK_QUEUED
from app.services.sync_service.steps.base import ExecutionStepBase


class SyncActionStep(ExecutionStepBase):
    """同步动作：标记剧集为看过（带重试）"""

    stage = "sync_action"

    def execute(self, ctx: ExecutionContext, prev: dict | None = None) -> StepOutcome:
        outputs = prev or {}
        bgm_se_id = str(outputs.get("subject_id") or ctx.subject_id)
        bgm_ep_id = str(outputs.get("episode_id") or "")
        inputs = {
            "subject_id": bgm_se_id,
            "episode_id": bgm_ep_id,
        }
        try:
            mark_status = ctx.service._retry_mark_episode(
                ctx.bgm,
                bgm_se_id,
                bgm_ep_id,
                queue_payload=ctx.item.model_dump(),
            )
        except ValueError as ve:
            if "认证失败" in str(ve) or "access_token" in str(ve):
                return StepOutcome(
                    status="error",
                    reason=f"认证失败: {ve}",
                    inputs=inputs,
                    outputs={"mark_status": "error", "error": str(ve)},
                    error_detail={"type": "auth_failed", "message": str(ve)},
                    is_terminal=True,
                )
            raise ve

        if mark_status == MARK_QUEUED:
            # API 不可达：已入待同步队列，等待补发调度器重放（无 result step）
            return StepOutcome(
                status="hit",
                subject_id=bgm_se_id,
                reason="API 不可达，已入待同步队列，等待补发调度器重放",
                inputs=inputs,
                outputs={
                    "mark_status": MARK_QUEUED,
                    "queued": True,
                },
                is_terminal=True,
            )

        action_label = {
            0: "已在看/看过（无变更）",
            1: "已标记为看过",
            2: "已添加收藏",
        }.get(mark_status, f"mark_status={mark_status}")
        return StepOutcome(
            status="hit",
            subject_id=bgm_se_id,
            reason=f"mark_episode_watched 返回 {mark_status}（{action_label}）",
            inputs=inputs,
            outputs={"mark_status": mark_status},
        )
