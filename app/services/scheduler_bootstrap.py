"""调度器注册引导

在应用启动前由 main.py 调用 ``register_all()`` 一次，将所有调度器注册到
``scheduler_registry``。集中替代原先散落在 main.py 的 6 行 import + start/stop 列表，
以及 config.py 的 4 个 try/except 联动调用。

注册后 main.py 只需：
    await scheduler_registry.start_all()   # 替代 6 行 start 列表
    await scheduler_registry.stop_all()    # 替代 6 行 stop 列表

config.py 保存配置后只需：
    await scheduler_registry.apply_config_by_section(section)  # 替代 4 个 try/except
"""

from __future__ import annotations

from ..core.logging import logger
from ..core.scheduler_registry import JobSpec, scheduler_registry


def register_all() -> None:
    """注册所有调度器到 registry（幂等，重复调用会覆盖并告警）"""

    # ── INI 驱动的单 job 调度器（spec 注册）──

    from .feiniu.scheduler import feiniu_scheduler

    scheduler_registry.register_spec(
        JobSpec(scheduler_id="feiniu", runner=feiniu_scheduler)
    )

    from .fongmi.scheduler import fongmi_scheduler

    scheduler_registry.register_spec(
        JobSpec(scheduler_id="fongmi", runner=fongmi_scheduler)
    )

    from ..utils.bangumi_archive import bangumi_archive
    from .bangumi_archive_scheduler import bangumi_archive_scheduler

    scheduler_registry.register_spec(
        JobSpec(
            scheduler_id="bangumi_archive",
            runner=bangumi_archive_scheduler,
            pre_reload_hook=bangumi_archive.reload_config,
        )
    )

    from .bangumi_replay_scheduler import bangumi_replay_scheduler

    scheduler_registry.register_spec(
        JobSpec(scheduler_id="bangumi_replay", runner=bangumi_replay_scheduler)
    )

    from .airing_today_scheduler import airing_today_scheduler

    scheduler_registry.register_spec(
        JobSpec(scheduler_id="airing_today", runner=airing_today_scheduler)
    )

    # ── 命令式调度器（instance 注册）──

    from .trakt.scheduler import trakt_scheduler

    scheduler_registry.register_instance("trakt", trakt_scheduler)

    # ── Summary：多实例 job，当前保留自管理 start/stop，注册为 instance ──
    # 注意：summary 继承体系独立（未继承 BaseScheduler），但参与 lifespan 统一启停。
    # 其配置联动仍由 summary_jobs API 直调 apply_config_after_save（多实例语义不适合 spec）。
    from .summary.scheduler import summary_scheduler

    scheduler_registry.register_instance("summary", summary_scheduler)

    logger.debug("调度器注册完成: %s", scheduler_registry.all_scheduler_ids())
