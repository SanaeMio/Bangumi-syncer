"""bangumi-data 本地匹配 step（阶段三）

对应原 _find_subject_id 阶段 2：bangumi-data 本地匹配。
命中即终止，match_method=bangumi_data。
未命中时回传候选列表到 trace，供候选队列展示。
"""

from __future__ import annotations

from app.core.logging import logger
from app.services.matching.context import MatchContext
from app.services.matching.steps.base import MatchStepBase, StepOutcome
from app.services.sync_service.match_trace import MatchCandidate


class BangumiDataStep(MatchStepBase):
    """bangumi-data 本地匹配

    - 调用 bgm_data.find_bangumi_id
    - 命中时设置 ctx.subject_id + ctx.match_stage=bangumi_data，终止管道
    - 未命中时回传候选列表（find_bangumi_candidates）到 outcome.candidates
    - bangumi-data 禁用时 skipped
    """

    stage = "bangumi_data"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        # 延迟导入：与 sync_service 内部使用同一 config_manager 引用，
        # 使测试 patch("app.services.sync_service.config_manager") 生效
        from app.services.sync_service import config_manager

        if not config_manager.get("bangumi_data", "enabled", fallback=True):
            return StepOutcome(status="skipped", reason="bangumi-data 已禁用")

        try:
            bgm_data = ctx.service._get_bangumi_data()
            release_date = None
            if ctx.item.release_date and len(ctx.item.release_date) >= 8:
                release_date = ctx.item.release_date[:10]

            result = bgm_data.find_bangumi_id(
                title=ctx.item.title,
                ori_title=ctx.item.ori_title,
                release_date=release_date,
                season=ctx.item.season,
                media_type=ctx.item.media_type,
            )

            if not result:
                # 未命中时回传候选列表到 trace，供候选队列展示
                candidates: list[MatchCandidate] = []
                try:
                    raw_candidates = bgm_data.find_bangumi_candidates(
                        title=ctx.item.title,
                        ori_title=ctx.item.ori_title,
                        release_date=release_date,
                        limit=5,
                    )
                    if raw_candidates:
                        candidates = [
                            MatchCandidate(
                                subject_id=str(c.get("id", "")),
                                name=c.get("name", ""),
                                name_cn=c.get("name_cn", ""),
                                score=float(c.get("score", 0.0)),
                            )
                            for c in raw_candidates
                        ]
                except Exception as cand_err:
                    logger.debug(
                        f"bangumi_data 候选回传失败（不影响主流程）: {cand_err}"
                    )
                reason = (
                    f"bangumi-data 无精确命中，回传 {len(candidates)} 条候选"
                    if candidates
                    else "bangumi-data 无匹配结果"
                )
                return StepOutcome(
                    status="miss",
                    reason=reason,
                    candidates=candidates,
                )

            bangumi_data_id, matched_title, date_matched = result
            # 季度ID可信度判定
            is_season_matched_id = self._judge_season_matched_id(
                ctx, matched_title, date_matched
            )

            ctx.subject_id = bangumi_data_id
            ctx.match_stage = "bangumi_data"
            ctx.is_season_matched_id = is_season_matched_id
            return StepOutcome(
                status="hit",
                subject_id=bangumi_data_id,
                reason=(
                    f"bangumi-data 匹配命中：{matched_title}，"
                    f"日期匹配={date_matched}，季度ID可信={is_season_matched_id}"
                ),
                score=1.0 if date_matched else 0.8,
                is_terminal=True,
            )
        except Exception as e:
            logger.error(f"bangumi-data 匹配出错: {e}")
            from app.services.sync_service import _build_error_detail

            return StepOutcome(
                status="error",
                reason=f"bangumi-data 匹配异常：{e}",
                error_detail=_build_error_detail(e),
            )

    @staticmethod
    def _judge_season_matched_id(
        ctx: MatchContext, matched_title: str, date_matched: bool
    ) -> bool:
        """判断 bangumi-data 命中的 ID 是否为特定季度ID"""
        if ctx.item.season <= 1:
            return True
        if date_matched:
            return True
        # 未通过日期匹配，检查标题是否包含季度信息
        return ctx.service._check_season_info_in_title(matched_title, ctx.item.season)
