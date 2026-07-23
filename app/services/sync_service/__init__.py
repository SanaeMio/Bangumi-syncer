"""
同步服务模块
"""

from __future__ import annotations

# time/asyncio 重新导出以兼容测试 patch（app.services.sync_service.time.sleep 等）
import asyncio  # noqa: F401
import threading
import time  # noqa: F401
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from rapidfuzz import fuzz

from ...core.config import config_manager
from ...core.database import database_manager
from ...core.logging import (
    get_sync_run_id,
    logger,
    new_inline_sync_run_id,
    sync_log_context,
)
from ...models.sync import CustomItem, SyncResponse
from ...utils.bangumi_api import BangumiApi
from ...utils.bangumi_data import BangumiData, bangumi_data
from ...utils.media_type_detector import detect_media_type
from ...utils.notifier import send_notify
from ..mapping_service import mapping_service
from .match_trace import MatchCandidate, MatchTrace
from .retry import RetryMixin
from .season_info import SeasonInfoMixin
from .task_manager import TaskManagerMixin
from .title_normalize import TitleNormalizeMixin


class SyncService(TaskManagerMixin, RetryMixin, SeasonInfoMixin, TitleNormalizeMixin):
    """同步服务"""

    def __init__(self):
        self._bangumi_data_cache: BangumiData | None = None
        self._cached_mappings: dict[str, str] = {}
        self._mapping_file_path: str | None = None
        self._last_modified_time: float = 0
        # 线程池大小从配置读取
        try:
            scheduler_cfg = config_manager.get_scheduler_config()
            max_workers = max(1, int(scheduler_cfg.get("max_concurrent_syncs", 3)))
        except (TypeError, ValueError, KeyError):
            max_workers = 3
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="sync_worker"
        )
        # 同步任务状态跟踪
        self._tasks_lock = threading.Lock()
        self._sync_tasks = {}
        self._task_counter = 0

    def shutdown(self) -> None:
        """关闭线程池，等待正在执行的任务完成"""
        self._executor.shutdown(wait=True)

    # ------------------------------------------------------------------
    # 同步记录查询（API 层通过本服务访问数据库，避免跨层直访）
    # ------------------------------------------------------------------

    def get_sync_records(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        user_name: str | None = None,
        source: str | None = None,
        source_prefix: str | None = None,
        match_method: str | None = None,
        match_platform: str | None = None,
        skip_count: bool = False,
    ) -> dict[str, Any]:
        """获取同步记录列表"""
        return database_manager.get_sync_records(
            limit=limit,
            offset=offset,
            status=status,
            user_name=user_name,
            source=source,
            source_prefix=source_prefix,
            match_method=match_method,
            match_platform=match_platform,
            skip_count=skip_count,
        )

    def get_sync_record_by_id(self, record_id: int) -> dict[str, Any] | None:
        """根据 ID 获取单个同步记录"""
        return database_manager.get_sync_record_by_id(record_id)

    def get_match_records(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        match_method: str | None = None,
        match_platform: str | None = None,
    ) -> dict[str, Any]:
        """获取匹配记录列表（含匹配追踪字段）"""
        return database_manager.get_match_records(
            limit=limit,
            offset=offset,
            status=status,
            match_method=match_method,
            match_platform=match_platform,
        )

    # ------------------------------------------------------------------
    # 待确认候选（候选沉淀）
    # ------------------------------------------------------------------

    def get_pending_candidates(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        """获取待确认候选列表"""
        return database_manager.get_pending_candidates(
            limit=limit, offset=offset, status=status
        )

    def get_pending_candidate_by_id(self, candidate_id: int) -> dict[str, Any] | None:
        """获取单条待确认候选详情"""
        return database_manager.get_pending_candidate_by_id(candidate_id)

    def confirm_pending_candidate(
        self, candidate_id: int, subject_id: str
    ) -> tuple[bool, str]:
        """确认待确认候选：写入自定义映射并标记为已确认

        返回 (success, message)。映射写入采用读全量→合并→写回的模式，
        避免覆盖已有映射。
        """
        record = database_manager.get_pending_candidate_by_id(candidate_id)
        if not record:
            return False, "候选记录不存在"
        if record.get("status") != "pending":
            return False, f"候选已处理（状态：{record.get('status')}）"

        title = record.get("request_title", "")
        season = int(record.get("request_season") or 1)
        if not title or not subject_id:
            return False, "标题或 subject_id 为空"

        # 读全量映射 → 合并新条目 → 写回
        all_mappings = mapping_service.get_all_mappings()
        if season > 1:
            all_mappings[title] = {"subject_id": str(subject_id), "season": season}
        else:
            all_mappings[title] = str(subject_id)
        if not mapping_service.update_custom_mappings(all_mappings):
            return False, "写入自定义映射失败"

        database_manager.update_pending_candidate_status(
            candidate_id, "confirmed", confirmed_subject_id=str(subject_id)
        )
        # 批量更新同 key 的其它 pending 行，避免残留（去重后通常无额外行）
        database_manager.resolve_similar_pending_candidates(
            request_title=title,
            request_season=season,
            user_name=record.get("user_name", ""),
            source=record.get("source", ""),
            status="confirmed",
            confirmed_subject_id=str(subject_id),
            exclude_id=candidate_id,
        )
        return True, f"已确认并写入映射：{title} → subject/{subject_id}"

    def reject_pending_candidate(self, candidate_id: int) -> tuple[bool, str]:
        """拒绝待确认候选"""
        if not database_manager.update_pending_candidate_status(
            candidate_id, "rejected"
        ):
            return False, "候选记录不存在或已处理"
        return True, "已忽略"

    def delete_pending_candidate(self, candidate_id: int) -> tuple[bool, str]:
        """删除待确认候选"""
        if not database_manager.delete_pending_candidate(candidate_id):
            return False, "候选记录不存在"
        return True, "已删除"

    @staticmethod
    def _collect_candidates_from_trace(trace: MatchTrace) -> list[dict[str, Any]]:
        """从 MatchTrace 各步骤中收集候选，去重并按 score 降序"""
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for step in trace.steps:
            for cand in step.candidates:
                if not cand.subject_id or cand.subject_id in seen:
                    continue
                seen.add(cand.subject_id)
                merged.append(cand.to_dict())
        merged.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return merged

    def _sediment_pending_candidate(
        self, item: CustomItem, actual_source: str, trace: MatchTrace
    ) -> None:
        """匹配失败时沉淀候选，供用户手动确认。

        仅当 trace 中存在候选时才写入 pending_candidates 表，
        并触发 pending_candidate 通知提醒用户前往 WebUI 确认。
        """
        candidates = self._collect_candidates_from_trace(trace)
        if not candidates:
            return
        try:
            database_manager.log_pending_candidate(
                request_title=item.title,
                request_ori_title=item.ori_title or "",
                request_season=item.season,
                request_episode=item.episode,
                user_name=item.user_name,
                source=actual_source,
                candidates=candidates,
                trace=trace.to_dict(),
            )
        except Exception as e:
            logger.warning(f"沉淀待确认候选失败（不影响主流程）: {e}")
            return

        # 沉淀成功后触发通知（不影响主流程）
        try:
            top = candidates[0] if candidates else {}
            send_notify(
                "pending_candidate",
                item,
                actual_source,
                candidates_count=len(candidates),
                top_candidate_id=str(top.get("subject_id", "")),
                top_candidate_name=top.get("name_cn") or top.get("name") or "",
            )
        except Exception as e:
            logger.warning(f"发送候选待确认通知失败（不影响主流程）: {e}")

    def test_match(self, item: CustomItem) -> dict[str, Any]:
        """测试匹配过程，返回匹配追踪详情（不执行实际同步、不发通知、不写库）

        用于「匹配记录」页面的匹配测试面板，直观展示三段式匹配的完整过程。
        不写入 sync_records 表，避免污染 dashboard 统计与同步记录列表。
        """
        trace = MatchTrace(
            request_title=item.title,
            request_ori_title=item.ori_title or "",
            request_season=item.season,
            request_episode=item.episode,
            request_media_type=item.media_type,
            request_release_date=item.release_date or "",
            request_user_name=item.user_name,
            request_platform_hint=item.source or "test-match",
        )
        subject_id, is_season_matched_id, failure_detail = self._find_subject_id(
            item, trace=trace
        )
        trace.finish()

        return {
            "subject_id": subject_id,
            "is_season_matched_id": is_season_matched_id,
            "failure_detail": failure_detail,
            "trace": trace.to_dict(),
        }

    def update_sync_record_status(
        self, record_id: int, status: str, message: str = ""
    ) -> bool:
        """更新同步记录的状态"""
        return database_manager.update_sync_record_status(record_id, status, message)

    def get_sync_stats(self) -> dict[str, Any]:
        """获取同步统计信息"""
        return database_manager.get_sync_stats()

    def get_heatmap_stats(self) -> list[dict[str, Any]]:
        """获取热力图数据（过去365天每天同步数）"""
        return database_manager.get_heatmap_stats()

    def sync_movie_watching(
        self, item: CustomItem, source: str = "custom"
    ) -> SyncResponse:
        """剧场版：仅将 Bangumi 条目收藏标为「在看」，不解析章节、不点单集。"""
        try:
            actual_source = item.source if item.source else source
            logger.info(f"接收到剧场版在看请求：{item}")
            send_notify("request_received", item, actual_source)

            if not config_manager.get(
                "sync", "movie_playback_start_mark_watching", fallback=True
            ):
                return SyncResponse(
                    status="ignored",
                    message="已在配置中关闭剧场版播放开始标记在看",
                )

            if item.media_type not in ("movie", "real_action"):
                return SyncResponse(
                    status="ignored", message="仅剧场版/真人电影支持播放开始标记在看"
                )

            if not item.title:
                logger.error("同步名称为空，跳过")
                return SyncResponse(status="error", message="同步名称为空")

            if not (
                perm_ok := self._check_user_permission(item.user_name, actual_source)
            )[0]:
                return SyncResponse(status="error", message=perm_ok[1])

            if self._is_title_blocked(item.title, item.ori_title):
                return SyncResponse(
                    status="ignored", message="番剧标题包含屏蔽关键词，跳过同步"
                )

            subject_id, _, error_response, trace = self._find_matching_subject(
                item, actual_source
            )
            if not subject_id:
                return error_response or SyncResponse(
                    status="error", message="未找到匹配的番剧"
                )

            bgm = self._get_bangumi_api_for_user(item.user_name)
            if not bgm:
                logger.error(f"无法为用户 {item.user_name} 创建bangumi API实例")
                return SyncResponse(status="error", message="bangumi配置错误")

            send_notify(
                "bangumi_id_found", item, actual_source, subject_id=str(subject_id)
            )

            try:
                mark_st = bgm.ensure_subject_watching(str(subject_id))
            except ValueError as ve:
                if "认证失败" in str(ve) or "access_token" in str(ve):
                    return SyncResponse(status="error", message=str(ve))
                raise ve

            if mark_st == 0:
                result_message = "条目已在看或已看过，无需变更"
            else:
                result_message = "播放开始：条目标记为在看"

            logger.info(
                f"bgm: {item.title} {result_message} https://bgm.tv/subject/{subject_id}"
            )

            database_manager.log_sync_record(
                user_name=item.user_name,
                title=item.title,
                ori_title=item.ori_title or "",
                season=item.season,
                episode=item.episode,
                subject_id=str(subject_id),
                episode_id=None,
                status="success",
                message=result_message,
                source=actual_source,
                media_type=item.media_type,
                match_method=trace.final_match_method if trace else "",
                match_score=trace.final_score if trace else None,
                match_platform=self._extract_matched_platform(trace, subject_id)
                if trace
                else "",
                match_trace=trace.to_dict() if trace else None,
            )

            return SyncResponse(
                status="success",
                message=result_message,
                data={
                    "title": item.title,
                    "season": item.season,
                    "episode": item.episode,
                    "subject_id": str(subject_id),
                },
            )
        except Exception as e:
            logger.error(f"剧场版在看处理出错: {e}")
            database_manager.log_sync_record(
                user_name=item.user_name if "item" in locals() else "unknown",
                title=item.title if "item" in locals() else "unknown",
                ori_title=item.ori_title if "item" in locals() else "",
                season=item.season if "item" in locals() else 0,
                episode=item.episode if "item" in locals() else 0,
                status="error",
                message=str(e),
                source=actual_source if "actual_source" in locals() else source,
                media_type=item.media_type if "item" in locals() else "movie",
                match_method=trace.final_match_method if trace else "",
                match_score=trace.final_score if trace else None,
                match_platform=self._extract_matched_platform(trace, None)
                if trace
                else "",
                match_trace=trace.to_dict() if trace else None,
            )
            send_notify(
                "mark_failed",
                item if "item" in locals() else None,
                actual_source if "actual_source" in locals() else source,
                error_message=str(e),
                error_type="sync_error",
                additional_info=f"完整错误信息: {traceback.format_exc()}",
            )
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")

    def _normalize_custom_item_params(
        self, item: CustomItem, actual_source: str = ""
    ) -> SyncResponse | None:
        """校验自定义条目参数。返回 SyncResponse 表示应立即返回该响应；None 表示校验通过。"""
        # 基本验证
        # 支持的媒体类型：episode/movie（原有）+ ova/oad/real_action（扩展）
        if item.media_type not in ("episode", "movie", "ova", "oad", "real_action"):
            logger.error(f"同步类型{item.media_type}不支持，跳过")
            return SyncResponse(
                status="error", message=f"同步类型{item.media_type}不支持"
            )

        if not item.title:
            logger.error("同步名称为空，跳过")
            return SyncResponse(status="error", message="同步名称为空")

        # episode/ova/oad/real_action 走剧集同步路径，不允许 season=0
        if (
            item.media_type in ("episode", "ova", "oad", "real_action")
            and item.season == 0
        ):
            logger.error("不支持SP标记同步，跳过")
            return SyncResponse(status="error", message="不支持SP标记同步")

        if item.episode == 0:
            logger.error(f"集数{item.episode}不能为0，跳过")
            return SyncResponse(status="error", message=f"集数{item.episode}不能为0")

        # 检查用户权限
        if not (perm_ok := self._check_user_permission(item.user_name, actual_source))[
            0
        ]:
            return SyncResponse(status="error", message=perm_ok[1])

        # 检查是否包含屏蔽关键词
        if self._is_title_blocked(item.title, item.ori_title):
            return SyncResponse(
                status="ignored", message="番剧标题包含屏蔽关键词，跳过同步"
            )

        return None

    def _find_matching_subject(
        self, item: CustomItem, actual_source: str
    ) -> tuple[str | None, bool, SyncResponse | None, MatchTrace]:
        """查找匹配的 Bangumi 条目。

        返回 (subject_id, is_season_matched_id, error_response, trace)：
        - 成功：(id, flag, None, trace)
        - 失败：(None, False, 应立即返回的 SyncResponse, trace)

        trace 始终非 None，包含三段式匹配的完整过程。
        trace 作为返回值沿调用链传递，避免并发场景下实例字段竞态。
        """
        # 始终创建匹配追踪，记录三段式匹配过程供「匹配记录」页面展示
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

        # 流水线第 1 步：接收请求（记录 sync 开始时的输入字段）
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

        # 查找番剧ID及其是否为特定季度ID的标记
        subject_id, is_season_matched_id, subject_find_error = self._find_subject_id(
            item, trace=trace
        )

        # 完成匹配追踪（trace 作为返回值沿调用链传递，避免并发竞态）
        # _find_subject_id 内部已对成功/失败分支调用 trace.finish()，
        # 此处仅对失败但未 finish 的情况补 finish + result step
        if subject_id:
            return subject_id, is_season_matched_id, None, trace

        # 流水线失败终止：未找到番剧（trace 已被 _find_subject_id finish，
        # 但未追加 result step，此处补一个 result step 供前端展示）
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
                "message": self._format_subject_not_found_message(
                    item, subject_find_error
                ),
            }
            trace.final_status = "error"
            trace.final_message = "未找到匹配的番剧"
        trace.finish()

        send_notify(
            "anime_not_found",
            item,
            actual_source,
            error_message="未找到匹配的番剧",
        )
        database_manager.log_sync_record(
            user_name=item.user_name,
            title=item.title,
            ori_title=item.ori_title or "",
            season=item.season,
            episode=item.episode,
            subject_id=None,
            episode_id=None,
            status="error",
            message=self._format_subject_not_found_message(item, subject_find_error),
            source=actual_source,
            media_type=item.media_type,
            match_method=trace.final_match_method,
            match_score=trace.final_score,
            match_platform=self._extract_matched_platform(trace, None),
            match_trace=trace.to_dict(),
        )
        # 匹配失败且有候选时，沉淀到 pending_candidates 供用户手动确认
        self._sediment_pending_candidate(item, actual_source, trace)
        return (
            None,
            False,
            SyncResponse(status="error", message="未找到匹配的番剧"),
            trace,
        )

    @staticmethod
    def _extract_matched_platform(trace: MatchTrace, subject_id: str | None) -> str:
        """从匹配追踪的命中候选中提取 Bangumi 条目 platform（TV/OVA/剧场版/日剧等）。

        优先查找与 subject_id 匹配的候选；找不到则取最后命中阶段的第一个候选。
        """
        if not subject_id or not trace:
            return ""
        target_id = str(subject_id)
        # 优先在命中阶段的候选中查找与 subject_id 匹配的条目
        for step in trace.steps:
            if step.status != "hit" or not step.subject_id:
                continue
            for cand in step.candidates:
                if cand.subject_id == target_id:
                    return cand.platform
            # 命中阶段但候选中没有完全匹配的 ID，取该阶段首个候选的 platform
            if step.candidates:
                return step.candidates[0].platform
        return ""

    @staticmethod
    def _pick_mainline_episode_candidate(
        candidates: list[dict], request_title: str
    ) -> dict:
        """在多个 episode 类型的候选中，按"主线剧集优先级"择优。

        场景："完美世界"搜索结果有 542046 剧场版（detect=movie 排除）、
        175141 双食记（6 集衍生短番，detect=episode 误命中）、
        403251 第三季 / 345811 第二季 / 449355 第四季（主线剧集）。
        不能取第一个 episode 候选（会误选双食记），需要择优。

        优先级（从高到低）：
        1. 标题与请求标题精确相等（最直接的主线条目）
        2. 标题含"第N季"声明（明确是季番条目）
        3. eps/total_episodes 最大的候选（主线剧集集数多）
        4. 兜底取第一个候选

        Args:
            candidates: detect_media_type 为 episode 的候选列表（已排除剧场版/电影）
            request_title: 请求侧标题（用于精确匹配判断）

        Returns:
            最佳候选
        """
        if not candidates:
            return {}  # type: ignore[return-value]
        if len(candidates) == 1:
            return candidates[0]

        request_title = (request_title or "").strip()

        # 1) 标题精确等于请求标题
        if request_title:
            for cand in candidates:
                name = (cand.get("name") or "").strip()
                name_cn = (cand.get("name_cn") or "").strip()
                if name == request_title or name_cn == request_title:
                    return cand

        # 2) 标题含"第N季"声明（明确是季番条目，优先于衍生短番）
        season_candidates = []
        for cand in candidates:
            name = cand.get("name") or ""
            name_cn = cand.get("name_cn") or ""
            combined = f"{name} {name_cn}"
            if "季" in combined or "期" in combined or "Season" in combined:
                season_candidates.append(cand)
        if season_candidates:
            # 在季番候选中再按 eps 排序
            season_candidates.sort(
                key=lambda c: int(c.get("eps") or c.get("total_episodes") or 0),
                reverse=True,
            )
            return season_candidates[0]

        # 3) eps/total_episodes 最大的候选
        sorted_by_eps = sorted(
            candidates,
            key=lambda c: int(c.get("eps") or c.get("total_episodes") or 0),
            reverse=True,
        )
        return sorted_by_eps[0]

    def _resolve_season_episode(
        self,
        bgm: BangumiApi,
        item: CustomItem,
        subject_id: str,
        is_season_matched_id: bool,
    ) -> tuple[str, str]:
        """根据 media_type 解析 Bangumi 季度与集数 ID。

        返回 (bgm_se_id, bgm_ep_id)；可能抛出 ValueError（认证错误由调用方处理）。
        """
        release_for_ep = None
        if item.release_date and len(item.release_date) >= 8:
            release_for_ep = item.release_date[:10]
        # 电影走短路径，剧集走季番解析
        if item.media_type == "movie":
            return bgm.get_movie_main_episode_id(subject_id, target_sort=item.episode)
        return bgm.get_target_season_episode_id(
            subject_id=subject_id,
            target_season=item.season,
            target_ep=item.episode,
            is_season_subject_id=is_season_matched_id,
            release_date=release_for_ep,
        )

    def _apply_sync_status(
        self,
        item: CustomItem,
        actual_source: str,
        bgm_se_id: str,
        bgm_ep_id: str,
        bgm_title: str,
        mark_status: int,
    ) -> str:
        """根据标记结果构建结果消息并发送通知。返回 result_message。"""
        if mark_status == 0:
            result_message = "已看过，不再重复标记"
            logger.info(
                f"bgm: {bgm_title or item.title} S{item.season:02d}E{item.episode:02d} {result_message}"
            )

            send_notify(
                "mark_skipped",
                item,
                actual_source,
                subject_id=bgm_se_id,
                episode_id=bgm_ep_id,
                bgm_title=bgm_title,
            )

        elif mark_status == 1:
            result_message = "已标记为看过"
            logger.info(
                f"bgm: {bgm_title or item.title} S{item.season:02d}E{item.episode:02d} {result_message} https://bgm.tv/ep/{bgm_ep_id}"
            )

            send_notify(
                "mark_success",
                item,
                actual_source,
                subject_id=bgm_se_id,
                episode_id=bgm_ep_id,
                bgm_title=bgm_title,
            )

        else:
            result_message = "已添加到收藏并标记为看过"
            logger.info(
                f"bgm: {bgm_title or item.title} 已添加到收藏 https://bgm.tv/subject/{bgm_se_id}"
            )
            logger.info(
                f"bgm: {bgm_title or item.title} S{item.season:02d}E{item.episode:02d} 已标记为看过 https://bgm.tv/ep/{bgm_ep_id}"
            )

            send_notify(
                "mark_success",
                item,
                actual_source,
                subject_id=bgm_se_id,
                episode_id=bgm_ep_id,
                bgm_title=bgm_title,
            )

        return result_message

    def _mark_subject_completed_if_needed(
        self,
        item: CustomItem,
        bgm: BangumiApi,
        bgm_se_id: str,
        bgm_title: str,
    ) -> None:
        """根据配置在单集标记后尝试将条目归档为「看过」（仅副作用，无返回值）。"""
        if item.media_type == "movie" and config_manager.get(
            "sync", "movie_mark_subject_completed", fallback=True
        ):
            try:
                coll = bgm.get_subject_collection(str(bgm_se_id))
                if coll.get("type") == 2:
                    logger.debug(
                        "剧场版条目收藏状态已为「看过」，跳过条目标记: "
                        f"subject_id={bgm_se_id}"
                    )
                else:
                    bgm.change_collection_state(subject_id=str(bgm_se_id), state=2)
            except Exception as e:
                logger.warning(
                    f"剧场版条目标记为看过失败（单集已处理）: subject_id={bgm_se_id} {e}"
                )

        if item.media_type != "movie" and config_manager.get(
            "sync", "anime_mark_subject_completed", fallback=False
        ):
            try:
                coll = bgm.get_subject_collection(str(bgm_se_id))
                if coll.get("type") == 2:
                    logger.debug(
                        "TV条目收藏状态已为「看过」，跳过条目标记: "
                        f"subject_id={bgm_se_id}"
                    )
                else:
                    # 获取番剧的总集数，如果已看集数等于或多于总集数，则自动归档为「看过」
                    subject_info = bgm.get_subject(bgm_se_id)
                    total_eps = subject_info.get("eps", 0)
                    watched_eps = coll.get("ep_status", 0) or 0
                    logger.debug(
                        f"获取到Subject: {bgm_se_id}, 总ep: {total_eps}, 已观看: {watched_eps}, coll: {coll}"
                    )
                    if total_eps > 0:
                        if watched_eps >= total_eps:
                            bgm.change_collection_state(
                                subject_id=str(bgm_se_id), state=2
                            )
                            logger.info(
                                f"bgm: {bgm_title or item.title} 所有剧集已看完（已看 {watched_eps}/{total_eps} 集），已自动归档为「看过」"
                            )
            except Exception as e:
                logger.warning(
                    f"TV番剧自动归档为「看过」失败（单集已处理）: subject_id={bgm_se_id} {e}"
                )

    def _allocate_inline_run_id(self) -> str:
        """直调 sync_custom_item 时分配 run_id。"""
        with self._tasks_lock:
            self._task_counter += 1
            counter = self._task_counter
        return new_inline_sync_run_id(counter)

    def sync_custom_item(
        self, item: CustomItem, source: str = "custom"
    ) -> SyncResponse:
        """同步自定义项目"""
        existing_run_id = get_sync_run_id()
        if existing_run_id:
            return self._sync_custom_item_impl(item, source)
        inline_id = self._allocate_inline_run_id()
        with sync_log_context(inline_id):
            return self._sync_custom_item_impl(item, source)

    def _sync_custom_item_impl(
        self, item: CustomItem, source: str = "custom"
    ) -> SyncResponse:
        actual_source = item.source if item.source else source
        status_holder: list[str] = ["error"]
        try:
            logger.info(
                f"同步开始: {item.title} S{item.season:02d}E{item.episode:02d} ({actual_source})"
            )
            return self._sync_custom_item_body(
                item, source, actual_source, status_holder
            )
        finally:
            logger.info(f"同步结束: status={status_holder[0]}")

    def _sync_custom_item_body(
        self,
        item: CustomItem,
        source: str,
        actual_source: str,
        status_holder: list[str],
    ) -> SyncResponse:
        try:
            trace: MatchTrace | None = None
            sync_action = (item.sync_action or "").strip().lower()
            if sync_action == "mark_watching":
                # movie 和 real_action（真人电影）走「标记在看」路径
                if item.media_type in ("movie", "real_action"):
                    result = self.sync_movie_watching(item, source)
                    status_holder[0] = result.status
                    return result
                result = SyncResponse(
                    status="ignored",
                    message="仅支持剧场版标记在看",
                )
                status_holder[0] = result.status
                return result

            logger.info(f"接收到同步请求：{item}")

            send_notify("request_received", item, actual_source)

            # 参数校验
            validation_error = self._normalize_custom_item_params(item, actual_source)
            if validation_error is not None:
                status_holder[0] = validation_error.status
                return validation_error

            # 查找番剧ID及其是否为特定季度ID的标记
            subject_id, is_season_matched_id, subject_error_response, trace = (
                self._find_matching_subject(item, actual_source)
            )
            if subject_error_response is not None:
                status_holder[0] = subject_error_response.status
                return subject_error_response

            # 获取对应用户的bangumi API实例
            bgm = self._get_bangumi_api_for_user(item.user_name)
            if not bgm:
                logger.error(f"无法为用户 {item.user_name} 创建bangumi API实例")
                status_holder[0] = "error"
                return SyncResponse(status="error", message="bangumi配置错误")

            # 查询 bangumi 章节：电影走短路径，剧集走季番解析
            ep_resolve_step = trace.start_step("episode_resolve") if trace else None
            # 记录集数解析的输入与变更过程（供详情页展示）
            resolve_input = {
                "input_subject_id": str(subject_id),
                "input_is_season_id": bool(is_season_matched_id),
                "request_season": item.season,
                "request_episode": item.episode,
                "media_type": item.media_type,
                "release_date": item.release_date or "",
            }
            try:
                bgm_se_id, bgm_ep_id = self._resolve_season_episode(
                    bgm, item, subject_id, is_season_matched_id
                )
            except ValueError as ve:
                # 捕获认证错误（通知已在 BangumiApi 中发送）
                if "认证失败" in str(ve) or "access_token" in str(ve):
                    if ep_resolve_step:
                        ep_resolve_step.status = "error"
                        ep_resolve_step.reason = f"认证失败: {ve}"
                        ep_resolve_step.processed_payload = {
                            **resolve_input,
                            "output_subject_id": "",
                            "output_episode_id": "",
                            "changed": False,
                            "error": str(ve),
                        }
                    status_holder[0] = "error"
                    return SyncResponse(status="error", message=str(ve))
                else:
                    raise ve

            if ep_resolve_step:
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

            if not bgm_ep_id:
                # 集数解析失败回退：通过前传/续集链在关联季条目中查找含目标 sort 的章节。
                # 场景：fongmi 传 episode=102（连续编号），但已命中条目（如第六季）
                # 的 sort 范围不含 102，需通过前传链向前找到含 sort=102 的季条目。
                cross_step = trace.start_step("cross_season") if trace else None
                cross_input_subject = str(subject_id)
                chain_pick = None
                try:
                    chain_pick = bgm.find_episode_across_seasons(
                        subject_id, item.episode
                    )
                except Exception as e:
                    logger.info(f"关联季条目链查找异常: {e}")
                if chain_pick:
                    chain_subject_id, chain_ep_id = chain_pick
                    prev_subject_id = subject_id
                    logger.info(
                        f"通过关联季条目链找到目标集: 原 subject_id={prev_subject_id}, "
                        f"改选 subject_id={chain_subject_id}, ep_id={chain_ep_id}, "
                        f"目标 episode={item.episode}"
                    )
                    subject_id = chain_subject_id
                    bgm_se_id = chain_subject_id
                    bgm_ep_id = chain_ep_id
                    if cross_step:
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
                    if trace:
                        trace.final_subject_id = str(chain_subject_id)
                        trace.final_episode_id = str(chain_ep_id)
                else:
                    logger.error(
                        f"bgm: {subject_id=} {item.season=} {item.episode=}, 不存在或集数过多，跳过"
                    )
                    if cross_step:
                        cross_step.status = "miss"
                        cross_step.reason = (
                            f"跨季链查找未命中含 sort={item.episode} 的季条目"
                        )
                        cross_step.processed_payload = {
                            "input_subject_id": cross_input_subject,
                            "output_subject_id": "",
                            "output_episode_id": "",
                            "target_episode": item.episode,
                            "changed": False,
                            "error": f"未找到含 sort={item.episode} 的关联季条目",
                        }
                    send_notify(
                        "episode_not_found",
                        item,
                        actual_source,
                        subject_id=subject_id,
                        error_message="不存在或集数过多",
                    )
                    # 与「未找到番剧」分支对称：写一条 error 同步记录，
                    # 便于在「同步记录」页面看到集数解析失败的情况
                    if trace:
                        result_step = trace.start_step("result")
                        result_step.status = "miss"
                        result_step.subject_id = str(subject_id)
                        result_step.reason = (
                            f"同步失败：未找到对应的剧集 · "
                            f"https://bgm.tv/subject/{subject_id}"
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
                    database_manager.log_sync_record(
                        user_name=item.user_name,
                        title=item.title,
                        ori_title=item.ori_title or "",
                        season=item.season,
                        episode=item.episode,
                        subject_id=str(subject_id),
                        episode_id=None,
                        status="error",
                        message="未找到对应的剧集（不存在或集数过多）",
                        source=actual_source,
                        media_type=item.media_type,
                        match_method=trace.final_match_method if trace else "",
                        match_score=trace.final_score if trace else None,
                        match_platform=self._extract_matched_platform(trace, subject_id)
                        if trace
                        else "",
                        match_trace=trace.to_dict() if trace else None,
                    )
                    status_holder[0] = "error"
                    return SyncResponse(status="error", message="未找到对应的剧集")

            # 通过实例缓存获取 Bangumi 平台标题（无额外 API 调用）
            subject_info = bgm.get_subject(bgm_se_id)
            bgm_title = ""
            if subject_info:
                bgm_title = (
                    subject_info.get("name_cn") or subject_info.get("name") or ""
                )

            logger.debug(
                f"bgm: 查询到 {bgm_title or item.title} (https://bgm.tv/subject/{bgm_se_id}) "
                f"S{item.season:02d}E{item.episode:02d} (https://bgm.tv/ep/{bgm_ep_id})"
            )

            # 发送匹配成功的通知，使用解析后的正确季度ID
            send_notify(
                "bangumi_id_found",
                item,
                actual_source,
                subject_id=bgm_se_id,
                bgm_title=bgm_title,
            )

            # 标记为看过
            sync_action_step = trace.start_step("sync_action") if trace else None
            try:
                mark_status = self._retry_mark_episode(bgm, bgm_se_id, bgm_ep_id)
            except ValueError as ve:
                # 捕获认证错误（通知已在 BangumiApi 中发送）
                if "认证失败" in str(ve) or "access_token" in str(ve):
                    if sync_action_step:
                        sync_action_step.status = "error"
                        sync_action_step.reason = f"认证失败: {ve}"
                    status_holder[0] = "error"
                    return SyncResponse(status="error", message=str(ve))
                else:
                    raise ve

            if sync_action_step:
                sync_action_step.status = "hit"
                sync_action_step.subject_id = str(bgm_se_id)
                # mark_status：0=已在看/看过 1=标记为看过 2=添加收藏
                action_label = {
                    0: "已在看/看过（无变更）",
                    1: "已标记为看过",
                    2: "已添加收藏",
                }.get(mark_status, f"mark_status={mark_status}")
                sync_action_step.reason = (
                    f"mark_episode_watched 返回 {mark_status}（{action_label}）"
                )
                if trace:
                    trace.final_action = str(mark_status)

            result_message = self._apply_sync_status(
                item, actual_source, bgm_se_id, bgm_ep_id, bgm_title, mark_status
            )

            self._mark_subject_completed_if_needed(item, bgm, bgm_se_id, bgm_title)

            # 回填最终剧集 ID 到 trace（匹配阶段未知 ep_id，此处补全）
            if trace and bgm_ep_id:
                trace.final_episode_id = str(bgm_ep_id)

            # 流水线最后一步：同步结果
            if trace:
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
                    "episode_url": f"https://bgm.tv/ep/{bgm_ep_id}"
                    if bgm_ep_id
                    else "",
                    "bgm_title": bgm_title,
                    "message": result_message,
                }
                trace.final_status = "success"
                trace.final_message = result_message
                trace.finish()

            # 记录同步成功到数据库
            database_manager.log_sync_record(
                user_name=item.user_name,
                title=item.title,
                ori_title=item.ori_title or "",
                season=item.season,
                episode=item.episode,
                subject_id=bgm_se_id,
                episode_id=bgm_ep_id,
                status="success",
                message=result_message,
                source=actual_source,
                media_type=item.media_type,
                bgm_title=bgm_title,
                match_method=trace.final_match_method if trace else "",
                match_score=trace.final_score if trace else None,
                match_platform=self._extract_matched_platform(trace, subject_id)
                if trace
                else "",
                match_trace=trace.to_dict() if trace else None,
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
                },
            )
            status_holder[0] = result.status
            return result
        except Exception as e:
            logger.error(f"自定义同步处理出错: {e}")

            # 记录同步失败到数据库
            database_manager.log_sync_record(
                user_name=item.user_name if "item" in locals() else "unknown",
                title=item.title if "item" in locals() else "unknown",
                ori_title=item.ori_title if "item" in locals() else "",
                season=item.season if "item" in locals() else 0,
                episode=item.episode if "item" in locals() else 0,
                status="error",
                message=str(e),
                source=actual_source if "actual_source" in locals() else source,
                media_type=item.media_type if "item" in locals() else "episode",
                match_method=trace.final_match_method if trace else "",
                match_score=trace.final_score if trace else None,
                match_platform=self._extract_matched_platform(trace, None)
                if trace
                else "",
                match_trace=trace.to_dict() if trace else None,
            )

            send_notify(
                "mark_failed",
                item if "item" in locals() else None,
                actual_source if "actual_source" in locals() else source,
                error_message=str(e),
                error_type="sync_error",
                additional_info=f"完整错误信息: {traceback.format_exc()}",
            )

            status_holder[0] = "error"
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")

    def _check_user_permission(
        self, user_name: str, source: str = ""
    ) -> tuple[bool, str]:
        """检查用户是否有权限同步。

        返回 (allowed, error_message)：
        - allowed=True 时 error_message 为空
        - allowed=False 时 error_message 为细粒化错误原因
        """
        # 测试来源跳过权限校验（由 sync.test_skip_permission_check 控制）
        is_test_source = source in ("test", "test-match")
        # fongmi 调试同步也视为测试来源
        if source and source.startswith("fongmi-debug"):
            is_test_source = True
        if is_test_source and config_manager.get(
            "sync", "test_skip_permission_check", fallback=False
        ):
            return True, ""

        mode = config_manager.get("sync", "mode", fallback="single")

        if mode == "single":
            allowed = config_manager.get_single_mode_media_usernames()
            if not allowed:
                logger.error(
                    "未设置 Bangumi 配置中的 media_server_username（媒体服务器用户名），请检查配置"
                )
                return False, (
                    "未配置媒体服务器用户名（media_server_username），"
                    "请在配置页面填写后再同步"
                )
            if user_name not in allowed:
                logger.debug(f"非配置同步用户：{user_name}，跳过")
                return False, (
                    f"用户 {user_name} 不在允许同步的媒体服务器用户名列表中"
                    f"（当前配置: {', '.join(allowed)}）"
                )
        elif mode == "multi":
            # 多用户模式，检查用户是否在映射配置中
            user_mappings = config_manager.get_user_mappings()
            if user_name not in user_mappings:
                logger.debug(f"多用户模式下用户 {user_name} 未配置映射，跳过")
                return False, (
                    f"用户 {user_name} 未在用户映射中配置（多用户模式下"
                    "需在 [sync] 段添加 媒体服务器用户名=bangumi-账号 映射）"
                )

            # 检查对应的bangumi配置是否存在且有效
            bangumi_config = self._get_bangumi_config_for_user(user_name)
            if not bangumi_config:
                logger.error(f"多用户模式下用户 {user_name} 的bangumi配置无效")
                return False, (
                    f"用户 {user_name} 对应的 Bangumi 账号配置无效或缺少 access_token"
                )
        else:
            logger.error(f"不支持的同步模式: {mode}")
            send_notify(
                "config_error",
                error_message=f"不支持的同步模式: {mode}",
                config_type="sync_mode",
                mode=mode,
            )
            return False, f"不支持的同步模式: {mode}（应为 single 或 multi）"

        return True, ""

    def _is_title_blocked(self, title: str, ori_title: str = None) -> bool:
        """检查番剧标题是否包含屏蔽关键词"""
        # 获取屏蔽关键词配置
        blocked_keywords_str = config_manager.get(
            "sync", "blocked_keywords", fallback=""
        ).strip()

        # 如果没有配置屏蔽关键词，直接返回False
        if not blocked_keywords_str:
            return False

        # 解析屏蔽关键词列表
        blocked_keywords = [
            keyword.strip()
            for keyword in blocked_keywords_str.split(",")
            if keyword.strip()
        ]

        # 如果解析后的关键词列表为空，直接返回False
        if not blocked_keywords:
            return False

        # 检查主标题
        if title:
            for keyword in blocked_keywords:
                if keyword.lower() in title.lower():
                    logger.info(
                        f'番剧标题 "{title}" 包含屏蔽关键词 "{keyword}"，跳过同步'
                    )
                    return True

        # 检查原始标题
        if ori_title:
            for keyword in blocked_keywords:
                if keyword.lower() in ori_title.lower():
                    logger.info(
                        f'番剧原始标题 "{ori_title}" 包含屏蔽关键词 "{keyword}"，跳过同步'
                    )
                    return True

        return False

    def _format_subject_not_found_message(self, item: CustomItem, detail: str) -> str:
        """同步记录用：未找到条目时的说明（与日志语义对齐）。"""
        parts = ["未查询到番剧信息，跳过"]
        if detail:
            parts.append(detail)
        if item.release_date and len(item.release_date) >= 8:
            parts.append(f"premiere_date={item.release_date[:10]}")
        return "；".join(parts)

    def _find_subject_id(
        self, item: CustomItem, trace: MatchTrace | None = None
    ) -> tuple[str | None, bool, str]:
        """根据标题和日期查找番剧ID。

        返回 (subject_id, is_season_matched_id, failure_detail)。
        成功时 failure_detail 为空字符串；失败时为简短原因，供同步记录与日志使用。

        当传入 trace 时，会记录每个匹配阶段的详细过程。
        """
        # 阶段 0：标题归一化（仅用于 API 搜索阶段，自定义映射仍使用原始标题以保证键名一致）
        normalized_title = self.normalize_title(item.title)
        if trace:
            trace.normalized_title = normalized_title
            normalize_step = trace.start_step("normalize")
            normalize_step.status = "hit"
            normalize_step.reason = f"标题归一化：{item.title!r} → {normalized_title!r}"
        else:
            normalize_step = None  # noqa: F841

        # 阶段 1：自定义映射（含季度感知 + 正则规则）
        if trace:
            step = trace.start_step("custom_mapping")
        else:
            step = None

        mapping_subject_id, match_type, match_reason = mapping_service.find_mapping(
            title=item.title,
            ori_title=item.ori_title or "",
            season=item.season,
        )

        if mapping_subject_id:
            logger.debug(
                f"匹配到自定义映射（{match_type}）：{item.title}={mapping_subject_id} - {match_reason}"
            )
            if step:
                step.status = "hit"
                step.subject_id = mapping_subject_id
                step.reason = match_reason
                step.score = 1.0
            if trace:
                trace.final_subject_id = mapping_subject_id
                trace.final_match_method = "custom_mapping"
                trace.final_score = 1.0
                trace.finish()
            # 自定义映射的ID不视为特定季度的ID
            return mapping_subject_id, False, ""

        if step:
            step.status = "miss"
            step.reason = "自定义映射与正则规则均未命中"

        # 阶段 2：bangumi-data 本地匹配
        # 标记是否通过bangumi-data获取的ID
        is_season_matched_id = False

        if config_manager.get("bangumi_data", "enabled", fallback=True):
            if trace:
                step = trace.start_step("bangumi_data")
            else:
                step = None
            try:
                bgm_data = self._get_bangumi_data()
                release_date = None

                if item.release_date and len(item.release_date) >= 8:
                    release_date = item.release_date[:10]
                else:
                    logger.debug("release_date为空或无效，尝试从bangumi-data中获取日期")

                bangumi_data_result = bgm_data.find_bangumi_id(
                    title=item.title,
                    ori_title=item.ori_title,
                    release_date=release_date,
                    season=item.season,
                    media_type=item.media_type,
                )

                if bangumi_data_result:
                    bangumi_data_id, matched_title, date_matched = bangumi_data_result
                    logger.info(
                        f"通过 bangumi-data 匹配到番剧 ID: {bangumi_data_id}, "
                        f"匹配标题: {matched_title}, 日期匹配: {date_matched}"
                    )

                    # 判断逻辑：优先使用日期匹配结果
                    if item.season > 1:
                        if date_matched:
                            # 通过日期匹配找到的，高可信度，直接标记为特定季度ID
                            logger.debug("通过日期匹配找到番剧，标记为可信的季度ID")
                            is_season_matched_id = True
                        else:
                            logger.debug(
                                f"未通过日期匹配，检查标题 '{matched_title}' 是否包含第{item.season}季信息"
                            )
                            # 未通过日期匹配，检查匹配到的标题中是否包含季度信息
                            title_has_season_info = self._check_season_info_in_title(
                                matched_title, item.season
                            )

                            # 根据季度信息判断是否为特定季度ID
                            if title_has_season_info:
                                logger.debug(
                                    f"匹配标题包含第{item.season}季信息，标记为特定季度ID"
                                )
                                is_season_matched_id = True
                            else:
                                logger.debug(
                                    f"匹配标题不包含季度信息，将从系列ID开始遍历续集查找第{item.season}季"
                                )
                                is_season_matched_id = False
                    else:
                        # 第一季总是返回True
                        is_season_matched_id = True

                    if step:
                        step.status = "hit"
                        step.subject_id = bangumi_data_id
                        step.reason = (
                            f"bangumi-data 匹配命中：{matched_title}，"
                            f"日期匹配={date_matched}，季度ID可信={is_season_matched_id}"
                        )
                        step.score = 1.0 if date_matched else 0.8
                    if trace:
                        trace.final_subject_id = bangumi_data_id
                        trace.final_match_method = "bangumi_data"
                        trace.final_score = 1.0 if date_matched else 0.8
                        trace.finish()
                    return bangumi_data_id, is_season_matched_id, ""
                else:
                    if step:
                        step.status = "miss"
                        step.reason = "bangumi-data 无匹配结果"
            except Exception as e:
                logger.error(f"bangumi-data 匹配出错: {e}")
                if step:
                    step.status = "error"
                    step.reason = f"bangumi-data 匹配异常：{e}"
        else:
            if trace:
                step = trace.start_step("bangumi_data")
                step.status = "skipped"
                step.reason = "bangumi-data 已禁用"

        # 阶段 3：Bangumi API 搜索
        if trace:
            step = trace.start_step("api_search")
        else:
            step = None

        # 根据配置与媒体类型决定搜索的条目类型：
        # - 默认仅动画（type=2）
        # - 开启三次元支持后扩展为 [2, 6]（动画 + 三次元，含日剧/电影）
        # - media_type=real_action 时强制包含 type=6（三次元），无论全局开关
        enable_real_action = config_manager.get(
            "sync", "enable_real_action", fallback=False
        )
        if item.media_type == "real_action":
            subject_types = [6]
        elif enable_real_action:
            subject_types = [2, 6]
        else:
            subject_types = [2]
        if step and (enable_real_action or item.media_type == "real_action"):
            step.reason = f"搜索 type={subject_types}（media_type={item.media_type}）"

        _ctx = (
            f"user_name={item.user_name!r} source={item.source!r} "
            f"S{item.season:02d}E{item.episode:02d} media_type={item.media_type!r} "
            f"title={item.title!r} ori_title={item.ori_title!r}"
        )
        try:
            # 使用对应用户的bangumi API实例进行搜索
            bgm = self._get_bangumi_api_for_user(item.user_name)
            if not bgm:
                logger.error(f"bgm: 无法为用户创建 Bangumi API 实例进行搜索；{_ctx}")
                if step:
                    step.status = "error"
                    step.reason = "无法创建 Bangumi API 实例"
                if trace:
                    trace.finish()
                return None, False, "无法创建 Bangumi API 实例，无法搜索条目"

            premiere_date = None
            if item.release_date and len(item.release_date) >= 8:
                premiere_date = item.release_date[:10]

            # 搜索时优先使用归一化标题（去除发布组/分辨率/编码等噪声），
            # 若归一化结果为空则回退到原始标题
            search_title = normalized_title or item.title
            bgm_data = bgm.bgm_search(
                title=search_title,
                ori_title=item.ori_title or "",
                premiere_date=premiere_date or "",
                is_movie=(item.media_type == "movie"),
                subject_types=subject_types,
            )
            if not bgm_data:
                logger.error(
                    "bgm: 未查询到番剧信息，跳过；"
                    f"{_ctx} premiere_date={premiere_date!r}"
                )
                if step:
                    step.status = "miss"
                    step.reason = "Bangumi API 搜索无结果"
                if trace:
                    trace.finish()
                return None, False, "Bangumi 搜索无结果"

            # top-N platform 加权排序：按放送形态重排候选，使最可能的目标排在首位
            is_movie_request = item.media_type == "movie"
            bgm_data = self._sort_candidates_by_platform(
                bgm_data, is_movie=is_movie_request, limit=15
            )
            # 注：limit=15 与 OLD_SEARCH_CANDIDATE_LIMIT 一致，
            # 保证 detect_media_type 改选时所有候选都可用，
            # 后续 trace 候选收集单独取 top-5。

            # 记录 api_search step（原始搜索结果，可能在后续 post_search step 中被改选）
            original_top = bgm_data[0] if bgm_data else {}
            original_top_id = original_top.get("id")
            original_top_name = (
                original_top.get("name_cn") or original_top.get("name") or ""
            )
            if step:
                step.status = "hit"
                step.subject_id = original_top_id
                step.reason = f"API 搜索命中：{original_top_name}"
                for cand in bgm_data[:5]:
                    step.candidates.append(
                        MatchCandidate(
                            subject_id=str(cand.get("id", "")),
                            name=cand.get("name", ""),
                            name_cn=cand.get("name_cn", ""),
                            platform=cand.get("platform", ""),
                            air_date=cand.get("date", ""),
                            source="api_search",
                        )
                    )

            # 校验返回结果的标题是否包含目标季度信息，确认是否精准命中季度本体
            is_api_season_matched = False
            post_step = None  # post_search step（如果触发改选）

            if item.season > 1:
                returned_name = bgm_data[0].get("name", "")
                returned_name_cn = bgm_data[0].get("name_cn", "")

                if self._check_season_info_in_title(
                    returned_name, item.season
                ) or self._check_season_info_in_title(returned_name_cn, item.season):
                    is_api_season_matched = True
            elif item.season == 1:
                # 第一季：若首条候选明确声明了第N季（N>1，如"凡人修仙传 第五季"），
                # 说明 API 按热度/相关度返回了续季，需要在候选列表里寻找
                # 标题不含季度后缀的条目作为第一季本体。
                top_name = bgm_data[0].get("name", "")
                top_name_cn = bgm_data[0].get("name_cn", "")
                top_explicit_season = max(
                    self._get_explicit_season_from_title(top_name) or 0,
                    self._get_explicit_season_from_title(top_name_cn) or 0,
                )
                if top_explicit_season > 1:
                    # 在候选列表里寻找标题不含季度声明的条目（即第一季本体）
                    for cand in bgm_data[1:]:
                        cand_name = cand.get("name", "")
                        cand_name_cn = cand.get("name_cn", "")
                        cand_season = max(
                            self._get_explicit_season_from_title(cand_name) or 0,
                            self._get_explicit_season_from_title(cand_name_cn) or 0,
                        )
                        if cand_season == 0:
                            logger.info(
                                f"首条候选为第{top_explicit_season}季，"
                                f"改选无季度后缀的候选: "
                                f"{cand_name_cn or cand_name}(id={cand.get('id')})"
                            )
                            if trace:
                                post_step = trace.start_step("post_search")
                            bgm_data[0] = cand
                            is_api_season_matched = True
                            if post_step:
                                post_step.status = "hit"
                                post_step.subject_id = cand.get("id")
                                post_step.reason = (
                                    f"季度改选：首条为第{top_explicit_season}季，"
                                    f"改选无季度后缀的第一季本体"
                                )
                                post_step.candidates.append(
                                    MatchCandidate(
                                        subject_id=str(cand.get("id", "")),
                                        name=cand.get("name", ""),
                                        name_cn=cand.get("name_cn", ""),
                                        platform=cand.get("platform", ""),
                                        air_date=cand.get("date", ""),
                                        source="post_search",
                                    )
                                )
                            break
                    if not is_api_season_matched:
                        logger.info(
                            f"首条候选明确为第{top_explicit_season}季，"
                            f"但候选列表中无无季度后缀的条目，保持首条"
                        )

                # 媒体类型校验：若请求 episode 但首条候选标题命中剧场版/电影/OVA/OAD/真人版
                # 关键词（如"完美世界"搜索首条返回"完美世界剧场版 九劫焚天"），
                # 在候选列表里寻找 detect_media_type 与请求一致的条目。
                # 仅在尚未通过季度改选时执行，避免覆盖上一步的正确结果。
                request_media_type = (item.media_type or "").strip().lower()
                if not is_api_season_matched and request_media_type:
                    top_detected = detect_media_type(
                        title=bgm_data[0].get("name_cn", ""),
                        ori_title=bgm_data[0].get("name", ""),
                    )
                    # 判断首条候选标题是否与请求标题精确匹配
                    # （如请求"完美世界"但首条是"完美世界双食记"，不算精确匹配）
                    top_name = (bgm_data[0].get("name") or "").strip()
                    top_name_cn = (bgm_data[0].get("name_cn") or "").strip()
                    request_title = (item.title or "").strip()
                    top_exact_match = request_title and request_title in {
                        top_name,
                        top_name_cn,
                    }
                    # 触发改选的条件：
                    # 1) 首条 detect 与请求类型不一致（如 movie vs episode）
                    # 2) 或首条标题与请求标题不精确匹配（如"完美世界双食记" vs "完美世界"）
                    #     —— 此时可能在 episode 候选中存在更合适的主线条目
                    need_reselect = (
                        top_detected != request_media_type or not top_exact_match
                    )
                    logger.info(
                        f"改选判定: 首条={top_name_cn or top_name} "
                        f"(id={bgm_data[0].get('id')}, detect={top_detected}, "
                        f"精确匹配={top_exact_match}), 请求类型={request_media_type}, "
                        f"need_reselect={need_reselect}, 候选数={len(bgm_data)}"
                    )
                    if need_reselect:
                        if trace:
                            post_step = trace.start_step("post_search")

                        # 1) 先在候选列表里找媒体类型一致的条目
                        #    可能有多个 episode 候选（如"完美世界"命中剧场版+衍生短番+多季），
                        #    需要按"主线剧集优先级"择优，避免误选衍生短番（如双食记只有6集）。
                        episode_candidates = []
                        for cand in bgm_data[1:]:
                            cand_detected = detect_media_type(
                                title=cand.get("name_cn", ""),
                                ori_title=cand.get("name", ""),
                            )
                            if cand_detected == request_media_type:
                                episode_candidates.append(cand)

                        # 如果首条 detect 一致但标题不精确匹配（如双食记），
                        # 也加入候选，参与主线优先级择优
                        if top_detected == request_media_type and not top_exact_match:
                            episode_candidates.insert(0, bgm_data[0])

                        logger.info(
                            f"episode_candidates 数={len(episode_candidates)}: "
                            + ", ".join(
                                f"{c.get('name_cn') or c.get('name')}(id={c.get('id')},"
                                f" eps={c.get('eps') or c.get('total_episodes')})"
                                for c in episode_candidates
                            )
                        )

                        if episode_candidates:
                            best_cand = self._pick_mainline_episode_candidate(
                                episode_candidates, item.title or ""
                            )
                            logger.info(
                                f"_pick_mainline_episode_candidate 择优结果: "
                                f"{best_cand.get('name_cn') or best_cand.get('name')}"
                                f"(id={best_cand.get('id')})"
                            )
                            # 仅当择优结果与当前首条不同时才改选并标记已匹配
                            if best_cand.get("id") != bgm_data[0].get("id"):
                                logger.info(
                                    f"首条候选 {top_name_cn or top_name} "
                                    f"(detect={top_detected}, 精确匹配={top_exact_match}) "
                                    f"不够理想，改选主线剧集: "
                                    f"{best_cand.get('name_cn') or best_cand.get('name')}"
                                    f"(id={best_cand.get('id')})"
                                )
                                bgm_data[0] = best_cand
                                is_api_season_matched = True

                        # 2) 候选列表里没有一致的，调用关联条目 API 在关联条目里找。
                        #    场景："完美世界"搜索首条是 542046 完美世界剧场版，
                        #    其关联条目含 577198 完美世界 第六季（主线故事），
                        #    可据此跳过剧场版命中主线剧集。
                        related_list = []
                        if not is_api_season_matched:
                            top_id = bgm_data[0].get("id")
                            if top_id:
                                try:
                                    related = bgm.get_related_subjects(top_id)
                                    if isinstance(related, list):
                                        related_list = related
                                    elif isinstance(related, dict):
                                        related_list = related.get("data", [])
                                    else:
                                        related_list = []
                                except Exception as e:
                                    logger.info(
                                        f"获取关联条目失败 (subject_id={top_id}): {e}"
                                    )
                                    related_list = []

                                logger.info(
                                    f"关联条目数={len(related_list)} (top_id={top_id}): "
                                    + ", ".join(
                                        f"{r.get('name_cn') or r.get('name')}"
                                        f"(id={r.get('id')},"
                                        f" relation={r.get('relation')})"
                                        for r in related_list
                                        if isinstance(r, dict)
                                    )
                                )

                                # 优先选 relation="主线故事" 且媒体类型一致的关联条目
                                mainline_match = None
                                other_match = None
                                for rel in related_list:
                                    if not isinstance(rel, dict):
                                        continue
                                    # 类型过滤：排除游戏(4)、书籍(5)等非影视条目，
                                    # 防止 detect_media_type 仅凭标题关键词误判
                                    rel_type = rel.get("type")
                                    if rel_type in (4, 5):
                                        continue
                                    rel_name = rel.get("name", "")
                                    rel_name_cn = rel.get("name_cn", "") or rel_name
                                    # 标题相关性校验：与搜索标题完全不相关的条目跳过，
                                    # 防止同 IP 下无关条目（如游戏改动画）被误选
                                    search_title = (item.title or "").strip()
                                    if search_title and rel_name_cn:
                                        title_sim = fuzz.ratio(
                                            rel_name_cn, search_title
                                        )
                                        if title_sim < 25:
                                            continue
                                    rel_detected = detect_media_type(
                                        title=rel_name_cn, ori_title=rel_name
                                    )
                                    if rel_detected != request_media_type:
                                        continue
                                    rel_relation = (rel.get("relation") or "").strip()
                                    if rel_relation == "主线故事":
                                        mainline_match = rel
                                        break
                                    if other_match is None:
                                        other_match = rel

                                chosen = mainline_match or other_match
                                logger.info(
                                    f"关联条目改选: mainline_match="
                                    f"{mainline_match.get('id') if mainline_match else None}, "
                                    f"other_match="
                                    f"{other_match.get('id') if other_match else None}, "
                                    f"chosen={chosen.get('id') if chosen else None}"
                                )
                                if chosen:
                                    chosen_id = chosen.get("id")
                                    if chosen_id:
                                        try:
                                            chosen_info = bgm.get_subject(chosen_id)
                                            if chosen_info and chosen_info.get("id"):
                                                logger.info(
                                                    f"首条候选媒体类型={top_detected} "
                                                    f"与请求 {request_media_type} 不一致，"
                                                    f"通过关联条目改选: "
                                                    f"{chosen_info.get('name_cn') or chosen_info.get('name')}"
                                                    f"(id={chosen_id}, "
                                                    f"relation={chosen.get('relation')})"
                                                )
                                                bgm_data[0] = chosen_info
                                                is_api_season_matched = True
                                        except Exception as e:
                                            logger.info(
                                                f"获取关联条目详情失败 "
                                                f"(subject_id={chosen_id}): {e}"
                                            )

                        # 记录 post_search step 结果
                        if post_step:
                            if (
                                is_api_season_matched
                                and bgm_data[0].get("id") != original_top_id
                            ):
                                post_step.status = "hit"
                                post_step.subject_id = bgm_data[0].get("id")
                                final_name = (
                                    bgm_data[0].get("name_cn")
                                    or bgm_data[0].get("name")
                                    or ""
                                )
                                post_step.reason = (
                                    f"搜索后处理改选：原首条 "
                                    f"{original_top_name}(id={original_top_id}) "
                                    f"→ {final_name}(id={bgm_data[0].get('id')})"
                                )
                            else:
                                post_step.status = "miss"
                                post_step.reason = (
                                    "搜索后处理未改选：候选与关联条目中"
                                    "均无一致条目，保持首条"
                                )
                            # 记录 episode 候选与关联条目作为 post_search 的候选
                            for c in episode_candidates:
                                post_step.candidates.append(
                                    MatchCandidate(
                                        subject_id=str(c.get("id", "")),
                                        name=c.get("name", ""),
                                        name_cn=c.get("name_cn", ""),
                                        platform=c.get("platform", ""),
                                        air_date=c.get("date", ""),
                                        source="post_search",
                                    )
                                )
                            for r in related_list:
                                if not isinstance(r, dict):
                                    continue
                                post_step.candidates.append(
                                    MatchCandidate(
                                        subject_id=str(r.get("id", "")),
                                        name=r.get("name", ""),
                                        name_cn=r.get("name_cn", ""),
                                        source="post_search_related",
                                    )
                                )

                        if not is_api_season_matched:
                            logger.info(
                                f"首条候选媒体类型={top_detected} 与请求 "
                                f"{request_media_type} 不一致，候选与关联条目中"
                                f"均无一致条目，保持首条"
                            )

            if trace:
                trace.final_subject_id = bgm_data[0]["id"]
                trace.final_match_method = "api_search"
                # API 搜索置信度：首条候选固定 0.9，季度命中加成 1.0
                trace.final_score = 1.0 if is_api_season_matched else 0.9
                trace.finish()
            return bgm_data[0]["id"], is_api_season_matched, ""
        except Exception as e:
            detail = f"Bangumi API 搜索出错: {e}"
            logger.error(f"bgm: {detail}；{_ctx}")
            if step:
                step.status = "error"
                step.reason = detail
            if trace:
                trace.finish()
            return None, False, detail

    def _get_bangumi_config_for_user(self, user_name: str) -> dict[str, str] | None:
        """根据媒体服务器用户名获取对应的bangumi配置"""
        mode = config_manager.get("sync", "mode", fallback="single")

        if mode == "single":
            # 单用户模式，使用默认的bangumi配置
            return {
                "username": config_manager.get("bangumi", "username", fallback=""),
                "access_token": config_manager.get(
                    "bangumi", "access_token", fallback=""
                ),
                "private": config_manager.get("bangumi", "private", fallback=False),
            }
        elif mode == "multi":
            # 多用户模式，根据用户映射查找对应的配置
            user_mappings = config_manager.get_user_mappings()
            bangumi_configs = config_manager.get_bangumi_configs()

            bangumi_section = user_mappings.get(user_name)
            if bangumi_section and bangumi_section in bangumi_configs:
                return bangumi_configs[bangumi_section]
            else:
                logger.error(f"多用户模式下未找到用户 {user_name} 的bangumi配置映射")
                return None

        return None

    def _get_bangumi_api_for_user(self, user_name: str) -> BangumiApi | None:
        """根据用户名创建对应的BangumiApi实例"""
        bangumi_config = self._get_bangumi_config_for_user(user_name)
        if not bangumi_config:
            return None

        if not bangumi_config["username"] or not bangumi_config["access_token"]:
            logger.error(f"用户 {user_name} 的bangumi配置不完整")
            return None

        return BangumiApi(
            username=bangumi_config["username"],
            access_token=bangumi_config["access_token"],
            private=bangumi_config["private"],
            http_proxy=config_manager.get("dev", "script_proxy", fallback=""),
            ssl_verify=config_manager.get("dev", "ssl_verify", fallback=True),
            bgm_api_proxy=config_manager.get("dev", "bgm_api_proxy", fallback=""),
            bgm_next_proxy=config_manager.get("dev", "bgm_next_proxy", fallback=""),
        )

    def _get_bangumi_data(self) -> BangumiData:
        """获取BangumiData实例（使用实例缓存避免内存泄漏）"""
        if self._bangumi_data_cache is None:
            self._bangumi_data_cache = bangumi_data
        return self._bangumi_data_cache

    def _load_custom_mappings(self) -> dict[str, str]:
        """从外部JSON文件读取自定义映射配置"""
        return mapping_service.load_custom_mappings()


# 全局同步服务实例（懒加载：首次访问 sync_service 时才创建实例与线程池）
_sync_service: SyncService | None = None


def __getattr__(name: str) -> Any:
    """模块级懒加载，避免 import 时即创建 ThreadPoolExecutor。"""
    global _sync_service
    if name == "sync_service":
        if _sync_service is None:
            _sync_service = SyncService()
        return _sync_service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
