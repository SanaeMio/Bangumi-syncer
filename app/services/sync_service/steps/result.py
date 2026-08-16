"""结果结算 step：统一结算 final_* 汇总字段并构建 result step

- 跨季改选时覆写 final_subject_id / final_match_method / final_match_method_detail
- 结算 final_episode_id / final_action / final_status / final_message
- 从 bgm.get_subject 取标题写入 ctx.bgm_title（供编排器通知/持久化复用）
"""

from __future__ import annotations

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.steps.base import ExecutionStepBase
from app.services.sync_service.steps.cross_season import _PATH_DETAIL

from ....core.logging import logger


class ResultStep(ExecutionStepBase):
    """同步结果结算：统一写入 final_* 汇总并输出 result step"""

    stage = "result"

    def execute(self, ctx: ExecutionContext) -> StepOutcome:
        trace = ctx.trace

        # 跨季改选：覆写匹配阶段结算的 final_*（原编排器手动覆写逻辑）
        if ctx.cross_season_hit:
            trace.final_subject_id = ctx.cross_season_subject_id
            trace.final_episode_id = ctx.cross_season_ep_id
            # 跨季链命中：粗粒度记 archive，细粒度记具体路径
            trace.final_match_method = "archive"
            detail, _ = _PATH_DETAIL.get(
                ctx.cross_season_path, ("cross_season_chain", "跨季链")
            )
            trace.final_match_method_detail = detail

        # 回填最终剧集 ID 到 trace
        trace.final_episode_id = str(ctx.bgm_ep_id)

        result_message = ctx.service._format_mark_status_message(ctx.mark_status)
        ctx.result_message = result_message
        trace.final_action = str(ctx.mark_status)
        trace.final_status = "success"
        trace.final_message = result_message

        # 取条目标题写入 ctx（供编排器通知/持久化复用，避免重复 get_subject）
        bgm_title = ""
        try:
            subject_info = ctx.bgm.get_subject(str(ctx.bgm_se_id))
            if subject_info:
                bgm_title = (
                    subject_info.get("name_cn") or subject_info.get("name") or ""
                )
        except Exception:
            logger.debug(f"获取条目标题失败: {ctx.bgm_se_id}", exc_info=True)
        ctx.bgm_title = bgm_title

        return StepOutcome(
            status="hit",
            subject_id=str(ctx.bgm_se_id),
            reason=(
                f"{result_message} · https://bgm.tv/subject/{ctx.bgm_se_id}"
                + (f" · https://bgm.tv/ep/{ctx.bgm_ep_id}" if ctx.bgm_ep_id else "")
            ),
            processed_payload={
                "status": "success",
                "episode": f"S{ctx.item.season:02d}E{ctx.item.episode:02d}",
                "subject_id": str(ctx.bgm_se_id),
                "episode_id": str(ctx.bgm_ep_id) if ctx.bgm_ep_id else "",
                "subject_url": f"https://bgm.tv/subject/{ctx.bgm_se_id}",
                "episode_url": (
                    f"https://bgm.tv/ep/{ctx.bgm_ep_id}" if ctx.bgm_ep_id else ""
                ),
                "bgm_title": bgm_title,
                "message": result_message,
            },
        )
