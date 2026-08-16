"""bgm_search 拆分 step（阶段二）

把 ``BangumiApi.bgm_search()`` 的 4 阶段拆成独立 step：
- ``SearchResetStep``        重置 bgm 状态 + 预计算 stripped_title/stripped_ori
- ``DateExactSearchStep``     带首播日期的精确搜索（阶段二第二步）
- ``VariantFallbackSearchStep`` 无日期兜底变体搜索（阶段二第二步）
- ``SearchFinalizeStep``      全 miss 清空状态 / 命中收尾

阶段二第一步：仅实现 ``SearchResetStep`` + ``SearchFinalizeStep``，
``bgm_search`` 接入这两个边界 step，中间逻辑操作 ctx 字段。
死状态 ``bgm.last_match_method`` 仍写（兼容），同时写 ``ctx.matched_variant_method``。
"""

from __future__ import annotations

from app.services.matching.context import MatchContext
from app.services.matching.steps.base import MatchStepBase, StepOutcome


class SearchResetStep(MatchStepBase):
    """bgm_search 阶段A：状态重置 + 预计算剥离后缀的标题变体

    对应原 ``bgm_search`` L237-260：
    - 重置 ``last_hit_source`` / ``last_match_method``（兼容其他读取点）
    - 预计算 ``stripped_title`` / ``stripped_ori`` 写入 ctx
    - 清空 ``ctx.bgm_data``
    """

    stage = "api_search_reset"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        # 重置命中来源标记：反映本次 bgm_search 的最终命中来源
        ctx.bgm.last_hit_source = ""
        # 同步重置预测性匹配方式标记（命中后由命中变体回填）
        ctx.bgm.last_match_method = ""

        # 预计算剥离季数/集数后缀的标题变体（用于 API 查询提升匹配率）
        # 标题归一化工具已下沉到 bangumi_archive/_title_normalize，
        # API 与 archive 路径共用同一套剥离逻辑
        from app.utils.bangumi_archive._title_normalize import (
            _strip_season_episode_suffix,
        )

        ctx.stripped_title = _strip_season_episode_suffix(ctx.item.title)
        ctx.stripped_ori = (
            _strip_season_episode_suffix(ctx.item.ori_title)
            if ctx.item.ori_title
            else ""
        )
        ctx.bgm_data = None
        ctx.matched_variant_method = ""
        return StepOutcome(status="skipped", reason="状态重置完成")


class SearchFinalizeStep(MatchStepBase):
    """bgm_search 阶段D：收尾

    对应原 ``bgm_search`` L378-387：
    - 全 miss 时清空 ``last_match_method``（避免残留脏读），返回 miss
    - 命中时返回 hit + subject_id
    """

    stage = "api_search_finalize"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        if not ctx.bgm_data or len(ctx.bgm_data) == 0:
            # 清空预测性匹配方式标记，避免残留上次精确命中的 method 造成脏读
            ctx.bgm.last_match_method = ""
            ctx.matched_variant_method = ""
            return StepOutcome(status="miss", reason="无匹配结果", is_terminal=True)
        subject_id = str(ctx.bgm_data[0].get("id", ""))
        return StepOutcome(
            status="hit",
            subject_id=subject_id,
            is_terminal=True,
        )
