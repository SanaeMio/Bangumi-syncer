"""bgm_search 拆分 step（阶段二）

把 ``BangumiApi.bgm_search()`` 的 4 阶段拆成独立 step：
- ``SearchResetStep``             重置状态 + 预计算 stripped_title/stripped_ori
- ``DateExactSearchStep``         带首播日期的精确搜索
- ``VariantFallbackSearchStep``   无日期兜底变体搜索
- ``SearchFinalizeStep``          全 miss 清空状态 / 命中收尾

阶段二完成后 ``bgm_search`` 变为薄包装，依次调 4 个 step，
状态全部走 ctx，命中的变体方法经 ``bgm_search`` 的 out_meta 回传
给主 ctx（不再写 bgm 实例死状态，避免并发脏读）。
"""

from __future__ import annotations

import datetime

import httpx

from app.core.logging import logger
from app.services.matching.context import MatchContext
from app.services.matching.steps.base import MatchStepBase, StepOutcome
from app.utils.bangumi_archive._title_normalize import (
    API_SIMILARITY_FALLBACK,
    API_SIMILARITY_PRIMARY,
)

# 兜底搜索（无日期模式）拉取候选条目的上限
FALLBACK_SEARCH_LIMIT = 15


class SearchResetStep(MatchStepBase):
    """bgm_search 阶段A：状态重置 + 预计算剥离后缀的标题变体

    - 重置 ``ctx.matched_variant_method``（清除上次搜索残留）
    - 预计算 ``stripped_title`` / ``stripped_ori`` 写入 ctx
    - 清空 ``ctx.bgm_data``
    """

    stage = "api_search_reset"

    def execute(self, ctx: MatchContext) -> StepOutcome:
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


class DateExactSearchStep(MatchStepBase):
    """bgm_search 阶段B：带首播日期的精确搜索

    - premiere_date 有效时，air_date ± 2 天区间内依次尝试 ori_title → title → stripped_title → stripped_ori
    - is_movie=True 且未命中时，扩展 end_date +200 天再试一次
    - ValueError（日期格式）/httpx.HTTPError（网络）静默降级到阶段C
    """

    stage = "api_search_date_exact"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        premiere_date = ctx.item.release_date
        if not premiere_date or len(premiere_date) < 10:
            return StepOutcome(status="skipped", reason="无有效日期，跳过精确搜索")

        try:
            air_date = datetime.datetime.fromisoformat(premiere_date[:10])
            start_date = air_date - datetime.timedelta(days=2)
            end_date = air_date + datetime.timedelta(days=2)

            ctx.start_date_str = start_date.strftime("%Y-%m-%d")
            ctx.end_date_str = end_date.strftime("%Y-%m-%d")

            subject_types = ctx.subject_types
            # 依次尝试 ori_title → title → stripped_title → stripped_ori
            if ctx.item.ori_title:
                ctx.bgm_data = ctx.bgm.search(
                    title=ctx.item.ori_title,
                    start_date=ctx.start_date_str,
                    end_date=ctx.end_date_str,
                    subject_types=subject_types,
                )
            ctx.bgm_data = ctx.bgm_data or ctx.bgm.search(
                title=ctx.item.title,
                start_date=ctx.start_date_str,
                end_date=ctx.end_date_str,
                subject_types=subject_types,
            )
            # 剥离季数/集数后缀变体（仅在与原 title 不同时尝试）
            # 提升 API 场景匹配率：覆盖「完美世界 S06E279」类查询
            if (
                not ctx.bgm_data
                and ctx.stripped_title
                and ctx.stripped_title != ctx.item.title
            ):
                ctx.bgm_data = ctx.bgm.search(
                    title=ctx.stripped_title,
                    start_date=ctx.start_date_str,
                    end_date=ctx.end_date_str,
                    subject_types=subject_types,
                )
            if (
                not ctx.bgm_data
                and ctx.stripped_ori
                and ctx.stripped_ori != (ctx.item.ori_title or "")
            ):
                ctx.bgm_data = ctx.bgm.search(
                    title=ctx.stripped_ori,
                    start_date=ctx.start_date_str,
                    end_date=ctx.end_date_str,
                    subject_types=subject_types,
                )

            # 剧场版未命中时扩展日期范围再试一次（与剧集首播窗口 ±2 天不同）
            # P2-4 修复：使用局部变量而非覆写 ctx.end_date_str，避免日志误导
            if not ctx.bgm_data and ctx.item.media_type == "movie":
                movie_search_title = ctx.item.ori_title or ctx.item.title
                movie_end_date = air_date + datetime.timedelta(days=200)
                movie_end_date_str = movie_end_date.strftime("%Y-%m-%d")
                ctx.bgm_data = ctx.bgm.search(
                    title=movie_search_title,
                    start_date=ctx.start_date_str,
                    end_date=movie_end_date_str,
                    subject_types=subject_types,
                )
        except ValueError:
            logger.warning(
                f"首播日期格式解析失败: {premiere_date}，降级至无日期模式搜索"
            )
            return StepOutcome(status="miss", reason="日期格式解析失败，降级兜底")
        except httpx.HTTPError as e:
            # 网络不可达/重试耗尽：精确搜索失败不中断，降级到阶段C
            logger.error(f"精确搜索 API 失败（网络错误）: {e}")
            return StepOutcome(status="miss", reason="网络错误，降级兜底")

        if ctx.bgm_data:
            return StepOutcome(status="hit", reason="日期精确搜索命中")
        return StepOutcome(status="miss", reason="日期精确搜索未命中")


