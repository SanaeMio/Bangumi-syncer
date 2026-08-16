"""跨季链回退 step：通过前传/续集链在关联季条目中查找含目标 sort 的章节

仅在 episode_resolve 未命中（ctx.bgm_ep_id 为空）时执行，命中时更新
ctx.bgm_se_id / bgm_ep_id 并记录改选信息（供 ResultStep 统一覆写 final_*）。
"""

from __future__ import annotations

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.steps.base import ExecutionStepBase

from ....core.logging import logger

# 命中路径 → 细粒度匹配方式 / 展示文案（与 find_episode_across_seasons 各命中点回填值对应）
_PATH_DETAIL = {
    "chain": ("cross_season_chain", "前传/续集链"),
    "franchise_archive": ("cross_season_franchise_archive", "同 IP 闭包（本地归档）"),
    "franchise_online": ("cross_season_franchise_online", "同 IP 改编一跳（在线）"),
}


class CrossSeasonStep(ExecutionStepBase):
    """跨季链查找：命中时改选 subject + ep，并记录命中路径"""

    stage = "cross_season"

    def execute(self, ctx: ExecutionContext) -> StepOutcome:
        if ctx.bgm_ep_id:
            # 集数解析已命中，无需跨季回退（skipped 语义，由 step 内部 gate）
            return StepOutcome(
                status="skipped",
                reason="集数解析已命中，无需跨季回退",
            )

        cross_input_subject = str(ctx.subject_id)
        chain_pick = None
        try:
            chain_pick = ctx.bgm.find_episode_across_seasons(
                ctx.subject_id, ctx.item.episode
            )
        except Exception:
            logger.debug(f"关联季条目链查找异常: {ctx.subject_id}", exc_info=True)

        if not chain_pick:
            logger.error(
                f"bgm: {ctx.subject_id=} {ctx.item.season=} {ctx.item.episode=}, "
                "不存在或集数过多，跳过"
            )
            return StepOutcome(
                status="miss",
                reason=f"跨季链查找未命中含 sort={ctx.item.episode} 的季条目",
                processed_payload={
                    "input_subject_id": cross_input_subject,
                    "output_subject_id": "",
                    "output_episode_id": "",
                    "target_episode": ctx.item.episode,
                    "changed": False,
                    "error": f"未找到含 sort={ctx.item.episode} 的关联季条目",
                },
                is_terminal=True,
            )

        chain_subject_id, chain_ep_id = chain_pick
        cross_path = getattr(ctx.bgm, "last_cross_season_path", "") or ""
        match_method_detail, path_label = _PATH_DETAIL.get(
            cross_path, ("cross_season_chain", "跨季链")
        )
        prev_subject_id = ctx.subject_id
        logger.debug(
            f"通过关联季条目链找到目标集({path_label}): "
            f"原 subject_id={prev_subject_id}, "
            f"改选 subject_id={chain_subject_id}, ep_id={chain_ep_id}, "
            f"目标 episode={ctx.item.episode}"
        )

        # 改选结果写入 ctx，final_* 覆写由 ResultStep 统一结算
        ctx.bgm_se_id = str(chain_subject_id)
        ctx.bgm_ep_id = str(chain_ep_id)
        ctx.cross_season_hit = True
        ctx.cross_season_subject_id = str(chain_subject_id)
        ctx.cross_season_ep_id = str(chain_ep_id)
        ctx.cross_season_path = cross_path

        return StepOutcome(
            status="hit",
            subject_id=str(chain_subject_id),
            reason=(
                f"跨季链查找命中（{path_label}）：原 subject_id={prev_subject_id} → "
                f"chain_subject_id={chain_subject_id}, "
                f"ep_id={chain_ep_id} (目标 episode={ctx.item.episode})"
            ),
            processed_payload={
                "input_subject_id": cross_input_subject,
                "output_subject_id": str(chain_subject_id),
                "output_episode_id": str(chain_ep_id),
                "target_episode": ctx.item.episode,
                "changed": str(prev_subject_id) != str(chain_subject_id),
                "match_path": cross_path,
                "subject_url": f"https://bgm.tv/subject/{chain_subject_id}",
                "episode_url": f"https://bgm.tv/ep/{chain_ep_id}",
            },
        )
