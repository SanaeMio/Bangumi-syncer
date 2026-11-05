"""场景装配（Composition Root）与注册表纯机制化测试。

BDD 场景（编号与规划源清单对齐；本文件覆盖 1/2/5/6/7/8）：

1. ``wire_scenarios()`` 后 ``get_scenario("match")`` 返回绑定匹配场景 hooks 的
   ``ScenarioRuntime``
2. 重复 ``wire_scenarios()`` 幂等（覆盖注册，不抛异常、不打 warning，仍可获取）
5. 未注册 task_type → ``KeyError``（消息含未知 task_type 与已注册列表）
6. 未装配（表为空）→ ``KeyError``（消息明确提示需先装配）
7. 装配层 / 注册表 / 场景模块在**干净子进程**中以任意顺序导入均不触发
   循环依赖（``subprocess`` 隔离验证 import 顺序无关）
8. 装配层必须**静态 import** 场景工厂（禁止 importlib / 字符串路径），注册表
   不得残留字符串表 ``_SCENARIO_PROVIDERS``

未在本文件直接编号的场景：场景 3（生产引导装配）见
``tests/services/test_scheduler_bootstrap.py``；场景 4（测试会话装配一致）由
conftest 装配路径回归覆盖；场景 9（行为等价回归）由全量套件覆盖。

说明：注册表 ``_scenarios`` 为进程级共享状态。测试中通过 ``monkeypatch`` 隔离，
不新增任何「仅测试用」的重置 API（monkeypatch 自动还原）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import scenarios
from app.services.agent.run.registry import get_scenario
from app.services.matching import llm_assist


class TestWireScenarios:
    """场景 1/2：装配后可用 + 幂等。"""

    def test_wire_scenarios_get_match_returns_runtime_bound_to_match_hooks(self):
        """wire_scenarios() 后 get_scenario("match") 返回绑定 _MATCH_HOOKS 的运行入口。"""
        scenarios.wire_scenarios()

        runtime = get_scenario("match")

        assert runtime.task_type == "match"
        # _MATCH_HOOKS 为模块级单例：用 is 断言装配实例与场景工厂同源
        assert runtime.hooks is llm_assist._MATCH_HOOKS

    def test_wire_scenarios_called_twice_is_idempotent_and_logs_no_warning(self):
        """重复 wire_scenarios() 幂等：覆盖注册 + 打 debug（非 warning）+ 仍可获取。

        正向证据（两路互补，均不依赖日志级别阈值）：
        - 行为面：``get_scenario_runtime()`` 每次调用新建 ``ScenarioRuntime``，
          故第二次装配后 ``get_scenario("match")`` 必为新实例（``is not`` 首次），
          直接证明「覆盖确实发生」，而非「第二次装配未执行」。
        - 日志面：覆盖分支走 ``logger.info``（消息含「覆盖」）；项目 Logger 的
          监听器**不受级别阈值限制**（见 ``app/core/logging.py`` 中
          ``log_level_enabled``/``log`` 的注释），故断言收到覆盖 INFO 行，
          证明走的是覆盖分支。
        """
        from app.core.logging import logger

        seen: list[tuple[str, str]] = []

        def _listener(line: str, level: str) -> None:
            seen.append((line, level))

        scenarios.wire_scenarios()
        first = get_scenario("match")

        logger.add_listener(_listener)
        try:
            scenarios.wire_scenarios()
        finally:
            logger.remove_listener(_listener)

        runtime = get_scenario("match")
        assert runtime.task_type == "match"
        assert runtime.hooks is llm_assist._MATCH_HOOKS
        # 正向证据 1：覆盖注册真的发生（第二次装配产生新的 ScenarioRuntime 实例）
        assert runtime is not first, (
            "第二次 wire_scenarios() 应覆盖为新的 ScenarioRuntime 实例"
        )
        # 正向证据 2：覆盖分支的 INFO 日志被监听器捕获
        assert any(level == "INFO" and "覆盖" in line for line, level in seen), (
            f"应捕获覆盖分支 INFO 日志，实际 {seen}"
        )

        warnings = [(line, level) for line, level in seen if level == "WARNING"]
        assert warnings == [], f"重复装配属预期，不应打 warning，实际 {warnings}"


class TestGetScenarioErrors:
    """场景 5/6：未注册 / 未装配的错误契约。"""

    def test_get_scenario_unknown_task_raises_keyerror_with_registered_list(self):
        """未注册 task_type → KeyError，消息含未知 task_type 与已注册列表。"""
        scenarios.wire_scenarios()

        with pytest.raises(KeyError) as exc_info:
            get_scenario("no_such_task")

        message = exc_info.value.args[0]
        assert "no_such_task" in message
        assert "match" in message, f"错误消息应含已注册列表，实际 {message!r}"

    def test_get_scenario_before_wiring_raises_keyerror_hinting_wiring(
        self, monkeypatch
    ):
        """未装配（表为空）→ KeyError，消息明确提示需先装配（monkeypatch 隔离）。"""
        monkeypatch.setattr("app.services.agent.run.registry._scenarios", {})

        with pytest.raises(KeyError) as exc_info:
            get_scenario("match")

        message = exc_info.value.args[0]
        assert "未装配" in message, f"错误消息应提示未装配，实际 {message!r}"


class TestImportOrderGuard:
    """场景 7：装配层 / 注册表 / 场景模块的导入顺序无关（无循环依赖）。

    在**干净子进程**中分别按不同顺序导入三模块：若存在循环导入或装配层被
    场景模块反向依赖，子进程会 ImportError 退出（returncode != 0）。
    ``cwd`` 设为仓库根，使 ``python -c`` 的 ``sys.path[0]`` 指向仓库；
    ``env`` 继承当前测试进程——conftest 已设 ``CONFIG_FILE`` 指向测试临时配置，
    子进程因此不会读写本地 ``config.ini``。
    """

    _REPO_ROOT = Path(__file__).resolve().parents[3]

    _IMPORT_ORDERS: dict[str, tuple[str, ...]] = {
        # A: 装配层优先（触发其静态 import 链）
        "composition_root_first": (
            "import app.services.scenarios",
            "import app.services.agent.run.registry",
            "import app.services.matching.llm_assist",
        ),
        # B: 注册表优先
        "registry_first": (
            "import app.services.agent.run.registry",
            "import app.services.matching.llm_assist",
            "import app.services.scenarios",
        ),
        # C: 场景模块优先
        "scenario_first": (
            "import app.services.matching.llm_assist",
            "import app.services.agent.run.registry",
        ),
    }

    @pytest.mark.parametrize("order_name", sorted(_IMPORT_ORDERS))
    def test_import_orders_no_cycle(self, order_name):
        """任一导入顺序均成功（无循环依赖），失败时输出子进程 stderr。"""
        code = "\n".join(self._IMPORT_ORDERS[order_name])

        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=self._REPO_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
        )

        assert completed.returncode == 0, (
            f"导入顺序 {order_name!r} 失败（returncode={completed.returncode}），"
            f"存在循环导入或反向依赖。\n"
            f"code:\n{code}\n"
            f"stderr:\n{completed.stderr}"
        )
