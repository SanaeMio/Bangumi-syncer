"""
Trakt 数据同步服务
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from app.utils.bangumi_data import bangumi_data

from ...core.database import database_manager
from ...core.logging import logger
from ...models.sync import CustomItem
from ...services.mapping_service import mapping_service
from ...services.sync_service import sync_service
from ...utils.media_type_detector import detect_media_type
from ...utils.notifier import send_notify
from .auth import trakt_auth_service
from .client import TraktClient, TraktClientFactory
from .models import TraktHistoryItem, TraktSyncResult


class TraktSyncService:
    """Trakt 数据同步服务"""

    def __init__(self):
        self._active_syncs: dict[str, asyncio.Task] = {}
        self._sync_results: dict[str, TraktSyncResult] = {}

    async def sync_user_trakt_data(
        self,
        user_id: str,
        full_sync: bool = False,
        sync_types: Optional[list[str]] = None,
    ) -> TraktSyncResult:
        """同步用户的 Trakt 数据

        Args:
            user_id: 用户ID
            full_sync: 是否全量同步（忽略上次同步时间）
            sync_types: 同步类型列表，可选 ['history', 'ratings', 'collection']

        Returns:
            同步结果
        """
        if sync_types is None:
            sync_types = ["history"]  # 默认只同步观看历史

        # 获取用户的 Trakt 配置
        config = await asyncio.to_thread(
            trakt_auth_service.get_user_trakt_config, user_id
        )
        if config is None:
            return TraktSyncResult(
                success=False,
                message="Trakt 配置不存在",
                error_count=1,
                synced_count=0,
                skipped_count=0,
                details={},
            )

        if not config.access_token:
            return TraktSyncResult(
                success=False,
                message="Trakt 未授权，请先完成 OAuth 授权",
                error_count=1,
                synced_count=0,
                skipped_count=0,
                details={},
            )

        # 检查令牌是否过期
        if config.is_token_expired():
            logger.warning(f"用户 {user_id} 的 Trakt 令牌已过期，尝试刷新")
            success = await trakt_auth_service.refresh_token(user_id)
            if not success:
                return TraktSyncResult(
                    success=False,
                    message="Trakt 令牌过期且刷新失败",
                    error_count=1,
                    synced_count=0,
                    skipped_count=0,
                    details={},
                )
            # 刷新后重新获取配置
            config = await asyncio.to_thread(
                trakt_auth_service.get_user_trakt_config, user_id
            )

        # 创建 Trakt 客户端
        if config is None:
            return TraktSyncResult(
                success=False,
                message="Trakt 配置不存在",
                error_count=1,
                synced_count=0,
                skipped_count=0,
                details={},
            )

        client = await TraktClientFactory.create_client(config.access_token)
        if not client:
            return TraktSyncResult(
                success=False,
                message="创建 Trakt 客户端失败",
                error_count=1,
                synced_count=0,
                skipped_count=0,
                details={},
            )

        try:
            synced_count = 0
            skipped_count = 0
            error_count = 0
            details = {}

            # 同步观看历史（评分与收藏同步暂未实现）
            if "history" in sync_types:
                logger.info(f"开始同步用户 {user_id} 的 Trakt 观看历史")
                history_result = await self._sync_watched_history(
                    user_id, client, config, full_sync
                )
                synced_count += history_result.synced_count
                skipped_count += history_result.skipped_count
                error_count += history_result.error_count
                details["history"] = history_result.details

            # 更新最后同步时间（只要没有错误就更新）
            if error_count == 0 and config is not None:
                config.last_sync_time = int(time.time())
                await asyncio.to_thread(
                    database_manager.save_trakt_config, config.to_dict()
                )

            success = error_count == 0

            return TraktSyncResult(
                success=success,
                message=f"同步完成: {synced_count} 成功, {skipped_count} 跳过, {error_count} 失败",
                synced_count=synced_count,
                skipped_count=skipped_count,
                error_count=error_count,
                details=details,
            )

        except Exception as e:
            logger.error(f"同步 Trakt 数据失败: {e}")
            return TraktSyncResult(
                success=False,
                message=f"同步失败: {str(e)}",
                error_count=1,
                synced_count=0,
                skipped_count=0,
                details={},
            )
        finally:
            await client.close()

    def _filter_already_synced(
        self,
        syncable_items: list[TraktHistoryItem],
        full_sync: bool,
        synced_set: set,
    ) -> tuple[list[TraktHistoryItem], int]:
        """增量同步时过滤已同步记录，减少后续 API 请求。

        返回 (pending_items, skipped_count_increment)。
        """
        pending_items: list[TraktHistoryItem] = []
        skipped_count = 0
        for item in syncable_items:
            if (
                not full_sync
                and (item.trakt_item_id, item.watched_timestamp) in synced_set
            ):
                skipped_count += 1
            else:
                pending_items.append(item)
        logger.info(f"去重后剩余 {len(pending_items)} 条待同步记录")
        return pending_items, skipped_count

    def _collect_detail_fetch_tasks(
        self,
        pending_items: list[TraktHistoryItem],
        sync_filter_enabled: bool,
        show_original_titles: dict[str, str],
        show_genres: dict[str, list[str]],
    ) -> list[tuple[str, str, int]]:
        """第一阶段：TMDB 过滤 + 收集需要 API 请求的 tid。

        返回 fetch_tasks 列表 (tid, item_type, trakt_id_int)。
        命中 bangumi_data TMDB ID 的条目会直接写入 show_genres。
        """
        fetch_tasks: list[tuple[str, str, int]] = []  # (tid, item_type, trakt_id_int)
        for item in pending_items:
            trakt_id = None
            need_details = sync_filter_enabled
            if item.type == "episode" and item.show:
                show = item.show
                trakt_id = show.get("ids", {}).get("trakt")
                if not show.get("original_title") and not show.get("originalTitle"):
                    need_details = True
            elif item.type == "movie" and item.movie:
                trakt_id = item.movie.get("ids", {}).get("trakt")
            if not trakt_id:
                continue
            tid = str(trakt_id)
            if need_details and tid not in show_original_titles:
                # bangumi_data 仅收录动画条目，TMDB 命中则确认为动画，跳过 Trakt 详情请求
                if sync_filter_enabled:
                    if item.type == "episode" and item.show:
                        show = item.show
                        tmdb_id = show.get("ids", {}).get("tmdb")
                        ep_data = item.episode or {}
                        if tmdb_id and bangumi_data.get_title_by_tmdb_id(
                            f"tv/{tmdb_id}", season=ep_data.get("season")
                        ):
                            logger.debug(
                                f"命中 bangumi_data 中 TMDB ID，跳过 Trakt 详情请求: tv/{tmdb_id}"
                            )
                            show_genres[tid] = ["anime"]
                            continue
                    elif item.type == "movie" and item.movie:
                        tmdb_id = item.movie.get("ids", {}).get("tmdb")
                        if tmdb_id and bangumi_data.get_title_by_tmdb_id(
                            f"movie/{tmdb_id}"
                        ):
                            logger.debug(
                                f"命中 bangumi_data 中 TMDB ID，跳过 Trakt 详情请求: movie/{tmdb_id}"
                            )
                            show_genres[tid] = ["anime"]
                            continue
                fetch_tasks.append((tid, item.type, trakt_id))
        return fetch_tasks

    async def _fetch_details_batch(
        self,
        fetch_tasks: list[tuple[str, str, int]],
        client: TraktClient,
        sync_filter_enabled: bool,
        show_original_titles: dict[str, str],
        show_genres: dict[str, list[str]],
    ) -> None:
        """第二阶段：并发获取详情（Semaphore 控制并发避免触发速率限制）。

        将结果写入 show_original_titles 与 show_genres。
        """
        if not fetch_tasks:
            return
        sem = asyncio.Semaphore(5)

        async def _fetch_one(
            tid: str, item_type: str, trakt_id_int: int
        ) -> tuple[str, Optional[dict]]:
            async with sem:
                if item_type == "episode":
                    resp = await client.get_show_info(tid)
                else:
                    resp = await client.get_movie_info(trakt_id_int)
                return tid, resp

        results = await asyncio.gather(
            *[_fetch_one(t, it, tid_int) for t, it, tid_int in fetch_tasks],
            return_exceptions=True,
        )
        for tid, resp in results:
            if isinstance(resp, Exception):
                logger.debug(f"获取 Trakt 详情失败: {resp}")
                continue
            if resp:
                ot = resp.get("original_title")
                if ot:
                    show_original_titles[tid] = ot
                if sync_filter_enabled:
                    show_genres[tid] = resp.get("genres", [])

    def _apply_genre_filter(
        self,
        item: TraktHistoryItem,
        sync_filter_enabled: bool,
        show_genres: dict[str, list[str]],
        show_original_titles: dict[str, str],
        custom_mappings: dict,
    ) -> bool:
        """类型过滤：仅同步动画类型，除非命中映射。

        返回 True 表示应跳过该条目。
        """
        if not sync_filter_enabled:
            return False
        if item.type == "episode":
            show = item.show
            item_title = (show or {}).get("title", "")
            trakt_id = (show or {}).get("ids", {}).get("trakt")
        else:
            m = item.movie or {}
            item_title = m.get("title", "")
            trakt_id = m.get("ids", {}).get("trakt")
        tid = str(trakt_id) if trakt_id else ""
        genres = show_genres.get(tid, []) if tid else []
        ori_title = show_original_titles.get(tid, "") if tid else ""
        if genres and not (
            any(g in genres for g in ("anime", "donghua", "animation"))
            or item_title in custom_mappings
            or ori_title in custom_mappings
        ):
            logger.debug(
                f"Trakt 类型过滤——跳过非动画条目: {item_title} (genres={genres})"
            )
            return True
        return False

    async def _convert_and_sync_items(
        self,
        user_id: str,
        pending_items: list[TraktHistoryItem],
        sync_filter_enabled: bool,
        show_genres: dict[str, list[str]],
        show_original_titles: dict[str, str],
        custom_mappings: dict,
    ) -> tuple[int, int, int, list[dict]]:
        """转换为 CustomItem 并同步。

        返回 (synced_count, skipped_count, error_count, details)。
        """
        synced_count = 0
        skipped_count = 0
        error_count = 0
        details: list[dict] = []

        for item in pending_items:
            try:
                # 类型过滤：仅同步动画类型，除非命中映射
                if self._apply_genre_filter(
                    item,
                    sync_filter_enabled,
                    show_genres,
                    show_original_titles,
                    custom_mappings,
                ):
                    skipped_count += 1
                    continue

                # 转换为 CustomItem
                custom_item = self._convert_trakt_history_to_custom_item(
                    user_id, item, show_original_titles
                )

                if not custom_item:
                    await asyncio.to_thread(
                        self._report_preconvert_failure,
                        user_id,
                        item,
                        "Trakt 记录缺少可用标题，无法转为同步条目",
                    )
                    error_count += 1
                    continue

                # 调用现有同步服务
                task_id = await sync_service.sync_custom_item_async(
                    custom_item, source="trakt"
                )

                # 记录同步历史
                await asyncio.to_thread(
                    self._record_sync_history, user_id, item, task_id
                )

                synced_count += 1
                details.append(
                    {
                        "media_type": custom_item.media_type,
                        "title": custom_item.title,
                        "season": custom_item.season,
                        "episode": custom_item.episode,
                        "task_id": task_id,
                    }
                )

                # 小延迟避免请求过快
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.error(f"同步单个观看历史失败: {e}")
                error_count += 1

        return synced_count, skipped_count, error_count, details

    async def _sync_watched_history(
        self, user_id: str, client: TraktClient, config, full_sync: bool
    ) -> TraktSyncResult:
        """同步观看历史"""
        try:
            # 计算开始日期（用于增量同步）
            start_date = None
            if not full_sync and config.last_sync_time:
                # 转换为 datetime，减去一天作为缓冲
                last_sync_dt = datetime.fromtimestamp(config.last_sync_time)
                start_date = last_sync_dt - timedelta(days=1)
                logger.info(f"增量同步，从 {start_date.date()} 开始")

            # 获取所有观看历史（自动分页）
            history_items = await client.get_all_watched_history(start_date=start_date)

            if not history_items:
                return TraktSyncResult(
                    success=True,
                    message="没有新的观看历史需要同步",
                    synced_count=0,
                    skipped_count=0,
                    error_count=0,
                    details={},
                )

            logger.info(f"获取到 {len(history_items)} 条观看历史记录")

            # 一次性加载已同步集合，后续 O(1) 查找（消除 N+1 查询）
            synced_set = await asyncio.to_thread(
                database_manager.get_trakt_synced_set, user_id
            )

            # 过滤：剧集需 episode+show；电影需 movie 且至少有标题或 TMDB（用于匹配 bangumi）
            syncable_items: list[TraktHistoryItem] = []
            skipped_count = 0
            error_count = 0
            for item in history_items:
                if item.type == "episode" and item.episode and item.show:
                    syncable_items.append(item)
                elif item.type == "movie" and item.movie:
                    m = item.movie
                    has_title = bool((m.get("title") or "").strip())
                    has_tmdb = m.get("ids", {}).get("tmdb") is not None
                    if has_title or has_tmdb:
                        syncable_items.append(item)
                    else:
                        logger.warning(f"跳过缺少标题与 TMDB 的电影记录: {item}")
                        self._report_preconvert_failure(
                            user_id,
                            item,
                            "电影记录缺少标题与 TMDB ID，无法转为同步条目",
                        )
                        error_count += 1
                else:
                    logger.warning(f"跳过不支持的类型或数据不完整的记录: {item}")
                    skipped_count += 1
            logger.info(f"过滤后得到 {len(syncable_items)} 条可同步记录（剧集 + 电影）")

            # 增量同步时先过滤已同步记录，减少后续 API 请求
            pending_items, dedup_skipped = self._filter_already_synced(
                syncable_items, full_sync, synced_set
            )
            skipped_count += dedup_skipped

            # 预先收集 sync/history 不包含 original_title 的节目，
            # 通过 /shows/:id?extended=full 获取日语原名；
            # 同时收集 genre 用于类型过滤
            show_original_titles: dict[str, str] = {}
            show_genres: dict[str, list[str]] = {}
            sync_filter_enabled = getattr(config, "sync_filter_enabled", True)
            custom_mappings = (
                await asyncio.to_thread(mapping_service.load_custom_mappings)
                if sync_filter_enabled
                else {}
            )

            # 第一阶段：TMDB 过滤 + 收集需要 API 请求的 tid
            fetch_tasks = self._collect_detail_fetch_tasks(
                pending_items, sync_filter_enabled, show_original_titles, show_genres
            )

            # 第二阶段：并发获取详情（Semaphore 控制并发避免触发速率限制）
            await self._fetch_details_batch(
                fetch_tasks,
                client,
                sync_filter_enabled,
                show_original_titles,
                show_genres,
            )

            # 转换为 CustomItem 并同步
            (
                synced_count,
                sync_skipped,
                sync_error,
                details,
            ) = await self._convert_and_sync_items(
                user_id,
                pending_items,
                sync_filter_enabled,
                show_genres,
                show_original_titles,
                custom_mappings,
            )
            skipped_count += sync_skipped
            error_count += sync_error

            return TraktSyncResult(
                success=error_count == 0,
                message=f"观看历史同步: {synced_count} 成功, {skipped_count} 跳过, {error_count} 失败",
                synced_count=synced_count,
                skipped_count=skipped_count,
                error_count=error_count,
                details={"items": details},
            )

        except Exception as e:
            logger.error(f"同步观看历史失败: {e}")
            return TraktSyncResult(
                success=False,
                message=f"同步观看历史失败: {str(e)}",
                error_count=1,
                synced_count=0,
                skipped_count=0,
                details={},
            )

    def _trakt_item_failure_context(
        self, user_id: str, item: TraktHistoryItem, reason: str
    ) -> dict[str, Any]:
        """从 Trakt 历史条目提取失败通知与 sync_records 共用字段。"""
        title = "unknown"
        ori_title = ""
        season = 0
        episode = 0
        media_type = "episode"

        if item.type == "episode" and item.show:
            show = item.show
            ep = item.episode or {}
            title = (
                show.get("title")
                or show.get("original_title")
                or show.get("originalTitle")
                or item.trakt_item_id
                or "unknown"
            )
            ori_raw = show.get("original_title") or show.get("originalTitle") or ""
            ori_title = ori_raw if str(ori_raw).strip() else ""
            season = int(ep.get("season") or 0)
            episode = int(ep.get("number") or 0)
            media_type = "episode"
        elif item.type == "movie" and item.movie:
            movie = item.movie
            raw_title = (movie.get("title") or "").strip()
            title = raw_title or item.trakt_item_id or "unknown"
            ori_raw = movie.get("original_title") or movie.get("originalTitle") or ""
            ori_title = ori_raw if str(ori_raw).strip() else ""
            season = 1
            episode = 1
            media_type = "movie"
        else:
            title = item.trakt_item_id or "unknown"

        return {
            "user_name": user_id,
            "title": str(title),
            "ori_title": ori_title,
            "season": season,
            "episode": episode,
            "media_type": media_type,
            "source": "trakt",
            "error_message": reason,
        }

    def _report_preconvert_failure(
        self, user_id: str, item: TraktHistoryItem, reason: str
    ) -> None:
        """Trakt 转 CustomItem 前失败：webhook/邮件 + 应用内 sync_records。"""
        ctx = self._trakt_item_failure_context(user_id, item, reason)
        send_notify(
            "mark_failed",
            item=None,
            source="trakt",
            error_type="trakt_title_unresolved",
            user_name=ctx["user_name"],
            title=ctx["title"],
            ori_title=ctx["ori_title"],
            season=ctx["season"],
            episode=ctx["episode"],
            error_message=reason,
        )
        database_manager.log_sync_record(
            user_name=ctx["user_name"],
            title=ctx["title"],
            ori_title=ctx["ori_title"],
            season=ctx["season"],
            episode=ctx["episode"],
            status="error",
            message=reason,
            source="trakt",
            media_type=ctx["media_type"],
        )

    def _record_sync_history(
        self, user_id: str, item: TraktHistoryItem, task_id: str
    ) -> None:
        """记录同步历史到数据库"""
        try:
            # 使用数据库管理器的保存方法
            history_data = {
                "user_id": user_id,
                "trakt_item_id": item.trakt_item_id,
                "media_type": item.type,
                "watched_at": item.watched_timestamp,  # Use the timestamp property for consistency
                "synced_at": int(time.time()),
                "task_id": task_id,
            }

            success = database_manager.save_trakt_sync_history(history_data)
            if not success:
                logger.error("保存同步历史失败")

        except Exception as e:
            logger.error(f"记录同步历史失败: {e}")

    def _convert_trakt_history_to_custom_item(
        self,
        user_id: str,
        item: TraktHistoryItem,
        show_original_titles: Optional[dict[str, str]] = None,
    ) -> Optional[CustomItem]:
        """将 Trakt 观看历史转换为 CustomItem"""
        try:
            if item.type == "movie":
                return self._trakt_movie_history_to_custom_item(user_id, item)

            if item.type != "episode" or not item.episode or not item.show:
                logger.warning(f"非剧集类型或数据不完整: {item.type}")
                return None

            # 提取剧集信息
            show = item.show
            episode = item.episode

            # 获取剧集标题
            # 优先从 bangumi_data 获取中文标题；未收录时降级使用 Trakt 自带标题
            title: Optional[str] = None

            show_tmdb = show.get("ids", {}).get("tmdb")
            if show_tmdb is not None:
                title = bangumi_data.get_title_by_tmdb_id(
                    f"tv/{show_tmdb}", season=episode.get("season")
                )

            if not title:
                # bangumi_data 未收录时，按日文原名 → 预取原文 → 英文标题顺序降级
                title = (
                    show.get("original_title")
                    or show.get("originalTitle")
                    or (show_original_titles or {}).get(
                        str(show.get("ids", {}).get("trakt", ""))
                    )
                    or show.get("title")
                )

            if not title:
                logger.warning(f"剧集标题为空: {item.trakt_item_id}")
                return None

            # 获取原始标题，填入 Trakt 英文标题供自定义映射与 bangumi-data 备选匹配
            ori_title = show.get("title") or title

            # 获取季和集数
            season = episode.get("season", 1)
            episode_num = episode.get("number", 1)

            # 获取发行日期：episode.first_aired → show.first_aired → bangumi_data begin → show.year → watched_at
            release_date = ""
            if episode.get("first_aired"):
                release_date = episode["first_aired"]
            elif show.get("first_aired"):
                release_date = show["first_aired"]
            elif show_tmdb is not None:
                bgm_begin = bangumi_data.get_begin_by_tmdb_id(
                    f"tv/{show_tmdb}", season=episode.get("season")
                )
                if bgm_begin and isinstance(bgm_begin, str):
                    release_date = bgm_begin[:10]
            if not release_date and show.get("year"):
                release_date = f"{show['year']}-01-01"

            if not release_date and item.watched_at:
                try:
                    release_date = item.watched_at.split("T")[0]
                except Exception:
                    release_date = ""

            # 检测 OVA/OAD/三次元类型
            detected = detect_media_type(
                title=title, ori_title=ori_title or "", item_type="episode"
            )

            # 构建 CustomItem
            return CustomItem(
                media_type=detected,
                title=title,
                ori_title=ori_title,
                season=season,
                episode=episode_num,
                release_date=release_date,
                user_name=user_id,  # 使用 user_id 作为 user_name
                source="trakt",
                raw_payload={
                    "source": "trakt",
                    "history_kind": "episode",
                    "watched_at": item.watched_at,
                    "show": {
                        "title": show.get("title"),
                        "year": show.get("year"),
                        "ids": show.get("ids", {}),
                    },
                    "episode": {
                        "season": episode.get("season"),
                        "number": episode.get("number"),
                        "title": episode.get("title"),
                        "first_aired": episode.get("first_aired"),
                    },
                },
            )

        except Exception as e:
            logger.error(f"转换 Trakt 历史记录失败: {e}, 数据: {item}")
            return None

    def _trakt_movie_history_to_custom_item(
        self, user_id: str, item: TraktHistoryItem
    ) -> Optional[CustomItem]:
        """将 Trakt 电影观看历史转为 CustomItem（剧场版 / 独立电影打格子）"""
        if not item.movie:
            logger.warning(f"电影数据缺失: {item.trakt_item_id}")
            return None
        movie = item.movie
        ids = movie.get("ids") or {}
        tmdb_num = ids.get("tmdb")

        title: Optional[str] = None
        if tmdb_num is not None:
            title = bangumi_data.get_title_by_tmdb_id(f"movie/{tmdb_num}")
            if not title:
                title = bangumi_data.get_title_by_tmdb_id(str(tmdb_num))
        if not title:
            title = (movie.get("title") or "").strip()
        if not title:
            logger.warning(f"电影标题为空: {item.trakt_item_id}")
            return None

        ori_title = movie.get("title") or title

        release_date = ""
        if movie.get("released"):
            release_date = str(movie["released"])[:10]
        elif movie.get("year"):
            release_date = f"{movie['year']}-01-01"
        elif item.watched_at:
            try:
                release_date = item.watched_at.split("T")[0]
            except Exception:
                release_date = ""

        # 电影也检测是否为真人电影（三次元）
        detected = detect_media_type(
            title=title, ori_title=ori_title or "", item_type="movie"
        )

        return CustomItem(
            media_type=detected,
            title=title,
            ori_title=ori_title,
            season=1,
            episode=1,
            release_date=release_date,
            user_name=user_id,
            source="trakt",
            raw_payload={
                "source": "trakt",
                "history_kind": "movie",
                "watched_at": item.watched_at,
                "movie": {
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "released": movie.get("released"),
                    "ids": ids,
                },
            },
        )

    async def start_user_sync_task(self, user_id: str, full_sync: bool = False) -> str:
        """启动用户同步任务（异步执行）

        Returns:
            任务ID
        """
        task_id = f"trakt_sync_{user_id}_{int(time.time())}"

        async def sync_task() -> None:
            try:
                result = await self.sync_user_trakt_data(user_id, full_sync)
                self._sync_results[task_id] = result
                logger.info(f"Trakt 同步任务 {task_id} 完成: {result.message}")
            except Exception as e:
                logger.error(f"Trakt 同步任务 {task_id} 失败: {e}")
                self._sync_results[task_id] = TraktSyncResult(
                    success=False,
                    message=f"任务执行失败: {str(e)}",
                    error_count=1,
                    synced_count=0,
                    skipped_count=0,
                    details={},
                )
            finally:
                # 清理任务记录
                await asyncio.sleep(300)  # 5分钟后清理
                if task_id in self._active_syncs:
                    del self._active_syncs[task_id]

        # 创建并运行任务
        task = asyncio.create_task(sync_task())
        self._active_syncs[task_id] = task

        logger.info(f"Trakt 同步任务 {task_id} 已启动")
        return task_id

    def get_sync_result(self, task_id: str) -> Optional[TraktSyncResult]:
        """获取同步任务结果"""
        return self._sync_results.get(task_id)

    def get_active_sync_tasks(self) -> dict[str, str]:
        """获取活跃的同步任务"""
        return {
            task_id: "running"
            for task_id, task in self._active_syncs.items()
            if not task.done()
        }


# 全局 Trakt 同步服务实例
trakt_sync_service = TraktSyncService()
