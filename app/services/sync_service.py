"""
同步服务模块
"""

import asyncio
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ..core.config import config_manager
from ..core.database import database_manager
from ..core.logging import logger
from ..models.sync import CustomItem, SyncResponse
from ..utils.bangumi_api import BangumiApi
from ..utils.bangumi_data import BangumiData, bangumi_data
from ..utils.data_util import (
    extract_emby_data,
    extract_jellyfin_data,
    extract_plex_data,
)
from ..utils.notifier import send_notify
from .mapping_service import mapping_service


class SyncService:
    """同步服务"""

    def __init__(self):
        self._bangumi_data_cache: Optional[BangumiData] = None
        self._cached_mappings: dict[str, str] = {}
        self._mapping_file_path: Optional[str] = None
        self._last_modified_time: float = 0
        # 线程池用于异步处理同步任务
        self._executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="sync_worker"
        )
        # 同步任务状态跟踪
        self._sync_tasks = {}
        self._task_counter = 0

    async def sync_custom_item_async(
        self, item: CustomItem, source: str = "custom"
    ) -> str:
        """异步同步自定义项目，返回任务ID"""
        self._task_counter += 1
        task_id = f"sync_{self._task_counter}_{int(time.time())}"

        # 记录任务状态
        self._sync_tasks[task_id] = {
            "status": "pending",
            "item": item.dict(),
            "source": source,
            "created_at": time.time(),
            "result": None,
            "error": None,
        }

        # 提交到线程池异步执行
        self._executor.submit(self._sync_custom_item_sync, item, source, task_id)

        # 不等待结果，立即返回任务ID
        logger.info(f"同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_custom_item_sync(
        self, item: CustomItem, source: str, task_id: str
    ) -> SyncResponse:
        """同步执行的内部方法"""
        try:
            # 更新任务状态
            self._sync_tasks[task_id]["status"] = "running"

            # 执行同步逻辑
            result = self.sync_custom_item(item, source)

            # 更新任务结果
            self._sync_tasks[task_id]["status"] = "completed"
            self._sync_tasks[task_id]["result"] = result.dict()

            return result

        except Exception as e:
            # 更新任务错误
            self._sync_tasks[task_id]["status"] = "failed"
            self._sync_tasks[task_id]["error"] = str(e)
            logger.error(f"异步同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    def get_sync_task_status(self, task_id: str) -> Optional[dict]:
        """获取同步任务状态"""
        return self._sync_tasks.get(task_id)

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """清理旧的任务记录"""
        current_time = time.time()
        cutoff_time = current_time - (max_age_hours * 3600)

        old_tasks = [
            task_id
            for task_id, task_info in self._sync_tasks.items()
            if task_info["created_at"] < cutoff_time
        ]

        for task_id in old_tasks:
            del self._sync_tasks[task_id]

        if old_tasks:
            logger.info(f"清理了 {len(old_tasks)} 个旧的同步任务记录")

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

            subject_id, _ = self._find_subject_id(item)
            if not subject_id:
                send_notify(
                    "anime_not_found",
                    item,
                    actual_source,
                    error_message="未找到匹配的番剧",
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
                return SyncResponse(
                    status="error", message=f"集数{item.episode}不能为0"
                )

            # 检查用户权限
            if not self._check_user_permission(item.user_name):
                return SyncResponse(status="error", message="用户无权限同步")

            # 检查是否包含屏蔽关键词
            if self._is_title_blocked(item.title, item.ori_title):
                return SyncResponse(
                    status="ignored", message="番剧标题包含屏蔽关键词，跳过同步"
                )

            # 查找番剧ID及其是否为特定季度ID的标记
            subject_id, is_season_matched_id = self._find_subject_id(item)
            if not subject_id:
                send_notify(
                    "anime_not_found",
                    item,
                    actual_source,
                    error_message="未找到匹配的番剧",
                )
                return SyncResponse(status="error", message="未找到匹配的番剧")

            # 获取对应用户的bangumi API实例
            bgm = self._get_bangumi_api_for_user(item.user_name)
            if not bgm:
                logger.error(f"无法为用户 {item.user_name} 创建bangumi API实例")
                return SyncResponse(status="error", message="bangumi配置错误")

            # 查询 bangumi 章节：电影走短路径，剧集走季番解析
            try:
                release_for_ep = None
                if item.release_date and len(item.release_date) >= 8:
                    release_for_ep = item.release_date[:10]
                if item.media_type == "movie":
                    bgm_se_id, bgm_ep_id = bgm.get_movie_main_episode_id(
                        subject_id, target_sort=item.episode
                    )
                else:
                    bgm_se_id, bgm_ep_id = bgm.get_target_season_episode_id(
                        subject_id=subject_id,
                        target_season=item.season,
                        target_ep=item.episode,
                        is_season_subject_id=is_season_matched_id,
                        release_date=release_for_ep,
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

            logger.debug(
                f"bgm: 查询到 {item.title} (https://bgm.tv/subject/{bgm_se_id}) "
                f"S{item.season:02d}E{item.episode:02d} (https://bgm.tv/ep/{bgm_ep_id})"
            )

            # 发送匹配成功的通知，使用解析后的正确季度ID
            send_notify("bangumi_id_found", item, actual_source, subject_id=bgm_se_id)

            # 标记为看过
            try:
                mark_status = self._retry_mark_episode(bgm, bgm_se_id, bgm_ep_id)
            except ValueError as ve:
                # 捕获认证错误（通知已在 BangumiApi 中发送）
                if "认证失败" in str(ve) or "access_token" in str(ve):
                    return SyncResponse(status="error", message=str(ve))
                else:
                    raise ve

            result_message = ""

            if mark_status == 0:
                result_message = "已看过，不再重复标记"
                logger.info(
                    f"bgm: {item.title} S{item.season:02d}E{item.episode:02d} {result_message}"
                )

                send_notify(
                    "mark_skipped",
                    item,
                    actual_source,
                    subject_id=bgm_se_id,
                    episode_id=bgm_ep_id,
                )

            elif mark_status == 1:
                result_message = "已标记为看过"
                logger.info(
                    f"bgm: {item.title} S{item.season:02d}E{item.episode:02d} {result_message} https://bgm.tv/ep/{bgm_ep_id}"
                )

                send_notify(
                    "mark_success",
                    item,
                    actual_source,
                    subject_id=bgm_se_id,
                    episode_id=bgm_ep_id,
                )

            else:
                result_message = "已添加到收藏并标记为看过"
                logger.info(
                    f"bgm: {item.title} 已添加到收藏 https://bgm.tv/subject/{bgm_se_id}"
                )
                logger.info(
                    f"bgm: {item.title} S{item.season:02d}E{item.episode:02d} 已标记为看过 https://bgm.tv/ep/{bgm_ep_id}"
                )

                send_notify(
                    "mark_success",
                    item,
                    actual_source,
                    subject_id=bgm_se_id,
                    episode_id=bgm_ep_id,
                )

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
            )

            return SyncResponse(
                status="success",
                message=result_message,
                data={
                    "title": item.title,
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

    def _find_subject_id(self, item: CustomItem) -> tuple[Optional[str], bool]:
        """根据标题和日期查找番剧ID"""
        # 获取自定义映射
        custom_mappings = self._load_custom_mappings()
        mapping_subject_id = custom_mappings.get(item.title, "")

        if mapping_subject_id:
            logger.debug(f"匹配到自定义映射：{item.title}={mapping_subject_id}")
            # 自定义映射的ID不视为特定季度的ID
            return mapping_subject_id, False

        # 标记是否通过bangumi-data获取的ID
        is_season_matched_id = False

        # 尝试使用 bangumi-data 匹配番剧ID
        if config_manager.get("bangumi_data", "enabled", fallback=True):
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

                    return bangumi_data_id, is_season_matched_id
            except Exception as e:
                logger.error(f"bangumi-data 匹配出错: {e}")

        # 如果没有匹配到，使用 bangumi API 搜索
        try:
            # 使用对应用户的bangumi API实例进行搜索
            bgm = self._get_bangumi_api_for_user(item.user_name)
            if not bgm:
                logger.error(f"无法为用户 {item.user_name} 创建bangumi API实例进行搜索")
                return None, False

            premiere_date = None
            if item.release_date and len(item.release_date) >= 8:
                premiere_date = item.release_date[:10]

            bgm_data = bgm.bgm_search(
                title=item.title,
                ori_title=item.ori_title or "",
                premiere_date=premiere_date or "",
                is_movie=(item.media_type == "movie"),
            )
            if not bgm_data:
                logger.error(
                    f"bgm: 未查询到番剧信息，跳过\nbgm: {item.title=} {item.ori_title=} {premiere_date=}"
                )
                return None, False

            # API搜索得到的ID不视为特定季度的ID
            return bgm_data[0]["id"], False
        except Exception as e:
            logger.error(f"bgm API搜索出错: {e}")
            return None, False

    def _check_season_info_in_title(self, title: str, season: int) -> bool:
        """检查标题中是否包含季度信息"""
        # 中文数字映射
        chinese_numbers = {
            1: "一",
            2: "二",
            3: "三",
            4: "四",
            5: "五",
            6: "六",
            7: "七",
            8: "八",
            9: "九",
            10: "十",
        }

        # 数字形式
        season_keywords = [
            f"第{season}季",
            f"第{season}期",
            f"{season}期",
            f"{season}季",
            f"Season {season}",
            f"S{season}",
        ]

        # 中文数字形式
        if season in chinese_numbers:
            chinese_num = chinese_numbers[season]
            season_keywords.extend(
                [
                    f"第{chinese_num}季",
                    f"第{chinese_num}期",
                    f"{chinese_num}期",
                    f"{chinese_num}季",
                ]
            )

        # 检查基本季度关键词
        for keyword in season_keywords:
            if keyword in title:
                logger.debug(f'匹配标题 "{title}" 包含季度信息: {keyword}')
                return True

        # 检查更复杂的格式
        chinese_num = chinese_numbers.get(season, "")

        # 基础模式：第X季 或 X季
        base_patterns = [rf"第{season}季", rf"{season}季"]

        # 如果有中文数字，添加中文数字模式
        if chinese_num:
            base_patterns.extend([rf"第{chinese_num}季", rf"{chinese_num}季"])

        # 部分标识符
        part_indicators = [r"\s+上半", r"\s+下半", r"\s+第2部分", r"\s+第二部分"]

        # 组合所有模式
        for base_pattern in base_patterns:
            for indicator in part_indicators:
                full_pattern = base_pattern + indicator
                if re.search(full_pattern, title):
                    logger.debug(f'匹配标题 "{title}" 包含复杂季度信息: {full_pattern}')
                    return True

        return False

    def _retry_mark_episode(
        self, bgm_api: BangumiApi, subject_id: str, ep_id: str, max_retries: int = 3
    ) -> int:
        """带重试机制的标记剧集方法（优化版，减少阻塞时间）"""
        for attempt in range(max_retries + 1):
            try:
                mark_status = bgm_api.mark_episode_watched(
                    subject_id=subject_id, ep_id=ep_id
                )
                if attempt > 0:
                    logger.info(f"重试成功，第 {attempt + 1} 次尝试标记成功")
                return mark_status
            except Exception as e:
                if attempt < max_retries:
                    # 优化延迟策略：减少最大延迟时间
                    delay = min(2**attempt, 3)  # 最大延迟3秒: 1, 2, 3秒
                    logger.error(
                        f"标记剧集失败: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )

                    # 使用非阻塞方式等待（在线程池中执行时不会阻塞主线程）
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"标记剧集失败，已达到最大重试次数 {max_retries}: {str(e)}"
                    )
                    raise e
        # This line should never be reached due to the loop logic
        return 0  # pragma: no cover

    async def _retry_mark_episode_async(
        self, bgm_api: BangumiApi, subject_id: str, ep_id: str, max_retries: int = 3
    ) -> int:
        """异步版本的重试标记剧集方法"""
        for attempt in range(max_retries + 1):
            try:
                # 在线程池中执行同步操作
                loop = asyncio.get_event_loop()
                mark_status = await loop.run_in_executor(
                    self._executor, bgm_api.mark_episode_watched, subject_id, ep_id
                )
                if attempt > 0:
                    logger.info(f"异步重试成功，第 {attempt + 1} 次尝试标记成功")
                return mark_status
            except Exception as e:
                if attempt < max_retries:
                    delay = min(2**attempt, 3)  # 最大延迟3秒
                    logger.error(
                        f"异步标记剧集失败: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )

                    # 使用异步等待，不阻塞事件循环
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"异步标记剧集失败，已达到最大重试次数 {max_retries}: {str(e)}"
                    )
                    raise e
        # This line should never be reached due to the loop logic
        return 0  # pragma: no cover

    def _get_bangumi_config_for_user(self, user_name: str) -> Optional[dict[str, str]]:
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

    def _get_bangumi_api_for_user(self, user_name: str) -> Optional[BangumiApi]:
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
        )

    def _get_bangumi_data(self) -> BangumiData:
        """获取BangumiData实例（使用实例缓存避免内存泄漏）"""
        if self._bangumi_data_cache is None:
            self._bangumi_data_cache = bangumi_data
        return self._bangumi_data_cache

    def _load_custom_mappings(self) -> dict[str, str]:
        """从外部JSON文件读取自定义映射配置"""
        return mapping_service.load_custom_mappings()

    async def sync_plex_item_async(self, plex_data: dict[str, Any]) -> str:
        """异步同步Plex项目，返回任务ID"""
        self._task_counter += 1
        task_id = f"plex_{self._task_counter}_{int(time.time())}"

        # 记录任务状态
        self._sync_tasks[task_id] = {
            "status": "pending",
            "item": plex_data,
            "source": "plex",
            "created_at": time.time(),
            "result": None,
            "error": None,
        }

        # 提交到线程池异步执行
        self._executor.submit(self._sync_plex_item_sync, plex_data, task_id)

        logger.info(f"Plex同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_plex_item_sync(
        self, plex_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Plex同步的内部方法"""
        try:
            # 更新任务状态
            self._sync_tasks[task_id]["status"] = "running"

            # 执行同步逻辑
            result = self.sync_plex_item(plex_data)

            # 更新任务结果
            self._sync_tasks[task_id]["status"] = "completed"
            self._sync_tasks[task_id]["result"] = result.dict()

            return result

        except Exception as e:
            # 更新任务错误
            self._sync_tasks[task_id]["status"] = "failed"
            self._sync_tasks[task_id]["error"] = str(e)
            logger.error(f"异步Plex同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    def sync_plex_item(self, plex_data: dict[str, Any]) -> SyncResponse:
        """处理Plex同步请求"""
        try:
            ev = plex_data["event"]
            if ev not in ("media.play", "media.scrobble"):
                logger.debug(f"事件类型{ev}无需同步，跳过")
                return SyncResponse(status="ignored", message=f"事件类型{ev}无需同步")

            md = plex_data["Metadata"]
            mtype = (md.get("type") or "").lower()
            if ev == "media.play" and mtype != "movie":
                logger.debug(f"事件类型{ev}非电影，无需同步")
                return SyncResponse(
                    status="ignored",
                    message=f"事件类型{ev}非电影，无需同步",
                )

            if mtype == "movie":
                logger.debug(
                    f"接收到Plex同步请求：{plex_data['event']} "
                    f"{plex_data['Account']['title']} 电影 {md.get('title', '')}"
                )
            else:
                logger.debug(
                    f"接收到Plex同步请求：{plex_data['event']} {plex_data['Account']['title']} "
                    f"S{md['parentIndex']:02d}E{md['index']:02d} {md.get('grandparentTitle', '')}"
                )

            # 提取数据并调用自定义同步
            custom_item = extract_plex_data(plex_data)
            logger.debug(f"Plex重新组装JSON报文：{custom_item}")

            if ev == "media.play":
                return self.sync_movie_watching(custom_item, source="plex")
            return self.sync_custom_item(custom_item, source="plex")
        except Exception as e:
            logger.error(f"Plex同步处理出错: {e}")
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")

    async def sync_emby_item_async(self, emby_data: dict[str, Any]) -> str:
        """异步同步Emby项目，返回任务ID"""
        self._task_counter += 1
        task_id = f"emby_{self._task_counter}_{int(time.time())}"

        # 记录任务状态
        self._sync_tasks[task_id] = {
            "status": "pending",
            "item": emby_data,
            "source": "emby",
            "created_at": time.time(),
            "result": None,
            "error": None,
        }

        # 提交到线程池异步执行
        self._executor.submit(self._sync_emby_item_sync, emby_data, task_id)

        logger.info(f"Emby同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_emby_item_sync(
        self, emby_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Emby同步的内部方法"""
        try:
            # 更新任务状态
            self._sync_tasks[task_id]["status"] = "running"

            # 执行同步逻辑
            result = self.sync_emby_item(emby_data)

            # 更新任务结果
            self._sync_tasks[task_id]["status"] = "completed"
            self._sync_tasks[task_id]["result"] = result.dict()

            return result

        except Exception as e:
            # 更新任务错误
            self._sync_tasks[task_id]["status"] = "failed"
            self._sync_tasks[task_id]["error"] = str(e)
            logger.error(f"异步Emby同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    def sync_emby_item(self, emby_data: dict[str, Any]) -> SyncResponse:
        """处理Emby同步请求"""
        try:
            # 记录接收到的数据
            logger.debug(f"接收到Emby同步请求：{emby_data}")

            # 验证必要字段是否存在
            required_fields = ["Event", "Item", "User"]
            for field in required_fields:
                if field not in emby_data:
                    logger.error(f"Emby请求缺少必要字段: {field}")
                    return SyncResponse(
                        status="error", message=f"请求缺少必要字段: {field}"
                    )

            event = emby_data["Event"]
            emby_item = emby_data["Item"]
            is_movie = str(emby_item.get("Type") or "").lower() == "movie"
            playback_start_movie = event == "playback.start" and is_movie

            if (
                event != "item.markplayed"
                and event != "playback.stop"
                and not playback_start_movie
            ):
                logger.debug(f"事件类型{event}无需同步，跳过")
                return SyncResponse(
                    status="ignored", message=f"事件类型{event}无需同步"
                )

            if is_movie:
                if "Name" not in emby_item:
                    logger.error("Emby 电影 Item 缺少 Name 字段")
                    return SyncResponse(
                        status="error", message="Item缺少必要字段: Name"
                    )
            else:
                item_required_fields = [
                    "Type",
                    "SeriesName",
                    "ParentIndexNumber",
                    "IndexNumber",
                ]
                for field in item_required_fields:
                    if field not in emby_item:
                        logger.error(f"Emby Item缺少必要字段: {field}")
                        return SyncResponse(
                            status="error", message=f"Item缺少必要字段: {field}"
                        )

            # 如果是播放停止事件,只有播放完成才判断为看过
            if event == "playback.stop":
                if (
                    "PlaybackInfo" not in emby_data
                    or "PlayedToCompletion" not in emby_data["PlaybackInfo"]
                ):
                    logger.debug(
                        "播放停止事件缺少PlaybackInfo.PlayedToCompletion字段，跳过"
                    )
                    return SyncResponse(status="ignored", message="播放信息不完整")

                if emby_data["PlaybackInfo"]["PlayedToCompletion"] is not True:
                    if is_movie:
                        logger.debug(
                            f"{emby_item.get('Name', '')} 电影未播放完成，跳过"
                        )
                    else:
                        logger.debug(
                            f"{emby_item['SeriesName']} S{emby_item['ParentIndexNumber']:02d}E{emby_item['IndexNumber']:02d}未播放完成，跳过"
                        )
                    return SyncResponse(status="ignored", message="未播放完成")

            # 提取数据并调用自定义同步
            custom_item = extract_emby_data(emby_data)
            logger.debug(f"Emby重新组装JSON报文：{custom_item}")

            if playback_start_movie:
                return self.sync_movie_watching(custom_item, source="emby")
            return self.sync_custom_item(custom_item, source="emby")
        except Exception as e:
            logger.error(f"Emby同步处理出错: {e}")
            logger.error(traceback.format_exc())
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")

    async def sync_jellyfin_item_async(self, jellyfin_data: dict[str, Any]) -> str:
        """异步同步Jellyfin项目，返回任务ID"""
        self._task_counter += 1
        task_id = f"jellyfin_{self._task_counter}_{int(time.time())}"

        # 记录任务状态
        self._sync_tasks[task_id] = {
            "status": "pending",
            "item": jellyfin_data,
            "source": "jellyfin",
            "created_at": time.time(),
            "result": None,
            "error": None,
        }

        # 提交到线程池异步执行
        self._executor.submit(self._sync_jellyfin_item_sync, jellyfin_data, task_id)

        logger.info(f"Jellyfin同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_jellyfin_item_sync(
        self, jellyfin_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Jellyfin同步的内部方法"""
        try:
            # 更新任务状态
            self._sync_tasks[task_id]["status"] = "running"

            # 执行同步逻辑
            result = self.sync_jellyfin_item(jellyfin_data)

            # 更新任务结果
            self._sync_tasks[task_id]["status"] = "completed"
            self._sync_tasks[task_id]["result"] = result.dict()

            return result

        except Exception as e:
            # 更新任务错误
            self._sync_tasks[task_id]["status"] = "failed"
            self._sync_tasks[task_id]["error"] = str(e)
            logger.error(f"异步Jellyfin同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    def sync_jellyfin_item(self, jellyfin_data: dict[str, Any]) -> SyncResponse:
        """处理Jellyfin同步请求"""
        try:
            logger.debug(f"接收到Jellyfin同步请求：{jellyfin_data}")

            ntype = jellyfin_data.get("NotificationType", "")
            mtype = (jellyfin_data.get("media_type") or "").lower()
            playback_start_movie = ntype == "PlaybackStart" and mtype == "movie"

            if ntype != "PlaybackStop" and not playback_start_movie:
                logger.debug(f"事件类型{ntype}无需同步，跳过")
                return SyncResponse(
                    status="ignored",
                    message=f"事件类型{ntype}无需同步",
                )

            if ntype == "PlaybackStop":
                if jellyfin_data["PlayedToCompletion"] == "False":
                    logger.debug(
                        f"是否播完：{jellyfin_data['PlayedToCompletion']}，无需同步，跳过"
                    )
                    return SyncResponse(
                        status="ignored", message="未播放完成，跳过同步"
                    )

            # 提取数据并调用自定义同步
            custom_item = extract_jellyfin_data(jellyfin_data)
            logger.debug(f"Jellyfin重新组装JSON报文：{custom_item}")

            if playback_start_movie:
                return self.sync_movie_watching(custom_item, source="jellyfin")
            return self.sync_custom_item(custom_item, source="jellyfin")
        except Exception as e:
            logger.error(f"Jellyfin同步处理出错: {e}")
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")


# 全局同步服务实例
sync_service = SyncService()
