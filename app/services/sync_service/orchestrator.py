"""同步编排器（阶段四）

统一编排：请求处理 → 匹配 → 集数解析 → 标记 → 持久化。

剥离原 sync_service._find_matching_subject / _sync_custom_item_body 的耦合：
- DB 写入、通知、调度器触发统一收口到本编排器
- trace→DB 的 finish+to_dict+log 样板统一收口到 _persist_sync_record
- 匹配阶段由 MatchPipeline 编排（matching/pipeline.py）；集数解析 / 跨季回退 /
  标记 / 结果结算由 SyncPipeline（本文件 _run_execution_pipeline 装配）编排，
  step 实现见 steps/ 子包。episode_resolve / cross_season / sync_action / result
  每步的进出数据与耗时由 SyncPipeline._record_trace 统一记录，final_* 由
  ResultStep 统一结算（跨季改选覆写也在其中），编排器只负责终态分支与副作用
  （通知 / 收藏归档 / 持久化）。
- bgm 对象生命周期：编排器创建并注入匹配管道（只读），标记阶段用完整实例（写）
"""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any

from ...core.logging import get_sync_run_id, logger, sync_log_context
from ...models.sync import CustomItem, SyncResponse
from .context import ExecutionContext
from .match_trace import MatchTrace
from .retry import MARK_QUEUED

if TYPE_CHECKING:
    from . import SyncService


