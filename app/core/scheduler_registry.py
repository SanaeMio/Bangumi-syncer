"""调度器注册表（SchedulerRegistry）

集中管理所有调度器实例，作为 lifespan 启停、配置保存联动、状态查询的单一入口。
替代原先散落在 main.py（6 行 start/stop 列表）和 config.py（4 个 try/except 联动）的硬编码。

两种注册模式：
- ``register_spec(JobSpec)``：INI 驱动的单 job 调度器（feiniu/fongmi/archive/replay）
  JobSpec 声明 scheduler_id / 关联配置段 / 预 reload 钩子，runner 持有 BaseScheduler 实例。
- ``register_instance(scheduler_id, scheduler)``：命令式调度器（trakt）
  scheduler 需实现 ``async start()`` / ``async stop()`` / ``get_all_jobs_status()`` 鸭子接口。

与 SectionMeta 的联动：
- ``apply_config_by_section(section)`` 通过 ``scheduler_id_for_section(section)``
  反查 scheduler_id，找到对应 spec/instance 并触发 ``apply_config_after_save``。
  若该 section 无 scheduler_id 关联或调度器未注册，静默跳过。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .config_schema import scheduler_id_for_section
from .logging import logger


@runtime_checkable
class SchedulerInstance(Protocol):
    """命令式调度器鸭子接口（trakt 等非 INI 驱动调度器）"""

    async def start(self) -> bool: ...

    async def stop(self) -> bool: ...

    def get_all_jobs_status(self) -> dict[str, dict]: ...


@dataclass(frozen=True)
class JobSpec:
    """INI 驱动单 job 调度器的声明式描述

    runner 持有 BaseScheduler 实例，Registry 仅做编排，不替代 BaseScheduler 的内部逻辑。
    pre_reload_hook 用于 archive 等需要在 apply_config_after_save 前重载单例配置的场景。
    """

    scheduler_id: str  # 与 SectionMeta.scheduler_id 对应，如 "feiniu"
    runner: Any  # BaseScheduler 实例，需实现 start/stop/apply_config_after_save
    pre_reload_hook: Any = field(default=None)  # Optional[() -> None]


class SchedulerRegistry:
    """调度器注册表单例"""

    def __init__(self) -> None:
        self._specs: dict[str, JobSpec] = {}
        self._instances: dict[str, SchedulerInstance] = {}

    # ===== 注册 =====

    def register_spec(self, spec: JobSpec) -> None:
        """注册 INI 驱动的单 job 调度器"""
        if spec.scheduler_id in self._specs:
            logger.warning("JobSpec 重复注册，覆盖: %s", spec.scheduler_id)
        if spec.scheduler_id in self._instances:
            logger.warning(
                "scheduler_id %s 已作为 instance 注册，将被 spec 覆盖",
                spec.scheduler_id,
            )
            self._instances.pop(spec.scheduler_id, None)
        self._specs[spec.scheduler_id] = spec

    def register_instance(
        self, scheduler_id: str, scheduler: SchedulerInstance
    ) -> None:
        """注册命令式调度器（trakt 等 DB 驱动多用户调度器）"""
        if scheduler_id in self._instances:
            logger.warning("scheduler instance 重复注册，覆盖: %s", scheduler_id)
        if scheduler_id in self._specs:
            logger.warning(
                "scheduler_id %s 已作为 spec 注册，将被 instance 覆盖",
                scheduler_id,
            )
            self._specs.pop(scheduler_id, None)
        self._instances[scheduler_id] = scheduler

    # ===== 查询 =====

    def get(self, scheduler_id: str) -> Any | None:
        """按 id 查调度器（spec.runner 或 instance）"""
        spec = self._specs.get(scheduler_id)
        if spec:
            return spec.runner
        return self._instances.get(scheduler_id)

    def all_scheduler_ids(self) -> list[str]:
        """所有已注册调度器 id"""
        return list(self._specs.keys()) + list(self._instances.keys())

    def get_all_jobs_status(self) -> dict[str, dict]:
        """汇总所有调度器的 job 状态

        spec 调度器通过 APScheduler get_job 获取单 job 状态；
        instance 调度器通过 get_all_jobs_status() 获取多 job 状态。
        """
        status: dict[str, dict] = {}
        for _sid, spec in self._specs.items():
            runner = spec.runner
            sched = getattr(runner, "scheduler", None)
            job_id = getattr(runner, "JOB_ID", "")
            if sched and job_id:
                job = sched.get_job(job_id)
                if job:
                    status[job_id] = {
                        "name": job.name,
                        "next_run_time": (
                            job.next_run_time.timestamp() if job.next_run_time else None
                        ),
                        "trigger": str(job.trigger),
                    }
        for sid, inst in self._instances.items():
            try:
                inst_status = inst.get_all_jobs_status()
                if isinstance(inst_status, dict):
                    status.update(inst_status)
            except Exception as e:
                logger.debug("获取 %s 调度器状态失败: %s", sid, e)
        return status

    # ===== 生命周期 =====

    async def start_all(self) -> None:
        """启动所有已注册调度器（spec 优先，instance 随后）"""
        for sid, spec in self._specs.items():
            try:
                ok = await spec.runner.start()
                logger.info("%s 调度器启动%s", sid, "成功" if ok else "失败")
            except Exception as e:
                logger.error("%s 调度器启动异常: %s", sid, e)
        for sid, inst in self._instances.items():
            try:
                ok = await inst.start()
                logger.info("%s 调度器启动%s", sid, "成功" if ok else "失败")
            except Exception as e:
                logger.error("%s 调度器启动异常: %s", sid, e)

    async def stop_all(self) -> None:
        """停止所有已注册调度器（instance 优先，spec 随后）"""
        for sid, inst in self._instances.items():
            try:
                await inst.stop()
                logger.info("%s 调度器已停止", sid)
            except Exception as e:
                logger.error("停止%s调度器失败: %s", sid, e)
        for sid, spec in self._specs.items():
            try:
                await spec.runner.stop()
                logger.info("%s 调度器已停止", sid)
            except Exception as e:
                logger.error("停止%s调度器失败: %s", sid, e)

    # ===== 配置联动 =====

    async def apply_config_by_section(self, section: str) -> None:
        """配置保存后按 section 反查并联动对应调度器

        通过 SectionMeta.scheduler_id 反查；无关联或未注册则静默跳过。
        spec 调度器：先执行 pre_reload_hook（如有），再调 apply_config_after_save。
        instance 调度器：不参与 INI 联动（trakt 配置在 DB，由 API 直调）。
        """
        scheduler_id = scheduler_id_for_section(section)
        if not scheduler_id:
            return
        spec = self._specs.get(scheduler_id)
        if not spec:
            # instance 调度器不参与 INI 联动，静默跳过
            return
        try:
            if spec.pre_reload_hook is not None:
                spec.pre_reload_hook()
            await spec.runner.apply_config_after_save()
        except Exception as e:
            logger.debug("%s 调度器随配置更新: %s", scheduler_id, e)


# 模块级单例
scheduler_registry = SchedulerRegistry()
