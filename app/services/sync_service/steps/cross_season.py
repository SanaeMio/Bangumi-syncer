"""跨季链回退 step：通过前传/续集链在关联季条目中查找含目标 sort 的章节

仅在 episode_resolve 未命中（prev 无 episode_id）时执行（step 内部 gate）：
- hit：outputs 的 subject_id / episode_id / match_path 经结果链 merge 覆盖
  上游产物（即"跨季改选"语义），step 不写 ctx
- miss：is_terminal（编排器据此走集数未找到分支），空值产物不覆盖上游
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

    def execute(self, ctx: ExecutionContext, prev: dict | None = None) -> StepOutcome:
        inputs = {
            "subject_id": str(ctx.subject_id),
            "target_episode": ctx.item.episode,
        }

        # gate：上游已有 ep_id（本季直接命中）则跳过
        if prev and prev.get("episode_id"):
            return StepOutcome(status="skipped", reason="集数解析已命中，无需跨季回退")

        target_season = ctx.item.season or 1
        try:
            chain_pick = ctx.bgm.find_episode_across_seasons(
                ctx.subject_id,
                ctx.item.episode,
                target_season=target_season,
            )
        except Exception:
            logger.debug(f"关联季条目链查找异常: {ctx.subject_id}", exc_info=True)
            chain_pick = None

        if not chain_pick:
            logger.error(
                f"bgm: {ctx.subject_id=} {ctx.item.season=} {ctx.item.episode=}, "
                "不存在或集数过多，跳过"
            )
            # 集数语义随 target_season 变化：大于 1 时是季内集编号，否则是全局 sort
            ep_desc = (
                f"第 {target_season} 季第 {ctx.item.episode} 集"
                if target_season > 1
                else f"sort={ctx.item.episode}"
            )
            return StepOutcome(
                status="miss",
                reason=f"跨季链查找未命中含 {ep_desc} 的季条目",
                inputs=inputs,
                outputs={
                    "subject_id": "",
                    "episode_id": "",
                    "changed": False,
                    "error": f"未找到含 {ep_desc} 的关联季条目",
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

        # 改选结果经结果链 merge 覆盖上游产物（同键覆盖），final_* 覆写由
        # ResultStep 统一结算
        return StepOutcome(
            status="hit",
            subject_id=str(chain_subject_id),
            reason=(
                f"跨季链查找命中（{path_label}）：原 subject_id={prev_subject_id} → "
                f"chain_subject_id={chain_subject_id}, "
                f"ep_id={chain_ep_id} (目标 episode={ctx.item.episode})"
            ),
            inputs=inputs,
            outputs={
                "subject_id": str(chain_subject_id),
                "episode_id": str(chain_ep_id),
                "changed": str(prev_subject_id) != str(chain_subject_id),
                "match_path": cross_path,
                "match_method_detail": match_method_detail,
                "match_path_label": path_label,
                "subject_url": f"https://bgm.tv/subject/{chain_subject_id}",
                "episode_url": f"https://bgm.tv/ep/{chain_ep_id}",
            },
        )
