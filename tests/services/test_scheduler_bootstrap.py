"""调度器注册引导（scheduler_bootstrap.register_all）测试

验证 register_all() 将 llm_match 调度器注册进统一注册表，且重复调用幂等
（覆盖并告警）。测试直接作用于模块级单例 scheduler_registry。
"""

from __future__ import annotations

from app.core.scheduler_registry import scheduler_registry
from app.services.agent.run.registry import get_scenario
from app.services.llm_match_scheduler import llm_match_scheduler
from app.services.scheduler_bootstrap import register_all


class TestRegisterAllLlmMatch:
    """register_all() 注册 llm_match 调度器"""

    def test_register_all_registers_llm_match(self):
        """register_all() 后 registry 中存在 llm_match"""
        register_all()
        assert scheduler_registry.get("llm_match") is not None
        assert "llm_match" in scheduler_registry.all_scheduler_ids()

    def test_register_all_llm_match_runner_is_singleton(self):
        """注册的 runner 是模块级 llm_match_scheduler 单例"""
        register_all()
        assert scheduler_registry.get("llm_match") is llm_match_scheduler

    def test_register_all_idempotent_emits_overwrite_warning(self):
        """重复调用 register_all 幂等（llm_match 仍在），且触发覆盖告警

        应用使用自定义 Logger（非 stdlib logging），通过 add_listener 捕获告警行。
        """
        from app.core.logging import logger

        seen: list[tuple[str, str]] = []

        def _listener(line: str, level: str) -> None:
            seen.append((line, level))

        logger.add_listener(_listener)
        try:
            register_all()  # 首次/或会话内已注册
            assert scheduler_registry.get("llm_match") is llm_match_scheduler

            register_all()  # 重复注册，应覆盖并告警

            # 幂等：重复调用后 llm_match 仍存在且仍是同一单例
            assert scheduler_registry.get("llm_match") is llm_match_scheduler
        finally:
            logger.remove_listener(_listener)

        # 覆盖告警：针对 llm_match 的 JobSpec 重复注册告警被记录
        overwrite_logged = any(
            level == "WARNING" and "llm_match" in line and "覆盖" in line
            for line, level in seen
        )
        assert overwrite_logged


class TestRegisterAllWiresScenarios:
    """register_all() 装配 Agent 场景（Composition Root 生产接入点）"""

    def test_register_all_wires_scenarios(self, monkeypatch):
        """清空场景表后调用 register_all()，match 场景被重新装配可用

        生产启动时 register_all() 是唯一的装配触发点；conftest 已在测试进程
        全局装配，故必须先清空 ``_scenarios`` 使断言具备鉴别力（否则不调用
        wire_scenarios 也会通过）。
        """
        monkeypatch.setattr("app.services.agent.run.registry._scenarios", {})

        register_all()

        runtime = get_scenario("match")
        assert runtime.task_type == "match"
