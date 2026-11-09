"""应用装配层（Composition Root）：把具体场景装配进通用注册表。

- 生产：由 ``scheduler_bootstrap.register_all()`` 调用 ``wire_scenarios()``
  一次，保证调度器取用 ``get_scenario(task_type)`` 前场景已登记（后续任务接入）。
- 测试：由 ``tests/conftest.py`` 调用一次，保证既有测试的
  ``get_scenario("match")`` 路径可用。

**必须静态 import 场景工厂**（禁止 ``importlib`` / 字符串路径）：这是本模块存在
的意义——让 ``get_scenario_runtime`` 的调用对静态分析 / LSP 可见；有 AST 守卫
测试防回归。装配层位于应用层而非 agent 通用层，从而保持「通用层不反向依赖
场景模块」的解耦。
"""

from __future__ import annotations

from app.services.agent.run.registry import register_scenario
from app.services.matching.llm_assist import get_scenario_runtime


def wire_scenarios() -> None:
    """注册全部 Agent 场景（幂等：重复调用覆盖注册）。"""
    register_scenario("match", get_scenario_runtime())
