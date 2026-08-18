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
from app.services.matching.steps.base import MatchStepBase, StepOutcome


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
            subject_types = [6]
        elif enable_real_action:
            subject_types = [2, 6]
        else:
            subject_types = [2]
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

        # archive 命中：设置 ctx，不终止（让 APISearchStep 做候选排序 + post_search 改选）
        ctx.archive_hit = True
        ctx.bgm_data = shortcut.data
        ctx.match_stage = "archive"
        ctx.match_method_detail = shortcut.match_method or "exact"

        first = shortcut.data[0] if shortcut.data else {}
        api_response_summary = {
            "total_candidates": len(shortcut.data),
            "is_archive_hit": True,
            "first_subject_id": first.get("id"),
            "first_name": first.get("name") or "",
            "first_name_cn": first.get("name_cn") or "",
            "match_method": shortcut.match_method,
        }

        return StepOutcome(
            status="hit",
            subject_id=str(first.get("id", "")),
            reason=f"archive 短路命中: {shortcut.match_method}",
            inputs=inputs,
            outputs={
                "subject_id": str(first.get("id", "")),
                "match_method": shortcut.match_method or "",
                "total_candidates": len(shortcut.data),
                "is_archive_hit": True,
            },
            request_params=request_params,
            api_response_summary=api_response_summary,
            is_terminal=False,  # 不终止，让 APISearchStep 做后续改选
        )
