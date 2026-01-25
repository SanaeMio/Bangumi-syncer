"""
Trakt 数据同步服务
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

from ...core.database import database_manager
from ...core.logging import logger
from ...models.sync import CustomItem
from ...services.sync_service import sync_service
from .auth import trakt_auth_service
from .client import TraktClient, TraktClientFactory
from .models import TraktHistoryItem, TraktSyncResult, TraktSyncStats


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
        config = trakt_auth_service.get_user_trakt_config(user_id)
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
            config = trakt_auth_service.get_user_trakt_config(user_id)

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
            stats = TraktSyncStats(
                total_items=0,
                movies=0,
                episodes=0,
                start_time=time.time(),
                end_time=None,
                duration=None,
            )
            synced_count = 0
            skipped_count = 0
            error_count = 0
            details = {}

            # 根据配置决定同步哪些类型
            if "history" in sync_types:
                logger.info(f"开始同步用户 {user_id} 的 Trakt 观看历史")
                history_result = await self._sync_watched_history(
                    user_id, client, config, full_sync
                )
                synced_count += history_result.synced_count
                skipped_count += history_result.skipped_count
                error_count += history_result.error_count
                details["history"] = history_result.details

            # if "ratings" in sync_types:
            #     logger.info(f"开始同步用户 {user_id} 的 Trakt 评分")
            #     ratings_result = await self._sync_ratings(
            #         user_id, client, config, full_sync
            #     )
            #     synced_count += ratings_result.synced_count
            #     skipped_count += ratings_result.skipped_count
            #     error_count += ratings_result.error_count
            #     details["ratings"] = ratings_result.details

            # if "collection" in sync_types:
            #     logger.info(f"开始同步用户 {user_id} 的 Trakt 收藏")
            #     collection_result = await self._sync_collection(
            #         user_id, client, config, full_sync
            #     )
            #     synced_count += collection_result.synced_count
            #     skipped_count += collection_result.skipped_count
            #     error_count += collection_result.error_count
            #     details["collection"] = collection_result.details

            # 更新最后同步时间（只要没有错误就更新）
            if error_count == 0 and config is not None:
                config.last_sync_time = int(time.time())
                database_manager.save_trakt_config(config.to_dict())

            stats.end_time = time.time()
            # 确保 start_time 不为 None
            assert stats.start_time is not None
            stats.duration = stats.end_time - stats.start_time
            stats.total_items = synced_count + skipped_count + error_count

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

            # 过滤出剧集（只同步剧集，不同步电影）, 并检查数据完整性
            episode_items = []
            for item in history_items:
                if item.type == "episode" and item.episode and item.show:
                    episode_items.append(item)
                else:
                    logger.warning(f"跳过非剧集或数据不完整的记录: {item}")
            skipped_count = len(history_items) - len(episode_items)
            logger.info(f"过滤后得到 {len(episode_items)} 条剧集记录")

            # 转换为 CustomItem 并同步
            synced_count = 0
            error_count = 0
            details = []

            for item in episode_items:
                try:
                    # 检查是否已同步过（避免重复同步）
                    if not self._should_sync_item(user_id, item):
                        skipped_count += 1
                        continue

                    # 转换为 CustomItem
                    custom_item = self._convert_trakt_history_to_custom_item(
                        user_id, item
                    )

                    if not custom_item:
                        skipped_count += 1
                        continue

                    # 调用现有同步服务
                    task_id = await sync_service.sync_custom_item_async(
                        custom_item, source="trakt"
                    )

                    # 记录同步历史
                    self._record_sync_history(user_id, item, task_id)

                    synced_count += 1
                    details.append(
                        {
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

    async def _sync_ratings(
        self, user_id: str, client: TraktClient, config, full_sync: bool
    ) -> TraktSyncResult:
        """同步评分"""
        # TODO: 实现评分同步
        logger.info(f"用户 {user_id} 的评分同步暂未实现")
        return TraktSyncResult(
            success=True,
            message="评分同步暂未实现",
            synced_count=0,
            skipped_count=0,
            error_count=0,
            details={},
        )

    async def _sync_collection(
        self, user_id: str, client: TraktClient, config, full_sync: bool
    ) -> TraktSyncResult:
        """同步收藏"""
        # TODO: 实现收藏同步
        logger.info(f"用户 {user_id} 的收藏同步暂未实现")
        return TraktSyncResult(
            success=True,
            message="收藏同步暂未实现",
            synced_count=0,
            skipped_count=0,
            error_count=0,
            details={},
        )

    def _should_sync_item(self, user_id: str, item: TraktHistoryItem) -> bool:
        """检查是否需要同步该项目（避免重复）"""
        try:
            # 获取用户的同步历史（最多1000条）
            history_data = database_manager.get_trakt_sync_history(
                user_id=user_id, limit=1000
            )

            if not history_data or "records" not in history_data:
                return True

            # 检查是否已存在相同记录
            for record in history_data["records"]:
                if (
                    record.get("trakt_item_id") == item.trakt_item_id
                    and record.get("watched_at") == item.watched_timestamp
                ):
                    return False

            return True
        except Exception as e:
            logger.warning(f"检查同步历史失败: {e}")
            return True

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
        self, user_id: str, item: TraktHistoryItem
    ) -> Optional[CustomItem]:
        """将 Trakt 观看历史转换为 CustomItem"""
        try:
            if item.type != "episode" or not item.episode or not item.show:
                logger.warning(f"非剧集类型或数据不完整: {item.type}")
                return None

            # 提取剧集信息
            show = item.show
            episode = item.episode

            # 获取剧集标题
            title = show.get("title", "")
            if not title:
                logger.warning(f"剧集标题为空: {item.trakt_item_id}")
                return None

            # 获取原始标题
            ori_title = show.get("original_title") or show.get("originalTitle") or title

            # 获取季和集数
            season = episode.get("season", 1)
            episode_num = episode.get("number", 1)

            # 获取发行日期（从剧集或剧中获取）
            release_date = ""
            if episode.get("first_aired"):
                release_date = episode["first_aired"]
            elif show.get("first_aired"):
                release_date = show["first_aired"]

            # 如果没有发行日期，使用观看日期
            if not release_date and item.watched_at:
                # 从 ISO 格式中提取日期部分
                try:
                    release_date = item.watched_at.split("T")[0]
                except:
                    release_date = ""

            # 构建 CustomItem
            return CustomItem(
                media_type="episode",
                title=title,
                ori_title=ori_title,
                season=season,
                episode=episode_num,
                release_date=release_date,
                user_name=user_id,  # 使用 user_id 作为 user_name
                source="trakt",
            )

        except Exception as e:
            logger.error(f"转换 Trakt 历史记录失败: {e}, 数据: {item}")
            return None

    async def start_user_sync_task(self, user_id: str, full_sync: bool = False) -> str:
        """启动用户同步任务（异步执行）

        Returns:
            任务ID
        """
        task_id = f"trakt_sync_{user_id}_{int(time.time())}"

        async def sync_task():
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
