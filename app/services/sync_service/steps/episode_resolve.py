"""集数解析 step：调用 _resolve_season_episode 解析季度与集数 ID

输入（ctx）：subject_id / is_season_matched_id / item（匹配阶段产物）
产出（outputs）：subject_id / episode_id / changed / subject_url / episode_url
"""

from __future__ import annotations

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.steps.base import ExecutionStepBase


class EpisodeResolveStep(ExecutionStepBase):
    """集数解析：根据 media_type 解析 Bangumi 季度与集数 ID"""

    stage = "episode_resolve"

    def execute(self, ctx: ExecutionContext, prev: dict | None = None) -> StepOutcome:
        inputs = {
            "subject_id": str(ctx.subject_id),
            "is_season_id": bool(ctx.is_season_matched_id),
            "season": ctx.item.season,
            "episode": ctx.item.episode,
            "media_type": ctx.item.media_type,
            "release_date": ctx.item.release_date or "",
        }

        try:
            bgm_se_id, bgm_ep_id = ctx.service._resolve_season_episode(
                ctx.bgm, ctx.item, ctx.subject_id, ctx.is_season_matched_id
            )
        except ValueError as ve:
            if "认证失败" in str(ve) or "access_token" in str(ve):
                return StepOutcome(
                    status="error",
                    reason=f"认证失败: {ve}",
                    inputs=inputs,
                    outputs={
                        "subject_id": "",
                        "episode_id": "",
                        "changed": False,
                        "error": str(ve),
                    },
                    error_detail={"type": "auth_failed", "message": str(ve)},
                    is_terminal=True,
                )
            raise ve

        changed = str(bgm_se_id) != str(ctx.subject_id) if bgm_se_id else False

        if bgm_ep_id:
            return StepOutcome(
                status="hit",
                subject_id=str(bgm_se_id),
                reason=(
                    f"集数解析：subject={bgm_se_id} episode={ctx.item.episode} → "
                    f"ep_id={bgm_ep_id}"
                ),
                inputs=inputs,
                outputs={
                    "subject_id": str(bgm_se_id),
                    "episode_id": str(bgm_ep_id),
                    "changed": changed,
                    "subject_url": f"https://bgm.tv/subject/{bgm_se_id}",
                    "episode_url": f"https://bgm.tv/ep/{bgm_ep_id}",
                },
            )

        return StepOutcome(
            status="miss",
            subject_id=str(bgm_se_id) if bgm_se_id else None,
            reason=f"集数解析未命中：subject={bgm_se_id} episode={ctx.item.episode}",
            inputs=inputs,
            outputs={
                "subject_id": str(bgm_se_id) if bgm_se_id else "",
                "episode_id": "",
                "changed": changed,
                "error": "未找到对应集数",
            },
        )
