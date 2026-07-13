"""单任务调度器抽象基类

提取 feiniu/fongmi scheduler 的公共逻辑（约 90% 代码相同）。
子类只需实现 4 个抽象方法：
  - JOB_ID: 任务唯一标识
  - DEFAULT_CRON: 默认 cron 表达式
  - _is_enabled(): 是否启用
  - _run_sync_job(): 执行同步任务
  - _get_driver_config(): 获取本驱动配置（含 sync_interval）
  - DRIVER_NAME: 驱动名称（用于日志）
"""

from __future__ import annotations

import abc
import asyncio

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ...core.config import config_manager
from ...core.logging import logger


class BaseScheduler(abc.ABC):
    """单任务调度器基类（feiniu/fongmi 模式）

    封装 APScheduler 的创建、start/stop、cron 解析、job 注册/刷新、
    配置保存后联动等公共逻辑。子类只需提供驱动特有信息。
    """

    # 子类必须覆盖的类属性
    JOB_ID: str = ""
    DEFAULT_CRON: str = "*/5 * * * *"
    DRIVER_NAME: str = ""  # 用于日志，如 "feiniu" / "fongmi"

    def __init__(self) -> None:
        self.scheduler: AsyncIOScheduler | None = None
        self._scheduler_config = config_manager.get_scheduler_config()

    # ===== 子类必须实现的抽象方法 =====

    @abc.abstractmethod
    def _is_enabled(self) -> bool:
        """判断本驱动是否启用"""

    @abc.abstractmethod
    async def _run_sync_job(self) -> None:
        """执行同步任务（由调度器定时调用）"""

    @abc.abstractmethod
    def _get_driver_config(self) -> dict:
        """获取本驱动配置（至少含 sync_interval 字段）"""

    # ===== 公共逻辑（子类无需覆盖）=====

    async def start(self) -> bool:
        name = self.DRIVER_NAME
        try:
            if self.scheduler and self.scheduler.running:
                if self._is_enabled():
                    self._schedule_or_refresh_job()
                else:
                    try:
                        self.scheduler.remove_job(self.JOB_ID)
                    except Exception:
                        pass
                return True

            if not self._is_enabled():
                logger.info(
                    f"{name} 同步未启用，本次不注册定时任务（APScheduler 未创建）"
                )
                return True

            self.scheduler = self._create_scheduler()
            self.scheduler.start()
            self._schedule_or_refresh_job()
            logger.info(f"{name} 调度器启动成功")
            return True
        except Exception as e:
            logger.error(f"启动 {name} 调度器失败: {e}")
            return False

    async def stop(self) -> bool:
        name = self.DRIVER_NAME
        try:
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
                self.scheduler = None
                logger.info(f"{name} 调度器已停止")
            return True
        except Exception as e:
            logger.error(f"停止 {name} 调度器失败: {e}")
            return False

    def _create_scheduler(self) -> AsyncIOScheduler:
        """创建 APScheduler 实例（子类可覆盖以自定义配置）"""
        return AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            executors={"default": AsyncIOExecutor()},
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 60,
            },
            timezone=self._scheduler_config.get("timezone", "Asia/Shanghai"),
        )

    def _default_cron_trigger(self) -> CronTrigger:
        """默认 cron trigger（由 DEFAULT_CRON 构造）"""
        return self._parse_cron(self.DEFAULT_CRON)

    def _parse_cron(self, cron_expression: str) -> CronTrigger:
        """解析 5 段式 cron 表达式，无效时降级到默认"""
        parts = cron_expression.split()
        if len(parts) != 5:
            logger.warning(
                f"{self.DRIVER_NAME} Cron 无效，使用默认 {self.DEFAULT_CRON}: {cron_expression}"
            )
            return self._default_cron_trigger()
        return CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

    def _schedule_or_refresh_job(self) -> None:
        """从配置读取 cron 并注册/刷新定时任务"""
        if not self.scheduler or not self.scheduler.running:
            return
        cfg = self._get_driver_config()
        cron_expr = cfg.get("sync_interval") or self.DEFAULT_CRON
        trigger = self._parse_cron(str(cron_expr))

        try:
            self.scheduler.remove_job(self.JOB_ID)
        except Exception:
            pass

        self.scheduler.add_job(
            func=self._run_sync_job,
            trigger=trigger,
            id=self.JOB_ID,
            name=f"{self.DRIVER_NAME} sync",
            replace_existing=True,
        )
        logger.info(f"{self.DRIVER_NAME} 定时任务已注册: {cron_expr}")

    def reload_job_if_running(self) -> None:
        """配置保存后若调度器在运行，则按新 Cron 重建任务"""
        if self.scheduler and self.scheduler.running and self._is_enabled():
            self._schedule_or_refresh_job()

    async def apply_config_after_save(self) -> None:
        """保存 config.ini 后同步调度状态（与 Web 配置联动）"""
        if self._is_enabled():
            if not self.scheduler or not self.scheduler.running:
                await self.start()
            else:
                self._schedule_or_refresh_job()
        else:
            await self.stop()

    def _run_sync_with_timeout(self, coro) -> None:
        """带超时执行同步任务的辅助方法（供子类 _run_sync_job 调用）

        用法：
            async def _run_sync_job(self):
                if not self._is_enabled():
                    return
                await self._run_sync_with_timeout(my_sync_service.run_sync())
        """
        timeout = self._scheduler_config.get("job_timeout", 300)

        async def _wrapper():
            try:
                await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.error(f"{self.DRIVER_NAME} 定时同步超时 ({timeout} 秒)")
            except Exception as e:
                logger.error(f"{self.DRIVER_NAME} 定时同步失败: {e}")

        return asyncio.ensure_future(_wrapper())
