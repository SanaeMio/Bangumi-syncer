"""Agent 场景注册表（纯机制）。

通用组件（如调度器）按 ``task_type`` 获取场景运行入口
(:class:`ScenarioRuntime`)。本模块只提供「登记 / 查询」机制，**不感知任何具体
场景**：场景运行入口由**应用装配层**（``app/services/scenarios.py``，
Composition Root）静态 import 场景工厂后调用 :func:`register_scenario` 注入。
如此既让场景工厂的静态分析可见（LSP 可解析调用），又保留「通用层不反向依赖
场景模块」的解耦（装配层位于应用层，不是 agent 通用层）。

新增场景：实现 ``ScenarioHooks`` 与「run 输入 → 场景 ctx」工厂（``new_ctx``），
提供 ``ScenarioRuntime`` 工厂，并在装配层 ``wire_scenarios`` 静态 import 该工厂
并登记 ``task_type``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic

from app.core.logging import logger
from app.services.agent.observe.recorder import TraceRecorder
from app.services.agent.protocols import StreamFn
from app.services.agent.run import runtime
from app.services.agent.run.scenario import CtxT, ScenarioHooks
from app.services.notification_service import NotificationService


@dataclass(frozen=True)
class ScenarioRuntime(Generic[CtxT]):
    """场景运行入口：调度器等通用组件通过它执行 / 恢复场景 run。

    ``CtxT`` 为场景上下文类型（场景私有）：本类只以类型变量出现，实现类
    （如 ``llm_assist.get_scenario_runtime``）把具体类型绑定到入口，
    通用层因此既能传递该ctx，又不引用其具体类型。
    """

    task_type: str
    hooks: ScenarioHooks[CtxT]
    #: run 输入数据 → 场景上下文工厂（**由场景提供**）：通用层只交付自己拥有的
    #: 输入（如 ``sync_record``），ctx 的构造（含凭据等场景私有依赖）由场景自建，
    #: 通用层既不感知 ctx 结构，也不import 场景模块。
    new_ctx: Callable[[dict[str, Any]], CtxT]

    async def run(
        self,
        run_id: str,
        *,
        ctx: CtxT,
        thinking_level: str,
        stream_fn: StreamFn | None = None,
        notification_service: NotificationService | None = None,
        span_recorder: TraceRecorder | None = None,
    ) -> str:
        """执行一次场景任务（转发通用运行时）。

        ``ctx`` 为场景上下文，由 :meth:`new_ctx` 构造；通用层对其内部结构不可见
        （只经 :data:`~app.services.agent.run.scenario.CtxT` 绑定类型）。
        ``stream_fn`` 为流式 LLM 调用注入；为空时由场景默认 ``build_stream_fn`` 构造。
        """
        return await runtime.run(
            run_id,
            hooks=self.hooks,
            ctx=ctx,
            thinking_level=thinking_level,
            stream_fn=stream_fn,
            notification_service=notification_service,
            span_recorder=span_recorder,
        )

    async def continue_run(
        self,
        run_id: str,
        *,
        ctx: CtxT,
        notification_service: NotificationService | None = None,
    ) -> None:
        """恢复续跑（转发通用状态机）。

        ``ctx`` 为场景上下文，由调用方构造；通用层对其完全不可见（不透明边界）。
        """
        await runtime.continue_run(
            run_id,
            hooks=self.hooks,
            ctx=ctx,
            notification_service=notification_service,
        )


# task_type → 已装配的场景运行入口；由应用装配层 register_scenario 注入
_scenarios: dict[str, ScenarioRuntime] = {}


def register_scenario(task_type: str, scenario_runtime: ScenarioRuntime) -> None:
    """登记场景运行入口（覆盖语义，幂等）。

    重复登记属预期（应用启动与测试 conftest 可能各装配一次），只打
    debug/info，**不打 warning**。
    """
    if task_type in _scenarios:
        logger.info(f"[registry] 覆盖已登记的场景: {task_type!r}")
    else:
        logger.info(f"[registry] 登记 Agent 场景: {task_type!r}")
    _scenarios[task_type] = scenario_runtime


def get_scenario(task_type: str) -> ScenarioRuntime:
    """按 task_type 获取已装配的场景运行入口。

    - 表为空：抛 ``KeyError``，提示需先装配（应用启动由
      ``scheduler_bootstrap.register_all()`` 触发，测试由 conftest 触发）
    - 未注册该 task_type：抛 ``KeyError``，消息含已登记列表
    """
    if not _scenarios:
        raise KeyError(
            f"场景未装配: {task_type!r}（应用启动由 "
            f"scheduler_bootstrap.register_all() 触发，测试由 conftest 触发）"
        )
    try:
        return _scenarios[task_type]
    except KeyError as e:
        registered = sorted(_scenarios)
        raise KeyError(
            f"未注册的 Agent 场景: {task_type!r}（已注册: {registered}）"
        ) from e
