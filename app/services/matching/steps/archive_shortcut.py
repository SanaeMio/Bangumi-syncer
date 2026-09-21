"""Archive 短路匹配 step（阶段五）

把 ``search()`` 内的 archive 短路提升为管道独立 step，与 APISearchStep 同级。
Archive 开启且命中时设置 ``ctx.bgm_data``，APISearchStep 检测到已有数据后
跳过 ``bgm_search()`` 调用，直接走候选排序 + post_search 改选（复用后续逻辑）。
Archive 关闭/未命中时 APISearchStep 正常走 API 搜索（托底）。

``search()`` 移除 archive 短路后只做纯 API 调用，职责单一。
"""

from __future__ import annotations

import datetime

from app.core.logging import logger
from app.services.matching.context import MatchContext
from app.services.matching.contracts import SOURCE_ARCHIVE, candidates_from_rows
from app.services.matching.steps.base import MatchStepBase, StepOutcome
from app.utils.bangumi_constants import SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL


class ArchiveShortcutStep(MatchStepBase):
    """Archive 短路匹配

    - Archive 开启时优先走本地归档（``bgm._archive.try_search``）
    - 命中时设置 ``ctx.bgm_data`` + ``ctx.match_stage="archive"`` +
      ``ctx.archive_hit=True`` + ``ctx.match_method_detail``，不终止（让
      APISearchStep 做后续改选）
    - Archive 关闭/未命中时返回 skipped/miss，APISearchStep 正常走 API 搜索
    - trace 中有独立 ``stage="archive"`` step
    """

    stage = "archive"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        item = ctx.item
        service = ctx.service

        # 获取 bgm（与 APISearchStep 同方式，设置 ctx.bgm 供后续 step 复用）
        bgm = ctx.bgm or service._get_bangumi_api_for_user(item.user_name)
        ctx.bgm = bgm
        if not bgm:
            # bgm 不可用，跳过 archive 短路，让 APISearchStep 处理错误
            return StepOutcome(status="skipped", reason="bgm 不可用，跳过 archive 短路")

        # Archive 未启用，跳过（API 托底）
        if not bgm._archive.enabled:
            return StepOutcome(status="skipped", reason="archive 未启用，走 API 托底")

        # 计算搜索标题（与 APISearchStep 一致：优先归一化标题）
        search_title = ctx.normalized_title or item.title

        # 构建日期窗口（与 DateExactSearchStep 一致：±2 天）
        # archive 短路用 start_date 抽取年份做消歧，end_date 做区间过滤
        start_date = ""
        end_date = ""
        if item.release_date and len(item.release_date) >= 8:
            try:
                air_date = datetime.datetime.fromisoformat(item.release_date[:10])
                start_date = (air_date - datetime.timedelta(days=2)).strftime(
                    "%Y-%m-%d"
                )
                end_date = (air_date + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
            except ValueError:
                pass

        # subject_types（与 APISearchStep 一致）
        from app.services.sync_service import config_manager

        enable_real_action = config_manager.get(
            "sync", "enable_real_action", fallback=False
        )
        if item.media_type == "real_action":
            subject_types = [SUBJECT_TYPE_REAL]
        elif enable_real_action:
            subject_types = [SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL]
        else:
            subject_types = [SUBJECT_TYPE_ANIME]
        ctx.subject_types = subject_types

        request_params = {
            "title": search_title,
            "start_date": start_date,
            "end_date": end_date,
            "subject_types": subject_types,
            "source": "archive_shortcut",
        }
        inputs = {
            "title": search_title,
            "start_date": start_date,
            "end_date": end_date,
            "subject_types": subject_types,
        }

        try:
            shortcut = bgm._archive.try_search(
                title=search_title,
                start_date=start_date,
                end_date=end_date,
                limit=15,
                subject_types=subject_types,
            )
        except Exception as e:
            logger.warning(f"archive 短路异常（降级到 API）: {e}")
            ctx.archive_hit = False
            return StepOutcome(
                status="error",
                reason=f"archive 短路异常: {e}",
                inputs=inputs,
                outputs={"subject_id": "", "error": str(e)},
                request_params=request_params,
            )

        if not shortcut.hit or not shortcut.data:
            # archive 未命中：重置 archive_hit（APISearchStep 据此判定命中来源）
            ctx.archive_hit = False
            return StepOutcome(
                status="miss",
                reason=f"archive 短路未命中: {shortcut.reason}",
                inputs=inputs,
                outputs={"subject_id": "", "total_candidates": 0},
                request_params=request_params,
            )

        # ------------------------------------------------------------------
        # 类型冲突补召回：请求「动画剧集」但 archive 命中全是三次元条目时，
        # 用纯动画类型再查一次，把同名动画候选并入。
        #
        # 场景（线上真实失败）：媒体库推送「凡人修仙传」，archive 精确匹配
        # 只命中真人剧 434076（type=6），而动画条目一律带后缀
        # （「凡人修仙传 年番」「凡人修仙传之凡人风起天南」…）故精确匹配
        # 召回不到。此前 APISearchStep 检测到类型冲突后清空 bgm_data 降级
        # 到 API 搜索，而 search() 只做纯 API 调用（archive 已提升为独立
        # step），离线/无网环境直接「Bangumi 搜索无结果」→ 完全漏标。
        #
        # 补召回后动画候选排在前面：下游「top 为 real_action 且列表无 episode
        # 候选才降级」的判据随之不成立，改由 _media_type_reselect 正常取舍。
        # ------------------------------------------------------------------
        data = shortcut.data
        if (
            item.media_type == "episode"
            and SUBJECT_TYPE_ANIME in subject_types
            and SUBJECT_TYPE_REAL in subject_types
            and not any(c.get("type") == SUBJECT_TYPE_ANIME for c in data)
        ):
            try:
                anime_only = bgm._archive.try_search(
                    title=search_title,
                    start_date=start_date,
                    end_date=end_date,
                    limit=15,
                    subject_types=[SUBJECT_TYPE_ANIME],
                )
            except Exception as e:  # noqa: BLE001 — 补召回失败不阻断主流程
                logger.debug(f"archive 动画类型补召回失败（沿用原候选）: {e}")
                anime_only = None
            if anime_only is not None and anime_only.hit and anime_only.data:
                seen = {c.get("id") for c in data}
                extra = [c for c in anime_only.data if c.get("id") not in seen]
                if extra:
                    logger.debug(
                        f"archive 命中全为三次元，补召回同名动画候选 "
                        f"{len(extra)} 条: "
                        f"{[c.get('name_cn') or c.get('name') for c in extra][:3]}"
                    )
                    data = extra + data

        # archive 命中：设置 ctx，不终止（让 APISearchStep 做候选排序 + post_search 改选）
        ctx.archive_hit = True
        ctx.bgm_data = data
        ctx.match_stage = "archive"
        ctx.match_method_detail = shortcut.match_method or "exact"

        first = data[0] if data else {}
        api_response_summary = {
            "total_candidates": len(data),
            "is_archive_hit": True,
            "first_subject_id": first.get("id"),
            "first_name": first.get("name") or "",
            "first_name_cn": first.get("name_cn") or "",
            "match_method": shortcut.match_method,
        }

        # C4：archive 命中也要产出候选。此前只设 ctx.bgm_data，不产出候选、
        # 不标注分数——「短路径盲信」的根源（量化：76 条命中里 10 条标错，
        # 13.2% 无值守静默错误率下限）。这里用 title_diff_ratio 给每条候选打
        # 相似度分，让后续裁决层能算 margin。控制流不变（仍 is_terminal=False）。
        try:
            candidates = candidates_from_rows(
                data,
                source=SOURCE_ARCHIVE,
                limit=15,
                scorer=lambda row: bgm.title_diff_ratio(
                    title=search_title, ori_title=item.ori_title, bgm_data=row
                ),
            )
        except Exception as e:  # noqa: BLE001 — 打分失败不阻断短路命中
            logger.debug(f"archive 候选打分失败（不影响主流程）: {e}")
            candidates = candidates_from_rows(data, source=SOURCE_ARCHIVE, limit=15)

        return StepOutcome(
            status="hit",
            subject_id=str(first.get("id", "")),
            reason=f"archive 短路命中: {shortcut.match_method}",
            candidates=candidates,
            inputs=inputs,
            outputs={
                "subject_id": str(first.get("id", "")),
                "match_method": shortcut.match_method or "",
                "total_candidates": len(data),
                "is_archive_hit": True,
            },
            request_params=request_params,
            api_response_summary=api_response_summary,
            is_terminal=False,  # 不终止，让 APISearchStep 做后续改选
        )
