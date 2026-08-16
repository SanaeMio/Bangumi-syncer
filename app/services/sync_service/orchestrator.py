"""同步编排器（阶段四）

统一编排：请求处理 → 匹配 → 集数解析 → 标记 → 持久化。

剥离原 sync_service._find_matching_subject / _sync_custom_item_body 的耦合：
- DB 写入、通知、调度器触发统一收口到本编排器
- trace→DB 的 finish+to_dict+log 样板统一收口到 _persist_sync_record
- episode_resolve / cross_season 保留原逻辑（作为编排器方法，不作为管道 step，
  避免 cross_season 修改 trace.final_* 与 pipeline._record_trace 冲突）
- bgm 对象生命周期：编排器创建并注入匹配管道（只读），标记阶段用完整实例（写）
"""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any

from ...core.logging import get_sync_run_id, logger, sync_log_context
from ...models.sync import CustomItem, SyncResponse
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

            # 9. 集数解析（episode_resolve + cross_season，保留原逻辑）
            bgm_se_id, bgm_ep_id = self._resolve_episode_with_cross_season(
                bgm, item, subject_id, is_season_matched_id, trace
            )

            if not bgm_ep_id:
                # 集数不存在：不发 bangumi_id_found（旧实现 resolve 成功后
                # 才通知，避免"已找到"与失败通知的矛盾序列）
                return self._handle_episode_not_found(
                    item, actual_source, trace, subject_id, status_holder
                )

            # 10. bangumi_id_found 通知：使用解析后的正确季度 ID（cross_season
            #     改选后 bgm_se_id 可能是跨季条目，subject_id 仅为匹配阶段结果）
            subject_info = bgm.get_subject(bgm_se_id)
            bgm_title = ""
            if subject_info:
                bgm_title = (
                    subject_info.get("name_cn") or subject_info.get("name") or ""
                )
            notification_service.notify(
                "bangumi_id_found",
                item,
                actual_source,
                subject_id=bgm_se_id,
                bgm_title=bgm_title,
            )

            logger.debug(
                f"bgm: 查询到 {bgm_title or item.title} (https://bgm.tv/subject/{bgm_se_id}) "
                f"S{item.season:02d}E{item.episode:02d} (https://bgm.tv/ep/{bgm_ep_id})"
            )

            # 12. 标记为看过
            return self._mark_and_persist(
                item,
                actual_source,
                bgm,
                bgm_se_id,
                bgm_ep_id,
                bgm_title,
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
    # 集数解析 + 跨季链（保留原 _sync_custom_item_body L1380-1559 逻辑）
    # ------------------------------------------------------------------

    def _resolve_episode_with_cross_season(
        self,
        bgm: Any,
        item: CustomItem,
        subject_id: str,
        is_season_matched_id: bool,
        trace: MatchTrace,
    ) -> tuple[str, str]:
        """集数解析 + 跨季链回退。返回 (bgm_se_id, bgm_ep_id)。

        - episode_resolve step：调用 _resolve_season_episode
        - 若未命中 ep_id：cross_season step 调用 find_episode_across_seasons
        - cross_season 命中时修改 subject_id 并标注 trace.final_*
        """
        ep_resolve_step = trace.start_step("episode_resolve")
        resolve_input = {
            "input_subject_id": str(subject_id),
            "input_is_season_id": bool(is_season_matched_id),
            "request_season": item.season,
            "request_episode": item.episode,
            "media_type": item.media_type,
            "release_date": item.release_date or "",
        }

        try:
            bgm_se_id, bgm_ep_id = self._sync._resolve_season_episode(
                bgm, item, subject_id, is_season_matched_id
            )
        except ValueError as ve:
            if "认证失败" in str(ve) or "access_token" in str(ve):
                ep_resolve_step.status = "error"
                ep_resolve_step.reason = f"认证失败: {ve}"
                ep_resolve_step.processed_payload = {
                    **resolve_input,
                    "output_subject_id": "",
                    "output_episode_id": "",
                    "changed": False,
                    "error": str(ve),
                }
                raise
            else:
                raise ve

        changed = str(bgm_se_id) != str(subject_id) if bgm_se_id else False
        if bgm_ep_id:
            ep_resolve_step.status = "hit"
            ep_resolve_step.subject_id = str(bgm_se_id)
            ep_resolve_step.reason = (
                f"集数解析：subject={bgm_se_id} episode={item.episode} → "
                f"ep_id={bgm_ep_id}"
            )
            ep_resolve_step.processed_payload = {
                **resolve_input,
                "output_subject_id": str(bgm_se_id),
                "output_episode_id": str(bgm_ep_id),
                "changed": changed,
                "subject_url": f"https://bgm.tv/subject/{bgm_se_id}",
                "episode_url": f"https://bgm.tv/ep/{bgm_ep_id}",
            }
        else:
            ep_resolve_step.status = "miss"
            ep_resolve_step.reason = (
                f"集数解析未命中：subject={bgm_se_id} episode={item.episode}"
            )
            ep_resolve_step.processed_payload = {
                **resolve_input,
                "output_subject_id": str(bgm_se_id) if bgm_se_id else "",
                "output_episode_id": "",
                "changed": changed,
                "error": "未找到对应集数",
            }

        if bgm_ep_id:
            return bgm_se_id, bgm_ep_id

        # 跨季链回退
        return self._cross_season_fallback(bgm, item, subject_id, bgm_se_id, trace)

    def _cross_season_fallback(
        self,
        bgm: Any,
        item: CustomItem,
        subject_id: str,
        bgm_se_id: str,
        trace: MatchTrace,
    ) -> tuple[str, str]:
        """跨季链查找：通过前传/续集链在关联季条目中查找含目标 sort 的章节"""
        cross_step = trace.start_step("cross_season")
        cross_input_subject = str(subject_id)
        chain_pick = None
        try:
            chain_pick = bgm.find_episode_across_seasons(subject_id, item.episode)
        except Exception as e:
            logger.debug(f"关联季条目链查找异常: {e}")

        if chain_pick:
            chain_subject_id, chain_ep_id = chain_pick
            prev_subject_id = subject_id
            logger.debug(
                f"通过关联季条目链找到目标集: 原 subject_id={prev_subject_id}, "
                f"改选 subject_id={chain_subject_id}, ep_id={chain_ep_id}, "
                f"目标 episode={item.episode}"
            )
            cross_step.status = "hit"
            cross_step.subject_id = str(chain_subject_id)
            cross_step.reason = (
                f"跨季链查找命中：原 subject_id={prev_subject_id} → "
                f"chain_subject_id={chain_subject_id}, "
                f"ep_id={chain_ep_id} (目标 episode={item.episode})"
            )
            cross_step.processed_payload = {
                "input_subject_id": cross_input_subject,
                "output_subject_id": str(chain_subject_id),
                "output_episode_id": str(chain_ep_id),
                "target_episode": item.episode,
                "changed": str(prev_subject_id) != str(chain_subject_id),
                "subject_url": f"https://bgm.tv/subject/{chain_subject_id}",
                "episode_url": f"https://bgm.tv/ep/{chain_ep_id}",
            }
            trace.final_subject_id = str(chain_subject_id)
            trace.final_episode_id = str(chain_ep_id)
            # 跨季链命中：粗粒度记 archive，细粒度记 cross_season_chain
            trace.final_match_method = "archive"
            trace.final_match_method_detail = "cross_season_chain"
            return chain_subject_id, chain_ep_id

        logger.error(
            f"bgm: {subject_id=} {item.season=} {item.episode=}, 不存在或集数过多，跳过"
        )
        cross_step.status = "miss"
        cross_step.reason = f"跨季链查找未命中含 sort={item.episode} 的季条目"
        cross_step.processed_payload = {
            "input_subject_id": cross_input_subject,
            "output_subject_id": "",
            "output_episode_id": "",
            "target_episode": item.episode,
            "changed": False,
            "error": f"未找到含 sort={item.episode} 的关联季条目",
        }
        return bgm_se_id, ""

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
    # 标记 + 持久化（原 L1583-1777）
    # ------------------------------------------------------------------

    def _mark_and_persist(
        self,
        item: CustomItem,
        actual_source: str,
        bgm: Any,
        bgm_se_id: str,
        bgm_ep_id: str,
        bgm_title: str,
        trace: MatchTrace,
        status_holder: list[str],
    ) -> SyncResponse:
        """标记为看过 + 持久化同步记录"""
        sync_action_step = trace.start_step("sync_action")
        try:
            mark_status = self._sync._retry_mark_episode(
                bgm,
                bgm_se_id,
                bgm_ep_id,
                queue_payload=item.model_dump(),
            )
        except ValueError as ve:
            if "认证失败" in str(ve) or "access_token" in str(ve):
                sync_action_step.status = "error"
                sync_action_step.reason = f"认证失败: {ve}"
                status_holder[0] = "error"
                return SyncResponse(status="error", message=str(ve))
            raise ve

        # API 不可达：已入待同步队列
        if mark_status == MARK_QUEUED:
            return self._handle_queued(
                item,
                actual_source,
                bgm_se_id,
                bgm_ep_id,
                bgm_title,
                trace,
                sync_action_step,
                status_holder,
            )

        sync_action_step.status = "hit"
        sync_action_step.subject_id = str(bgm_se_id)
        action_label = {
            0: "已在看/看过（无变更）",
            1: "已标记为看过",
            2: "已添加收藏",
        }.get(mark_status, f"mark_status={mark_status}")
        sync_action_step.reason = (
            f"mark_episode_watched 返回 {mark_status}（{action_label}）"
        )
        trace.final_action = str(mark_status)

        result_message = self._sync._apply_sync_status(
            item, actual_source, bgm_se_id, bgm_ep_id, bgm_title, mark_status
        )

        self._sync._mark_subject_completed_if_needed(item, bgm, bgm_se_id, bgm_title)

        # 回填最终剧集 ID 到 trace
        trace.final_episode_id = str(bgm_ep_id)

        # result step
        result_step = trace.start_step("result")
        result_step.status = "hit"
        result_step.subject_id = str(bgm_se_id)
        result_step.reason = (
            f"{result_message} · https://bgm.tv/subject/{bgm_se_id}"
            + (f" · https://bgm.tv/ep/{bgm_ep_id}" if bgm_ep_id else "")
        )
        result_step.processed_payload = {
            "status": "success",
            "episode": f"S{item.season:02d}E{item.episode:02d}",
            "subject_id": str(bgm_se_id),
            "episode_id": str(bgm_ep_id) if bgm_ep_id else "",
            "subject_url": f"https://bgm.tv/subject/{bgm_se_id}",
            "episode_url": f"https://bgm.tv/ep/{bgm_ep_id}" if bgm_ep_id else "",
            "bgm_title": bgm_title,
            "message": result_message,
        }
        trace.final_status = "success"
        trace.final_message = result_message
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
        sync_action_step: Any,
        status_holder: list[str],
    ) -> SyncResponse:
        """API 不可达已入队：记录为 queued + 发 sync_queued 通知"""
        from . import notification_service

        sync_action_step.status = "hit"
        sync_action_step.subject_id = str(bgm_se_id)
        sync_action_step.reason = "API 不可达，已入待同步队列，等待补发调度器重放"
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
