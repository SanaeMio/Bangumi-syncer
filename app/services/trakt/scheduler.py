"""
Trakt 数据同步调度器
"""

import asyncio
import time
from datetime import datetime
from typing import Optional

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ...core.config import config_manager
from ...core.database import database_manager
from ...core.logging import logger
from ...models.trakt import TraktConfig
from .auth import trakt_auth_service
from .sync_service import trakt_sync_service


class TraktScheduler:
    """Trakt 数据同步调度器"""

    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self.scheduler_config = config_manager.get_scheduler_config()
        self._user_jobs: dict[str, str] = {}  # user_id -> job_id

    def start(self) -> bool:
        """启动调度器"""
        try:
            if self.scheduler and self.scheduler.running:
                logger.warning("调度器已在运行")
                return True

            # 配置作业存储和执行器
            jobstores = {"default": MemoryJobStore()}
            executors = {
                "default": ThreadPoolExecutor(
                    self.scheduler_config.get("max_concurrent_syncs", 3)
                )
            }
            job_defaults = {
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 60,
            }

            # 创建调度器实例
            self.scheduler = AsyncIOScheduler(
                jobstores=jobstores,
                executors=executors,
                job_defaults=job_defaults,
                timezone="Asia/Shanghai",  # 使用中国时区
            )

            # 启动调度器
            self.scheduler.start()
            logger.info("Trakt 调度器启动成功")

            # 为所有启用同步的用户创建定时任务
            self._schedule_all_users()

            return True

        except Exception as e:
            logger.error(f"启动调度器失败: {e}")
            return False

    def stop(self) -> bool:
        """停止调度器"""
        try:
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
                self.scheduler = None
                self._user_jobs.clear()
                logger.info("Trakt 调度器已停止")
            return True
        except Exception as e:
            logger.error(f"停止调度器失败: {e}")
            return False

    def _schedule_all_users(self) -> None:
        """为所有启用同步的用户创建定时任务"""
        try:
            # 获取所有启用同步的 Trakt 配置
            configs = database_manager.get_trakt_configs_with_sync_enabled()

            if not configs:
                logger.info("没有启用 Trakt 同步的用户")
                return

            logger.info(f"为 {len(configs)} 个用户创建定时任务")

            for config_dict in configs:
                config = TraktConfig.from_dict(config_dict)
                if not config or not config.user_id or not config.sync_interval:
                    logger.warning(f"无效的 Trakt 配置，跳过: {config_dict}")
                    continue
                self.add_user_job(config.user_id, config.sync_interval)

        except Exception as e:
            logger.error(f"调度所有用户失败: {e}")

    def add_user_job(self, user_id: str, cron_expression: str) -> bool:
        """为用户添加定时任务"""
        try:
            if not self.scheduler:
                logger.error("调度器未初始化")
                return False

            # 如果用户已有任务，先移除
            if user_id in self._user_jobs:
                self.remove_user_job(user_id)

            # 解析 Cron 表达式
            try:
                # Cron 表达式格式: minute hour day month day_of_week
                parts = cron_expression.split()
                if len(parts) != 5:
                    raise ValueError(f"无效的 Cron 表达式: {cron_expression}")

                trigger = CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                )
            except Exception as e:
                logger.error(f"解析 Cron 表达式失败: {cron_expression}, 错误: {e}")
                # 使用默认间隔: 每6小时
                trigger = CronTrigger(hour="*/6")

            # 创建任务
            job = self.scheduler.add_job(
                func=self._sync_user_data_wrapper,
                trigger=trigger,
                args=[user_id],
                id=f"trakt_sync_{user_id}",
                name=f"Trakt Sync - {user_id}",
                replace_existing=True,
            )

            self._user_jobs[user_id] = job.id

            # 计算下次执行时间
            next_run = job.next_run_time
            next_run_str = (
                next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "未知"
            )

            logger.info(f"为用户 {user_id} 创建定时任务成功，下次执行: {next_run_str}")
            return True

        except Exception as e:
            logger.error(f"为用户 {user_id} 添加定时任务失败: {e}")
            return False

    def remove_user_job(self, user_id: str) -> bool:
        """移除用户的定时任务"""
        try:
            job_id = self._user_jobs.get(user_id)
            if not job_id:
                return True

            # 移除任务
            if self.scheduler:
                self.scheduler.remove_job(job_id)
                logger.info(f"移除用户 {user_id} 的定时任务成功")
            else:
                logger.warning(f"调度器未初始化，无法移除用户 {user_id} 的定时任务")

            del self._user_jobs[user_id]

            return True

        except Exception as e:
            logger.error(f"移除用户 {user_id} 的定时任务失败: {e}")
            return False

    def update_user_job(self, user_id: str, cron_expression: str) -> bool:
        """更新用户的定时任务"""
        try:
            # 先移除旧任务，再添加新任务
            self.remove_user_job(user_id)
            return self.add_user_job(user_id, cron_expression)
        except Exception as e:
            logger.error(f"更新用户 {user_id} 的定时任务失败: {e}")
            return False

    async def _sync_user_data_wrapper(self, user_id: str) -> None:
        """定时任务包装器，处理异常和超时"""
        try:
            # 设置任务超时
            timeout = self.scheduler_config.get("job_timeout", 300)

            await asyncio.wait_for(self.sync_user_data(user_id), timeout=timeout)

        except asyncio.TimeoutError:
            logger.error(f"用户 {user_id} 的同步任务超时 ({timeout}秒)")
        except Exception as e:
            logger.error(f"用户 {user_id} 的同步任务执行失败: {e}")

    async def sync_user_data(self, user_id: str) -> None:
        """执行用户数据同步（定时任务回调）"""
        start_time = time.time()
        logger.info(f"开始执行用户 {user_id} 的 Trakt 数据同步")

        try:
            # 获取用户配置
            config_dict = database_manager.get_trakt_config(user_id)
            if not config_dict:
                logger.error(f"用户 {user_id} 的 Trakt 配置未找到")
                return

            config = TraktConfig.from_dict(config_dict)

            # 检查是否启用
            if not config.enabled:
                logger.info(f"用户 {user_id} 的 Trakt 同步已禁用，跳过")
                return

            # 检查令牌是否需要刷新
            if config.is_token_expired():
                logger.info(f"用户 {user_id} 的 Trakt 令牌已过期，尝试刷新")
                success = await trakt_auth_service.refresh_token(user_id)
                if not success:
                    logger.error(f"用户 {user_id} 的 Trakt 令牌刷新失败，跳过同步")
                    return
                # 刷新后重新获取配置
                config_dict = database_manager.get_trakt_config(user_id)
                if not config_dict:
                    logger.error(f"用户 {user_id} 的 Trakt 配置刷新后未找到")
                    return
                config = TraktConfig.from_dict(config_dict)

            # 调用 Trakt 同步服务执行同步
            result = await trakt_sync_service.sync_user_trakt_data(
                user_id=user_id,
                full_sync=False,  # 定时任务使用增量同步
            )

            if result.success:
                logger.info(f"用户 {user_id} 的 Trakt 数据同步成功: {result.message}")
            else:
                logger.error(f"用户 {user_id} 的 Trakt 数据同步失败: {result.message}")

            elapsed_time = time.time() - start_time
            logger.info(
                f"用户 {user_id} 的 Trakt 数据同步完成，耗时: {elapsed_time:.2f}秒"
            )

        except Exception as e:
            logger.error(f"用户 {user_id} 的 Trakt 数据同步失败: {e}")
            elapsed_time = time.time() - start_time
            logger.info(f"用户 {user_id} 的同步任务结束，耗时: {elapsed_time:.2f}秒")

    def get_user_job_status(self, user_id: str) -> Optional[dict]:
        """获取用户的定时任务状态"""
        try:
            if not self.scheduler:
                return None

            job_id = self._user_jobs.get(user_id)
            if not job_id:
                return None

            job = self.scheduler.get_job(job_id)
            if not job:
                return None

            return {
                "job_id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.timestamp()
                if job.next_run_time
                else None,
                "trigger": str(job.trigger),
                "pending": job.pending,
            }
        except Exception as e:
            logger.error(f"获取用户 {user_id} 的任务状态失败: {e}")
            return None

    def get_all_jobs_status(self) -> dict[str, dict]:
        """获取所有定时任务状态"""
        status = {}
        for user_id in self._user_jobs.keys():
            job_status = self.get_user_job_status(user_id)
            if job_status:
                status[user_id] = job_status
        return status

    def trigger_user_sync(self, user_id: str) -> bool:
        """立即触发用户的同步任务"""
        try:
            if not self.scheduler:
                return False

            job_id = self._user_jobs.get(user_id)
            if not job_id:
                return False

            # 触发任务执行
            job = self.scheduler.get_job(job_id)
            if job:
                job.modify(next_run_time=datetime.now())
                logger.info(f"已触发用户 {user_id} 的同步任务")
                return True
            else:
                logger.error(f"未找到用户 {user_id} 的定时任务")
                return False

        except Exception as e:
            logger.error(f"触发用户 {user_id} 的同步任务失败: {e}")
            return False

    def pause_user_job(self, user_id: str) -> bool:
        """暂停用户的定时任务"""
        try:
            if not self.scheduler:
                return False

            job_id = self._user_jobs.get(user_id)
            if not job_id:
                return False

            job = self.scheduler.get_job(job_id)
            if job:
                job.pause()
                logger.info(f"已暂停用户 {user_id} 的定时任务")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"暂停用户 {user_id} 的定时任务失败: {e}")
            return False

    def resume_user_job(self, user_id: str) -> bool:
        """恢复用户的定时任务"""
        try:
            if not self.scheduler:
                return False

            job_id = self._user_jobs.get(user_id)
            if not job_id:
                return False

            job = self.scheduler.get_job(job_id)
            if job:
                job.resume()
                logger.info(f"已恢复用户 {user_id} 的定时任务")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"恢复用户 {user_id} 的定时任务失败: {e}")
            return False


# 全局调度器实例
trakt_scheduler = TraktScheduler()