class SyncOrchestrator:
    """同步编排器：请求处理 → 匹配 → 集数解析 → 标记 → 持久化"""

    def __init__(self, sync_service: SyncService) -> None:
        self._sync = sync_service

    # ------------------------------------------------------------------
    # 入口（保留原 sync_custom_item 的 run_id 上下文管理）
    # ------------------------------------------------------------------

    def sync_custom_item(
        self, item: CustomItem, source: str = "custom"
    ) -> SyncResponse:
        """同步自定义项目（编排器入口）"""
        existing_run_id = get_sync_run_id()
        if existing_run_id:
            return self._sync_impl(item, source)
        inline_id = self._sync._allocate_inline_run_id()
        with sync_log_context(inline_id):
            return self._sync_impl(item, source)

    def _sync_impl(self, item: CustomItem, source: str = "custom") -> SyncResponse:
        actual_source = item.source if item.source else source
        status_holder: list[str] = ["error"]
        try:
            logger.info(
                f"同步开始: {item.title} S{item.season:02d}E{item.episode:02d} ({actual_source})"
            )
            return self._body(item, source, actual_source, status_holder)
        finally:
            logger.info(f"同步结束: status={status_holder[0]}")

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def _body(
        self,
        item: CustomItem,
        source: str,
        actual_source: str,
        status_holder: list[str],
    ) -> SyncResponse:
        try:
            trace: MatchTrace | None = None
            sync_action = (item.sync_action or "").strip().lower()

            # 1. mark_watching 短路径：movie / real_action 走剧场版标记
            if sync_action == "mark_watching":
                if item.media_type in ("movie", "real_action"):
                    result = self._sync.sync_movie_watching(item, source)
                    status_holder[0] = result.status
                    return result
                result = SyncResponse(
                    status="ignored",
                    message="仅支持剧场版标记在看",
                )
                status_holder[0] = result.status
                return result

            logger.info(f"接收到同步请求：{item}")
            from . import notification_service

            notification_service.notify("request_received", item, actual_source)

            # 2. 参数校验
            validation_error = self._sync._normalize_custom_item_params(
                item, actual_source
            )
            if validation_error is not None:
                status_holder[0] = validation_error.status
                return validation_error

            # 3-6. 匹配（构建 trace + receive step + API 不可达短路 + 管道 + 失败处理）
            subject_id, is_season_matched_id, error_response, trace = (
                self._match_subject(item, actual_source)
            )
            if error_response is not None:
                status_holder[0] = error_response.status
                return error_response

            # 7. bgm 实例校验（匹配成功但 bgm 缺失，标记阶段无法继续）
            bgm = self._sync._get_bangumi_api_for_user(item.user_name)
            if not bgm:
                logger.error(f"无法为用户 {item.user_name} 创建bangumi API实例")
                status_holder[0] = "error"
                return SyncResponse(status="error", message="bangumi配置错误")

            # 8. 匹配歧义通知（trace.is_ambiguous 由 APISearchStep 设置，编排器据此发通知）
            self._sync._maybe_notify_match_ambiguous(trace, item, actual_source)

            # 9. 集数解析 → 跨季回退 → 标记 → 结果结算（执行阶段管线）
            exec_ctx = self._run_execution_pipeline(
                bgm, item, actual_source, subject_id, is_season_matched_id, trace
            )

            # 10. 终态分支（error / miss 短路；queued / success 继续收尾）
            terminal = exec_ctx.terminal
            if terminal is not None:
                stage, outcome = terminal
                if stage == "episode_resolve" and outcome.status == "error":
                    # 认证失败等：走统一异常处理（保持原 raise 语义）
                    err = ValueError(
                        (outcome.error_detail or {}).get("message", outcome.reason)
                    )
                    return self._handle_sync_exception(
                        item, source, actual_source, trace, err, status_holder
                    )
                if stage == "cross_season" and outcome.status == "miss":
                    # 集数不存在：不发 bangumi_id_found（旧实现 resolve 成功后
                    # 才通知，避免"已找到"与失败通知的矛盾序列）
                    return self._handle_episode_not_found(
                        item, actual_source, trace, subject_id, status_holder
                    )
                if stage == "sync_action" and outcome.status == "error":
                    status_holder[0] = "error"
                    return SyncResponse(
                        status="error",
                        message=(outcome.error_detail or {}).get(
                            "message", outcome.reason
                        ),
                    )

            # 11. bangumi_id_found 通知：使用解析后的正确季度 ID（cross_season
            #     改选后 exec_ctx.bgm_se_id 可能是跨季条目，subject_id 仅为匹配阶段结果）
            bgm_se_id = exec_ctx.bgm_se_id or subject_id
            bgm_title = exec_ctx.bgm_title or self._fetch_bgm_title(bgm, str(bgm_se_id))
            notification_service.notify(
                "bangumi_id_found",
                item,
                actual_source,
                subject_id=bgm_se_id,
                bgm_title=bgm_title,
            )

            logger.debug(
                f"bgm: 查询到 {bgm_title or item.title} (https://bgm.tv/subject/{bgm_se_id}) "
                f"S{item.season:02d}E{item.episode:02d} (https://bgm.tv/ep/{exec_ctx.bgm_ep_id})"
            )

            # 12. API 不可达已入队：queued 收尾（无 result step）
            if exec_ctx.mark_status == MARK_QUEUED:
                return self._handle_queued(
                    item,
                    actual_source,
                    bgm_se_id,
                    exec_ctx.bgm_ep_id,
                    bgm_title,
                    trace,
                    status_holder,
                )

            # 13. 标记成功收尾：通知 + 收藏归档 + 持久化
            return self._finalize_success(
                item,
                actual_source,
                bgm,
                bgm_se_id,
                exec_ctx.bgm_ep_id,
                bgm_title,
                exec_ctx.mark_status,
                exec_ctx.result_message,
                trace,
                status_holder,
            )
        except Exception as e:
            logger.error(f"自定义同步处理出错: {e}")
            return self._handle_sync_exception(
                item, source, actual_source, trace, e, status_holder
            )

    # ------------------------------------------------------------------
    # 匹配阶段（供 _body 和 sync_movie_watching 复用）
    # ------------------------------------------------------------------

    def _match_subject(
        self,
        item: CustomItem,
        actual_source: str,
    ) -> tuple[str | None, bool, SyncResponse | None, MatchTrace]:
        """运行匹配管道并处理失败。

        返回 (subject_id, is_season_matched_id, error_response, trace)：
        - 成功：(id, flag, None, trace)
        - 失败：(None, False, 应立即返回的 SyncResponse, trace)
        - API 不可达：(None, False, SyncResponse(ignored), trace)

        trace 始终非 None，包含匹配过程。
        """
        # 构建匹配追踪 + receive step
        trace = MatchTrace(
            request_title=item.title,
            request_ori_title=item.ori_title or "",
            request_season=item.season,
            request_episode=item.episode,
            request_media_type=item.media_type,
            request_release_date=item.release_date or "",
            request_sync_action=(item.sync_action or "").strip(),
            request_user_name=item.user_name,
            request_platform_hint=item.source or actual_source,
        )
        self._record_receive_step(trace, item, actual_source)

        # API 不可达短路（补发模式开启时跳过本轮匹配）
        bgm = self._sync._get_bangumi_api_for_user(item.user_name)
        unreachable_resp = self._check_api_unreachable(item, actual_source, bgm, trace)
        if unreachable_resp is not None:
            return None, False, unreachable_resp, trace

        # 运行匹配管道（阶段三：Normalize → CustomMapping → BangumiData → APISearch）
        subject_id, is_season_matched_id, subject_find_error = (
            self._sync._find_subject_id(item, trace=trace)
        )

        if subject_id:
            return subject_id, is_season_matched_id, None, trace

        # 匹配失败处理
        dummy_holder: list[str] = ["error"]
        error_resp = self._handle_match_failure(
            item, actual_source, trace, subject_find_error, dummy_holder
        )
        return None, False, error_resp, trace

    # ------------------------------------------------------------------
    # receive step 记录
    # ------------------------------------------------------------------

    @staticmethod
    def _record_receive_step(
        trace: MatchTrace, item: CustomItem, actual_source: str
    ) -> None:
        receive_step = trace.start_step("receive")
        receive_step.status = "hit"
        receive_step.reason = (
            f"{actual_source} 推送：{item.title} S{item.season:02d}E{item.episode:02d}"
        )
        receive_step.processed_payload = {
            "source": item.source or actual_source,
            "user_name": item.user_name,
            "title": item.title,
            "ori_title": item.ori_title,
            "season": item.season,
            "episode": item.episode,
            "media_type": item.media_type,
            "release_date": item.release_date,
            "sync_action": item.sync_action or "",
        }
        receive_step.raw_payload = item.raw_payload

    # ------------------------------------------------------------------
    # API 不可达短路
    # ------------------------------------------------------------------

    def _check_api_unreachable(
        self,
        item: CustomItem,
        actual_source: str,
        bgm: Any,
        trace: MatchTrace,
    ) -> SyncResponse | None:
        """API 不可达（TTL 内）时跳过本轮匹配，等待补发调度器恢复。

        仅补发模式（bangumi-replay enabled）开启时生效，关闭时保持原行为。
        """
        from . import is_replay_enabled

        if not (is_replay_enabled() and bgm and bgm.is_api_unreachable()):
            return None
        logger.warning(
            f"📚 Bangumi API 不可达，本轮跳过匹配: {item.title} "
            f"S{item.season:02d}E{item.episode:02d}（等待批量探测复位）"
        )
        trace.finish()
        try:
            from ..bangumi_replay_scheduler import bangumi_replay_scheduler

            bangumi_replay_scheduler.trigger_immediate_run()
        except Exception as e:
            logger.debug(f"📚 触发补发探测失败: {e}")
        return SyncResponse(
            status="ignored",
            message="Bangumi API 不可达：跳过本轮匹配，等待恢复后由补发调度器补发",
        )

    # ------------------------------------------------------------------
    # 匹配失败处理（原 _find_matching_subject L1014-1071）
    # ------------------------------------------------------------------

    def _handle_match_failure(
        self,
        item: CustomItem,
        actual_source: str,
        trace: MatchTrace,
        subject_find_error: str,
        status_holder: list[str],
    ) -> SyncResponse:
        """统一处理匹配失败：补 result step → 写 DB → 发通知 → 沉淀候选"""
        # 补 result step 供前端展示（_find_subject_id 已 finish trace，但未追加 result step）
        if not trace.steps or trace.steps[-1].stage != "result":
            result_step = trace.start_step("result")
            result_step.status = "miss"
            result_step.reason = (
                f"同步失败：未找到匹配的番剧 · {subject_find_error or '无候选'}"
            )
            result_step.processed_payload = {
                "status": "error",
                "episode": f"S{item.season:02d}E{item.episode:02d}",
                "subject_id": "",
                "episode_id": "",
                "subject_url": "",
                "episode_url": "",
                "bgm_title": "",
                "message": self._sync._format_subject_not_found_message(
                    item, subject_find_error
                ),
            }
            trace.final_status = "error"
            trace.final_message = "未找到匹配的番剧"
        trace.finish()

        sync_record_id = self._persist_sync_record(
            trace,
            item,
            actual_source,
            status="error",
            subject_id=None,
            episode_id=None,
            message=self._sync._format_subject_not_found_message(
                item, subject_find_error
            ),
        )

        from . import notification_service

        notification_service.notify(
            "anime_not_found",
            item,
            actual_source,
            in_app_ref_id=sync_record_id,
            error_message="未找到匹配的番剧",
        )
        # 匹配失败且有候选时，沉淀到 pending_candidates 供用户手动确认
        self._sync._sediment_pending_candidate(
            item, actual_source, trace, sync_record_id=sync_record_id
        )
        status_holder[0] = "error"
        return SyncResponse(status="error", message="未找到匹配的番剧")

    # ------------------------------------------------------------------
    # 执行阶段管线（episode_resolve → cross_season → sync_action → result）
    # ------------------------------------------------------------------

    def _run_execution_pipeline(
        self,
        bgm: Any,
        item: CustomItem,
        actual_source: str,
        subject_id: str,
        is_season_matched_id: bool,
        trace: MatchTrace,
    ) -> ExecutionContext:
        """装配并运行执行阶段管线，返回携带终态信息的 ExecutionContext

        terminal = (stage, outcome)：提前终止（error / miss / queued）。
        None 表示全部 step 执行完（含 result step）。
        """
        from .pipeline import SyncPipeline
        from .steps import (
            CrossSeasonStep,
            EpisodeResolveStep,
            ResultStep,
            SyncActionStep,
        )

        exec_ctx = ExecutionContext(
            item=item,
            bgm=bgm,
            trace=trace,
            service=self._sync,
            actual_source=actual_source,
            subject_id=subject_id,
            is_season_matched_id=is_season_matched_id,
        )
        pipeline = SyncPipeline(
            [
                EpisodeResolveStep(),
                CrossSeasonStep(),
                SyncActionStep(),
                ResultStep(),
            ]
        )
        exec_ctx.terminal = pipeline.run(exec_ctx)
        return exec_ctx

    @staticmethod
    def _fetch_bgm_title(bgm: Any, bgm_se_id: str) -> str:
        """获取条目标题（queued 分支 ResultStep 未执行时由编排器补取）"""
        try:
            subject_info = bgm.get_subject(bgm_se_id)
            if subject_info:
                return subject_info.get("name_cn") or subject_info.get("name") or ""
        except Exception:
            logger.debug(f"获取条目标题失败: {bgm_se_id}", exc_info=True)
        return ""

    # ------------------------------------------------------------------
    # 集数未找到处理（原 L1508-1559）
    # ------------------------------------------------------------------

    def _handle_episode_not_found(
        self,
        item: CustomItem,
        actual_source: str,
        trace: MatchTrace,
        subject_id: str,
        status_holder: list[str],
    ) -> SyncResponse:
        """集数解析失败：写 error 记录 + 发 episode_not_found 通知"""
        result_step = trace.start_step("result")
        result_step.status = "miss"
        result_step.subject_id = str(subject_id)
        result_step.reason = (
            f"同步失败：未找到对应的剧集 · https://bgm.tv/subject/{subject_id}"
        )
        result_step.processed_payload = {
            "status": "error",
            "episode": f"S{item.season:02d}E{item.episode:02d}",
            "subject_id": str(subject_id),
            "episode_id": "",
            "subject_url": f"https://bgm.tv/subject/{subject_id}",
            "episode_url": "",
            "bgm_title": "",
            "message": "未找到对应的剧集（不存在或集数过多）",
        }
        trace.final_status = "error"
        trace.final_message = "未找到对应的剧集（不存在或集数过多）"
        trace.finish()

        record_id = self._persist_sync_record(
            trace,
            item,
            actual_source,
            status="error",
            subject_id=str(subject_id),
            episode_id=None,
            message="未找到对应的剧集（不存在或集数过多）",
        )

        from . import notification_service

        notification_service.notify(
            "episode_not_found",
            item,
            actual_source,
            in_app_ref_id=record_id,
            subject_id=subject_id,
            error_message="不存在或集数过多",
        )
        status_holder[0] = "error"
        return SyncResponse(status="error", message="未找到对应的剧集")

    # ------------------------------------------------------------------
    # 标记成功收尾（ResultStep 已结算 final_*，此处只做副作用与持久化）
    # ------------------------------------------------------------------

    def _finalize_success(
        self,
        item: CustomItem,
        actual_source: str,
        bgm: Any,
        bgm_se_id: str,
        bgm_ep_id: str,
        bgm_title: str,
        mark_status: int,
        result_message: str,
        trace: MatchTrace,
        status_holder: list[str],
    ) -> SyncResponse:
        """标记成功：通知 + 收藏归档 + 持久化 + 组装 SyncResponse"""
        # 通知 mark_success/mark_skipped（result_message 已由 ResultStep 结算）
        self._sync._apply_sync_status(
            item, actual_source, bgm_se_id, bgm_ep_id, bgm_title, mark_status
        )

        self._sync._mark_subject_completed_if_needed(item, bgm, bgm_se_id, bgm_title)

        trace.finish()

        self._persist_sync_record(
            trace,
            item,
            actual_source,
            status="success",
            subject_id=bgm_se_id,
            episode_id=bgm_ep_id,
            message=result_message,
            bgm_title=bgm_title,
        )

        result = SyncResponse(
            status="success",
            message=result_message,
            data={
                "title": item.title,
                "bgm_title": bgm_title,
                "season": item.season,
                "episode": item.episode,
                "subject_id": bgm_se_id,
                "episode_id": bgm_ep_id,
                "match_method": trace.final_match_method,
                "match_trace": trace.to_dict(),
                "match_score": trace.final_score,
                "match_platform": self._sync._extract_matched_platform(
                    trace, bgm_se_id
                ),
            },
        )
        status_holder[0] = result.status
        return result

    # ------------------------------------------------------------------
    # queued 处理（原 L1605-1683）
    # ------------------------------------------------------------------

    def _handle_queued(
        self,
        item: CustomItem,
        actual_source: str,
        bgm_se_id: str,
        bgm_ep_id: str,
        bgm_title: str,
        trace: MatchTrace,
        status_holder: list[str],
    ) -> SyncResponse:
        """API 不可达已入队：记录为 queued + 发 sync_queued 通知

        sync_action step 已由 SyncPipeline 记录（hit），此处收口 final_*。
        """
        from . import notification_service

        trace.final_action = "queued"
        trace.final_status = "queued"
        trace.final_message = "API 不可达，已入待同步队列"
        trace.finish()

        queued_message = (
            f"📚 Bangumi API 不可达，已入待同步队列："
            f"{item.title} S{item.season:02d}E{item.episode:02d} → "
            f"subject/{bgm_se_id}" + (f" · ep/{bgm_ep_id}" if bgm_ep_id else "")
        )
        queued_record_id = self._persist_sync_record(
            trace,
            item,
            actual_source,
            status="queued",
            subject_id=bgm_se_id,
            episode_id=bgm_ep_id,
            message=queued_message,
            bgm_title=bgm_title,
        )
        # 回填 sync_record_id 到 pending_sync_queue 行
        if queued_record_id:
            try:
                from . import database_manager

                database_manager.link_pending_sync_to_record(
                    user_name=item.user_name,
                    subject_id=bgm_se_id,
                    episode_id=bgm_ep_id or None,
                    source=actual_source,
                    sync_record_id=queued_record_id,
                )
            except Exception as link_err:
                logger.warning(f"📚 回填 sync_record_id 失败（不影响入队）: {link_err}")
        try:
            notification_service.notify(
                "sync_queued",
                item,
                actual_source,
                subject_id=str(bgm_se_id),
                episode_id=str(bgm_ep_id) if bgm_ep_id else "",
                bgm_title=bgm_title,
            )
        except Exception:
            pass
        status_holder[0] = "queued"
        return SyncResponse(
            status="queued",
            message=queued_message,
            data={
                "title": item.title,
                "bgm_title": bgm_title,
                "season": item.season,
                "episode": item.episode,
                "subject_id": bgm_se_id,
                "episode_id": bgm_ep_id,
            },
        )

    # ------------------------------------------------------------------
    # 异常处理（原 L1778-1811）
    # ------------------------------------------------------------------

    def _handle_sync_exception(
        self,
        item: CustomItem,
        source: str,
        actual_source: str,
        trace: MatchTrace | None,
        e: Exception,
        status_holder: list[str],
    ) -> SyncResponse:
        """同步异常：写 error 记录 + 发 mark_failed 通知"""
        from . import database_manager, notification_service

        record_id = database_manager.log_sync_record(
            user_name=item.user_name,
            title=item.title,
            ori_title=item.ori_title or "",
            season=item.season,
            episode=item.episode,
            status="error",
            message=str(e),
            source=actual_source,
            media_type=item.media_type,
            match_method=trace.final_match_method if trace else "",
            match_score=trace.final_score if trace else None,
            match_platform=self._sync._extract_matched_platform(trace, None)
            if trace
            else "",
            match_trace=trace.to_dict() if trace else None,
        )

        notification_service.notify(
            "mark_failed",
            item,
            actual_source,
            in_app_ref_id=record_id,
            error_message=str(e),
            error_type="sync_error",
            additional_info=f"完整错误信息: {traceback.format_exc()}",
        )

        status_holder[0] = "error"
        return SyncResponse(status="error", message=f"处理失败: {str(e)}")

    # ------------------------------------------------------------------
    # 统一持久化（收口 trace→DB 的 finish+to_dict+log 样板）
    # ------------------------------------------------------------------

    def _persist_sync_record(
        self,
        trace: MatchTrace,
        item: CustomItem,
        actual_source: str,
        *,
        status: str,
        subject_id: str | None,
        episode_id: str | None,
        message: str,
        bgm_title: str = "",
    ) -> int:
        """统一收口 trace→DB 的 finish+to_dict+log 样板（原 5 处重复）

        trace 已由调用方 finish，此处仅负责 to_dict + log_sync_record。
        返回 sync_record_id（供通知 in_app_ref_id 与候选沉淀使用）。
        """
        from . import database_manager

        match_platform = self._sync._extract_matched_platform(trace, subject_id)
        return database_manager.log_sync_record(
            user_name=item.user_name,
            title=item.title,
            ori_title=item.ori_title or "",
            season=item.season,
            episode=item.episode,
            subject_id=subject_id,
            episode_id=episode_id,
            status=status,
            message=message,
            source=actual_source,
            media_type=item.media_type,
            bgm_title=bgm_title,
            match_method=trace.final_match_method,
            match_score=trace.final_score,
            match_platform=match_platform,
            match_trace=trace.to_dict(),
        )