class VariantFallbackSearchStep(MatchStepBase):
    """bgm_search 阶段C：无日期兜底变体搜索

    触发条件：精确搜索无结果 或 首条相似度 < API_SIMILARITY_PRIMARY。
    - 构造搜索变体（原始 → 剥离季后缀 → 书名号剥离 → 标题分割主段 → 媒体前缀变体）
    - 逐变体×type 笛卡尔积尝试，保留相似度 > API_SIMILARITY_FALLBACK 的候选
    - 命中变体时标注 ``ctx.matched_variant_method``（bgm_search 收尾时
      经 out_meta 回传给主 ctx，替代死状态 last_match_method）
    """

    stage = "api_search_variant_fallback"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        # 触发条件：未命中 或 首条相似度 < PRIMARY
        if ctx.bgm_data and len(ctx.bgm_data) > 0:
            ratio = ctx.bgm.title_diff_ratio(
                title=ctx.item.title,
                ori_title=ctx.item.ori_title,
                bgm_data=ctx.bgm_data[0],
            )
            if ratio >= API_SIMILARITY_PRIMARY:
                return StepOutcome(status="skipped", reason="精确搜索已命中，跳过兜底")

        from app.utils.bangumi_archive._title_normalize import build_search_variants

        search_titles = build_search_variants(ctx.item.title, ctx.item.ori_title or "")
        subject_types = ctx.subject_types
        types_to_try = subject_types if subject_types else [2]

        for v in search_titles:
            t = v.query
            # v0 接口支持多 type 数组，但兜底路径保留单 type 循环
            # 以保持变体×type 笛卡尔积的尝试顺序（行为对齐原 search_old）
            for t_type in types_to_try:
                try:
                    # v0 search 无日期模式：start_date/end_date 为空时不加 air_date filter
                    bgm_data_old = ctx.bgm.search(
                        title=t,
                        start_date="",
                        end_date="",
                        limit=FALLBACK_SEARCH_LIMIT,
                        subject_types=[t_type],
                    )
                except httpx.HTTPError as e:
                    # 网络错误不中断 fallback：跳过该变体继续尝试
                    logger.error(f"兜底搜索网络失败({t!r}): {e}")
                    bgm_data_old = []

                if bgm_data_old:
                    # 保留相似度 > 0.3 的候选；全部低于阈值时视为未命中
                    # 阈值从 0.5 下调到 0.3，让更多低相似度候选保留用于 trace 回传
                    matched = [
                        c
                        for c in bgm_data_old
                        if ctx.bgm.title_diff_ratio(
                            ctx.item.title, ctx.item.ori_title, bgm_data=c
                        )
                        > API_SIMILARITY_FALLBACK
                    ]
                    if matched:
                        ctx.bgm_data = matched
                        # 标注预测性匹配方式：本次命中来自哪个派生变体
                        # （bgm_search 收尾时经 out_meta 回传给主 ctx）
                        ctx.matched_variant_method = v.method
                        return StepOutcome(
                            status="hit",
                            subject_id=str(matched[0].get("id", "")),
                            reason=f"兜底变体命中: {v.method}",
                        )
        # 全 miss：保留 ctx.bgm_data（DateExactSearchStep 的低相似度候选），
        # 不清空。这样 SearchFinalizeStep 返回 hit，APISearchStep 能拿到候选
        # 执行置信度检查 → low_confidence 时沉淀为 pending_candidate 供用户确认。
        # 若 DateExactSearchStep 本身也无结果，ctx.bgm_data 已为空，无需显式清空。
        return StepOutcome(status="miss", reason="变体兜底未命中")


class SearchFinalizeStep(MatchStepBase):
    """bgm_search 阶段D：收尾

    - 全 miss 时清空 ``ctx.matched_variant_method``（避免残留脏读），返回 miss
    - 命中时返回 hit + subject_id
    """

    stage = "api_search_finalize"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        if not ctx.bgm_data or len(ctx.bgm_data) == 0:
            # 清空预测性匹配方式标记，避免残留上次精确命中的 method 造成脏读
            ctx.matched_variant_method = ""
            return StepOutcome(status="miss", reason="无匹配结果", is_terminal=True)
        subject_id = str(ctx.bgm_data[0].get("id", ""))
        return StepOutcome(
            status="hit",
            subject_id=subject_id,
            is_terminal=True,
        )
