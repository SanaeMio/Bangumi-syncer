"""同步动作 step：标记剧集为看过（或对剧场版只标条目在看）

输入（prev 上游产物）：subject_id / episode_id（episode_resolve 或
cross_season 改选后的当前有效产物）
产出（outputs）：mark_status（0/1/2；-1=MARK_QUEUED 表示 API 不可达已入队）

queued 与认证失败均为终态（is_terminal=True）：
- queued：无 result step，由编排器走 _handle_queued 收尾
- 认证失败：由编排器直接返回 error 响应

无 episode_id 时走「只标条目在看」：这是剧场版正片的语义（整片观看，不存在
第几集），与 sync_movie_watching 一致。
"""

from __future__ import annotations

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.retry import MARK_QUEUED
from app.services.sync_service.steps.base import ExecutionStepBase

_AUTH_ERROR_HINTS = ("认证失败", "access_token")


def _is_auth_error(ve: ValueError) -> bool:
    text = str(ve)
    return any(hint in text for hint in _AUTH_ERROR_HINTS)


class SyncActionStep(ExecutionStepBase):
    """同步动作：标记剧集为看过（带重试）"""

    stage = "sync_action"

    @staticmethod
    def _auth_error(inputs: dict) -> StepOutcome:
        """认证失败的统一终态产出"""
        return StepOutcome(
            status="error",
            reason="认证失败: Bangumi 账号 token 无效或已过期",
            inputs=inputs,
            outputs={"mark_status": "error", "error": "auth_failed"},
            error_detail={"type": "auth_failed", "message": "auth failed"},
            is_terminal=True,
        )

    def execute(self, ctx: ExecutionContext, prev: dict | None = None) -> StepOutcome:
        outputs = prev or {}
        bgm_se_id = str(outputs.get("subject_id") or ctx.subject_id)
        bgm_ep_id = str(outputs.get("episode_id") or "")
        inputs = {
            "subject_id": bgm_se_id,
            "episode_id": bgm_ep_id,
        }

        # 无单集：剧场版正片只标条目在看，不碰章节。
        if not bgm_ep_id:
            return self._mark_subject_watching(ctx, bgm_se_id, inputs)

        try:
            mark_status = ctx.service._retry_mark_episode(
                ctx.bgm,
                bgm_se_id,
                bgm_ep_id,
                queue_payload=ctx.item.model_dump(),
            )
        except ValueError as ve:
            if _is_auth_error(ve):
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

    def _mark_subject_watching(
        self, ctx: ExecutionContext, bgm_se_id: str, inputs: dict
    ) -> StepOutcome:
        """只把条目设为「在看」（不点单集）

        用于剧场版正片：Bangumi 的剧场版是单个条目、没有「第几集」的概念，
        正确动作与 ``sync_movie_watching`` 一致。返回值语义与集数路径不同
        （0=无需变更，1=已设为在看），故文案单独给出。
        """
        try:
            mark_status = ctx.bgm.ensure_subject_watching(bgm_se_id)
        except ValueError as ve:
            if _is_auth_error(ve):
                return self._auth_error(inputs)
            raise ve

        if mark_status == MARK_QUEUED:
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

        return StepOutcome(
            status="hit",
            subject_id=bgm_se_id,
            reason=f"剧场版正片：ensure_subject_watching 返回 {mark_status}",
            inputs=inputs,
            outputs={"mark_status": mark_status},
        )
