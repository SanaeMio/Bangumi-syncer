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

from ...core.config import config_manager
from ...core.database import database_manager
from ...core.logging import logger
from ...models.sync import CustomItem, SyncResponse
from ...utils.bangumi_api import BangumiApi
from ...utils.bangumi_data import BangumiData, bangumi_data
from ...utils.notifier import send_notify
from ..mapping_service import mapping_service
from .match_trace import MatchCandidate, MatchTrace
from .retry import RetryMixin
from .season_info import SeasonInfoMixin
from .task_manager import TaskManagerMixin


class SyncService(TaskManagerMixin, RetryMixin, SeasonInfoMixin):
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
        # 最近一次匹配追踪信息（供 log_sync_record 使用）
        self._last_match_trace: MatchTrace | None = None
        self._last_match_method: str = ""
        self._last_match_score: float | None = None
        self._last_match_platform: str = ""

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

    def test_match(self, item: CustomItem) -> dict[str, Any]:
        """测试匹配过程，返回匹配追踪详情（不执行实际同步、不写记录、不发通知）

        用于「调试工具」页面的匹配测试面板，直观展示三段式匹配的完整过程。
        """
        trace = MatchTrace(
            request_title=item.title,
            request_ori_title=item.ori_title or "",
            request_season=item.season,
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

            if item.media_type != "movie":
                return SyncResponse(
                    status="ignored", message="仅剧场版支持播放开始标记在看"
                )

            if not item.title:
                logger.error("同步名称为空，跳过")
                return SyncResponse(status="error", message="同步名称为空")

            if not self._check_user_permission(item.user_name):
                return SyncResponse(status="error", message="用户无权限同步")

            if self._is_title_blocked(item.title, item.ori_title):
                return SyncResponse(
                    status="ignored", message="番剧标题包含屏蔽关键词，跳过同步"
                )

            subject_id, _, subject_find_error = self._find_subject_id(item)
            if not subject_id:
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
                    message=self._format_subject_not_found_message(
                        item, subject_find_error
                    ),
                    source=actual_source,
                    media_type=item.media_type,
                )
                return SyncResponse(status="error", message="未找到匹配的番剧")

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

    def _normalize_custom_item_params(self, item: CustomItem) -> SyncResponse | None:
        """校验自定义条目参数。返回 SyncResponse 表示应立即返回该响应；None 表示校验通过。"""
        # 基本验证
        if item.media_type not in ("episode", "movie"):
            logger.error(f"同步类型{item.media_type}不支持，跳过")
            return SyncResponse(
                status="error", message=f"同步类型{item.media_type}不支持"
            )

        if not item.title:
            logger.error("同步名称为空，跳过")
            return SyncResponse(status="error", message="同步名称为空")

        if item.media_type == "episode" and item.season == 0:
            logger.error("不支持SP标记同步，跳过")
            return SyncResponse(status="error", message="不支持SP标记同步")

        if item.episode == 0:
            logger.error(f"集数{item.episode}不能为0，跳过")
            return SyncResponse(status="error", message=f"集数{item.episode}不能为0")

        # 检查用户权限
        if not self._check_user_permission(item.user_name):
            return SyncResponse(status="error", message="用户无权限同步")

        # 检查是否包含屏蔽关键词
        if self._is_title_blocked(item.title, item.ori_title):
            return SyncResponse(
                status="ignored", message="番剧标题包含屏蔽关键词，跳过同步"
            )

        return None

    def _find_matching_subject(
        self, item: CustomItem, actual_source: str
    ) -> tuple[str | None, bool, SyncResponse | None]:
        """查找匹配的 Bangumi 条目。

        返回 (subject_id, is_season_matched_id, error_response)：
        - 成功：(id, flag, None)
        - 失败：(None, False, 应立即返回的 SyncResponse)
        """
        # 始终创建匹配追踪，记录三段式匹配过程供「匹配记录」页面展示
        trace = MatchTrace(
            request_title=item.title,
            request_ori_title=item.ori_title or "",
            request_season=item.season,
        )

        # 查找番剧ID及其是否为特定季度ID的标记
        subject_id, is_season_matched_id, subject_find_error = self._find_subject_id(
            item, trace=trace
        )

        # 存储匹配追踪信息（供后续 log_sync_record 使用）
        trace.finish()
        self._last_match_trace = trace
        self._last_match_method = trace.final_match_method
        self._last_match_score = trace.final_score
        self._last_match_platform = ""  # 暂无 platform 信息，后续阶段补充

        if subject_id:
            return subject_id, is_season_matched_id, None

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
            match_method=self._last_match_method,
            match_score=self._last_match_score,
            match_platform=self._last_match_platform,
            match_trace=trace.to_dict() if trace else None,
        )
        return None, False, SyncResponse(status="error", message="未找到匹配的番剧")

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

    def sync_custom_item(
        self, item: CustomItem, source: str = "custom"
    ) -> SyncResponse:
        """同步自定义项目"""
        try:
            # 如果item中包含source字段，优先使用item的source
            actual_source = item.source if item.source else source
            sync_action = (item.sync_action or "").strip().lower()
            if sync_action == "mark_watching":
                if item.media_type == "movie":
                    return self.sync_movie_watching(item, source)
                return SyncResponse(
                    status="ignored",
                    message="仅支持剧场版标记在看",
                )

            logger.info(f"接收到同步请求：{item}")

            send_notify("request_received", item, actual_source)

            # 参数校验
            validation_error = self._normalize_custom_item_params(item)
            if validation_error is not None:
                return validation_error

            # 查找番剧ID及其是否为特定季度ID的标记
            subject_id, is_season_matched_id, subject_error_response = (
                self._find_matching_subject(item, actual_source)
            )
            if subject_error_response is not None:
                return subject_error_response

            # 获取对应用户的bangumi API实例
            bgm = self._get_bangumi_api_for_user(item.user_name)
            if not bgm:
                logger.error(f"无法为用户 {item.user_name} 创建bangumi API实例")
                return SyncResponse(status="error", message="bangumi配置错误")

            # 查询 bangumi 章节：电影走短路径，剧集走季番解析
            try:
                bgm_se_id, bgm_ep_id = self._resolve_season_episode(
                    bgm, item, subject_id, is_season_matched_id
                )
            except ValueError as ve:
                # 捕获认证错误（通知已在 BangumiApi 中发送）
                if "认证失败" in str(ve) or "access_token" in str(ve):
                    return SyncResponse(status="error", message=str(ve))
                else:
                    raise ve

            if not bgm_ep_id:
                logger.error(
                    f"bgm: {subject_id=} {item.season=} {item.episode=}, 不存在或集数过多，跳过"
                )
                send_notify(
                    "episode_not_found",
                    item,
                    actual_source,
                    subject_id=subject_id,
                    error_message="不存在或集数过多",
                )
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
            try:
                mark_status = self._retry_mark_episode(bgm, bgm_se_id, bgm_ep_id)
            except ValueError as ve:
                # 捕获认证错误（通知已在 BangumiApi 中发送）
                if "认证失败" in str(ve) or "access_token" in str(ve):
                    return SyncResponse(status="error", message=str(ve))
                else:
                    raise ve

            result_message = self._apply_sync_status(
                item, actual_source, bgm_se_id, bgm_ep_id, bgm_title, mark_status
            )

            self._mark_subject_completed_if_needed(item, bgm, bgm_se_id, bgm_title)

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
                match_method=self._last_match_method,
                match_score=self._last_match_score,
                match_platform=self._last_match_platform,
                match_trace=self._last_match_trace.to_dict()
                if self._last_match_trace
                else None,
            )

            return SyncResponse(
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
                match_method=self._last_match_method,
                match_score=self._last_match_score,
                match_platform=self._last_match_platform,
                match_trace=self._last_match_trace.to_dict()
                if self._last_match_trace
                else None,
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

    def _check_user_permission(self, user_name: str) -> bool:
        """检查用户是否有权限同步"""
        mode = config_manager.get("sync", "mode", fallback="single")

        if mode == "single":
            allowed = config_manager.get_single_mode_media_usernames()
            if not allowed:
                logger.error(
                    "未设置 Bangumi 配置中的 media_server_username（媒体服务器用户名），请检查配置"
                )
                return False
            if user_name not in allowed:
                logger.debug(f"非配置同步用户：{user_name}，跳过")
                return False
        elif mode == "multi":
            # 多用户模式，检查用户是否在映射配置中
            user_mappings = config_manager.get_user_mappings()
            if user_name not in user_mappings:
                logger.debug(f"多用户模式下用户 {user_name} 未配置映射，跳过")
                return False

            # 检查对应的bangumi配置是否存在且有效
            bangumi_config = self._get_bangumi_config_for_user(user_name)
            if not bangumi_config:
                logger.error(f"多用户模式下用户 {user_name} 的bangumi配置无效")
                return False
        else:
            logger.error(f"不支持的同步模式: {mode}")
            send_notify(
                "config_error",
                error_message=f"不支持的同步模式: {mode}",
                config_type="sync_mode",
                mode=mode,
            )
            return False

        return True

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
        # 阶段 1：自定义映射（含季度感知 + 正则规则）
        if trace:
            trace.normalized_title = item.title
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
            if trace:
                trace.final_subject_id = mapping_subject_id
                trace.final_match_method = "custom_mapping"
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
                    if trace:
                        trace.final_subject_id = bangumi_data_id
                        trace.final_match_method = "bangumi_data"
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

        # 根据配置决定搜索的条目类型：默认仅动画（type=2），
        # 开启三次元支持后扩展为 [2, 6]（动画 + 三次元，含日剧/电影）
        enable_real_action = config_manager.get(
            "sync", "enable_real_action", fallback=False
        )
        subject_types = [2, 6] if enable_real_action else [2]
        if step and enable_real_action:
            step.reason = f"已启用三次元支持，搜索 type={subject_types}"

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

            bgm_data = bgm.bgm_search(
                title=item.title,
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

            # 校验返回结果的标题是否包含目标季度信息，确认是否精准命中季度本体
            is_api_season_matched = False
            if item.season > 1:
                returned_name = bgm_data[0].get("name", "")
                returned_name_cn = bgm_data[0].get("name_cn", "")

                if self._check_season_info_in_title(
                    returned_name, item.season
                ) or self._check_season_info_in_title(returned_name_cn, item.season):
                    is_api_season_matched = True

            # 收集候选到 trace（top-5）
            if step:
                step.status = "hit"
                step.subject_id = bgm_data[0]["id"]
                step.reason = (
                    f"API 搜索命中：{bgm_data[0].get('name_cn') or bgm_data[0].get('name')}，"
                    f"季度ID可信={is_api_season_matched}"
                )
                # 记录候选列表
                for cand in bgm_data[:5]:
                    step.candidates.append(
                        MatchCandidate(
                            subject_id=str(cand.get("id", "")),
                            name=cand.get("name", ""),
                            name_cn=cand.get("name_cn", ""),
                            source="api_search",
                        )
                    )
            if trace:
                trace.final_subject_id = bgm_data[0]["id"]
                trace.final_match_method = "api_search"
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
