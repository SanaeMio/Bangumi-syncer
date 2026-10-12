"""自定义映射 step（阶段三）

对应原 _find_subject_id 阶段 1：自定义映射（含季度感知 + 正则规则 + 集数分段）。
命中即终止，match_method=custom_mapping。

**显式性语义（本 step 的核心）**：
用户「显式指定了目标条目」与「填了个主条目让程序往后找」是两回事。
- 显式（季度感知格式 / 带 segments）：subject_id 就是目标季条目本身，
  ``is_season_matched_id=True`` → 执行阶段在**该条目内**直接按集号定位，
  并且禁止跨季链改选（用户已明确表态，改选等于否定用户意图）。
- 非显式（简单格式 / 正则规则）：保持历史语义（填主条目、沿续集链往后找）。
"""

from __future__ import annotations

from app.services.matching.context import MatchContext
from app.services.matching.contracts import (
    SOURCE_CUSTOM_MAPPING,
    SubjectRef,
    make_candidate,
)
from app.services.matching.steps.base import MatchStepBase, StepOutcome


class CustomMappingStep(MatchStepBase):
    """自定义映射查找

    - 调用 mapping_service.find_episode_mapping（集数分段）与 find_mapping
    - 命中时设置 ctx.subject_id + ctx.match_stage=custom_mapping，终止管道
    - 未命中继续下一步
    """

    stage = "custom_mapping"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        from app.services.mapping_service import mapping_service

        item = ctx.item
        title = item.title
        ori_title = item.ori_title or ""

        mapping_inputs = {
            "title": title,
            "ori_title": ori_title,
            "season": item.season,
            "episode": item.episode,
        }

        # 先查集数分段映射（更具体，优先级高于标题级映射）。
        # 产出「目标条目 + 目标集号」，供执行阶段直接使用。
        seg_sid, seg_ep, seg_reason = mapping_service.find_episode_mapping(
            title=title,
            ori_title=ori_title,
            season=item.season,
            episode=item.episode,
        )
        if seg_sid:
            return self._hit(
                ctx,
                subject_id=seg_sid,
                reason=seg_reason,
                match_method="segment",
                is_explicit=True,
                target_episode=seg_ep,
                inputs=mapping_inputs,
            )

        mapping_subject_id, match_type, match_reason, is_explicit = (
            mapping_service.find_mapping(
                title=title,
                ori_title=ori_title,
                season=item.season,
            )
        )

        if mapping_subject_id:
            # is_explicit 已由 find_mapping 判定完毕（声明了 season，或条目携带
            # 非空的 segments）。这里是唯一的判定来源，不再自行读配置重算。
            return self._hit(
                ctx,
                subject_id=mapping_subject_id,
                reason=match_reason,
                match_method=match_type or "",
                is_explicit=is_explicit,
                inputs=mapping_inputs,
            )

        return StepOutcome(
            status="miss", reason="自定义映射与正则规则均未命中", inputs=mapping_inputs
        )

    @staticmethod
    def _hit(
        ctx: MatchContext,
        *,
        subject_id: str,
        reason: str,
        match_method: str,
        is_explicit: bool,
        inputs: dict,
        target_episode: int | None = None,
    ) -> StepOutcome:
        """构造命中产物（两条命中路径共用）"""
        ctx.subject_id = subject_id
        ctx.match_stage = "custom_mapping"
        # 显式指定 → 该 subject 就是目标季条目，执行阶段在条目内直接按集号定位；
        # 非显式 → 沿用「填主条目、沿续集链往后找」的历史语义。
        ctx.is_season_matched_id = is_explicit
        ctx.mapping_is_explicit = is_explicit
        ctx.mapping_target_episode = target_episode

        # C4：任何策略命中都必须产出候选，不许只回一个裸 id。
        # 自定义映射是用户显式指定的确定性映射，置信度等价于 1.0。
        candidate = make_candidate(
            SubjectRef(subject_id=str(subject_id), source=SOURCE_CUSTOM_MAPPING),
            score=1.0,
        )
        outputs = {
            "subject_id": subject_id,
            "match_method": match_method,
            "match_reason": reason,
            "is_explicit": is_explicit,
        }
        if target_episode is not None:
            outputs["target_episode"] = str(target_episode)

        return StepOutcome(
            status="hit",
            subject_id=subject_id,
            reason=reason,
            score=1.0,
            candidates=[candidate],
            inputs=inputs,
            outputs=outputs,
            is_terminal=True,
        )
