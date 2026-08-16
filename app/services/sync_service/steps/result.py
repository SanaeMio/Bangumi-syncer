"""结果结算 step：统一结算 final_* 汇总字段并构建 result step

- 跨季改选时覆写 final_subject_id / final_match_method / final_match_method_detail
- 结算 final_episode_id / final_action / final_status / final_message
- 从 bgm.get_subject 取标题，产出经结果链传给编排器（供通知/持久化复用）
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

    def execute(self, ctx: ExecutionContext, prev: dict | None = None) -> StepOutcome:
        trace = ctx.trace

        # 当前有效产物（上游：episode_resolve → cross_season → sync_action）
        outputs = prev or {}
        bgm_se_id = str(outputs.get("subject_id") or ctx.subject_id)
        bgm_ep_id = str(outputs.get("episode_id") or "")
        mark_status = outputs.get("mark_status")
        if mark_status is None:
            raise ValueError("结果结算失败：缺少上游 mark_status")

        # 跨季改选：覆写匹配阶段结算的 final_*（原编排器手动覆写逻辑）。
        # ctx.step_outputs 由结果链统一回填，跨季 hit 的 outputs 含 match_path
        cross = ctx.step_outputs.get("cross_season")
        if cross and "match_path" in cross:
            trace.final_subject_id = str(cross.get("subject_id") or bgm_se_id)
            trace.final_episode_id = str(cross.get("episode_id") or bgm_ep_id)
            # 跨季链命中：粗粒度记 archive，细粒度记具体路径
            trace.final_match_method = "archive"
            detail, _ = _PATH_DETAIL.get(
                cross.get("match_path", ""), ("cross_season_chain", "跨季链")
            )
            trace.final_match_method_detail = detail

        # 回填最终剧集 ID 到 trace
        trace.final_episode_id = str(bgm_ep_id)

        result_message = ctx.service._format_mark_status_message(mark_status)
        trace.final_action = str(mark_status)
        trace.final_status = "success"
        trace.final_message = result_message

        # 取条目标题（供编排器通知/持久化复用，避免重复 get_subject）
        bgm_title = ""
        try:
            subject_info = ctx.bgm.get_subject(bgm_se_id)
            if subject_info:
                bgm_title = (
                    subject_info.get("name_cn") or subject_info.get("name") or ""
                )
        except Exception:
            logger.debug(f"获取条目标题失败: {bgm_se_id}", exc_info=True)

        return StepOutcome(
            status="hit",
            subject_id=bgm_se_id,
            reason=(
                f"{result_message} · https://bgm.tv/subject/{bgm_se_id}"
                + (f" · https://bgm.tv/ep/{bgm_ep_id}" if bgm_ep_id else "")
            ),
            inputs={
                "subject_id": bgm_se_id,
                "episode_id": bgm_ep_id,
                "mark_status": mark_status,
            },
            outputs={
                "status": "success",
                "subject_id": bgm_se_id,
                "episode_id": bgm_ep_id,
                "subject_url": f"https://bgm.tv/subject/{bgm_se_id}",
                "episode_url": (f"https://bgm.tv/ep/{bgm_ep_id}" if bgm_ep_id else ""),
                "bgm_title": bgm_title,
                "message": result_message,
            },
        )
