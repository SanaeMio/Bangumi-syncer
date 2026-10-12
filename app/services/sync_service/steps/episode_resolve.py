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
        # 集数分段映射换算出的目标集号（非 None 时优先于请求集号）
        target_ep = ctx.mapping_target_episode
        inputs = {
            "subject_id": str(ctx.subject_id),
            "is_season_id": bool(ctx.is_season_matched_id),
            "season": ctx.item.season,
            "episode": ctx.item.episode,
            "media_type": ctx.item.media_type,
            "release_date": ctx.item.release_date or "",
            "mapping_target_episode": (str(target_ep) if target_ep is not None else ""),
        }

        try:
            bgm_se_id, bgm_ep_id = ctx.service._resolve_season_episode(
                ctx.bgm,
                ctx.item,
                ctx.subject_id,
                ctx.is_season_matched_id,
                target_episode=target_ep,
                # 显式绑定：条目内找不到该集时报错，不得沿续集链改选其它条目。
                # 改选恰恰发生在本步内部（get_target_season_episode_id 的回退），
                # 早于 CrossSeasonStep 的守卫，因此必须在这里就禁掉。
                mapping_is_explicit=ctx.mapping_is_explicit,
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
        # 报告用集号：有分段映射时展示换算后的目标集号（否则用户看到媒体集号
        # 与解析结果对不上，会以为解析错了）
        shown_ep = target_ep if target_ep is not None else ctx.item.episode

        if bgm_ep_id:
            return StepOutcome(
                status="hit",
                subject_id=str(bgm_se_id),
                reason=(
                    f"集数解析：subject={bgm_se_id} episode={shown_ep} → "
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
            reason=f"集数解析未命中：subject={bgm_se_id} episode={shown_ep}",
            inputs=inputs,
            outputs={
                "subject_id": str(bgm_se_id) if bgm_se_id else "",
                "episode_id": "",
                "changed": changed,
                "error": "未找到对应集数",
            },
        )
