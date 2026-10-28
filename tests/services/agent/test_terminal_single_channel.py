"""终端单通道契约守卫：终止工具由注册表 ``access="terminal"`` 派生。

BDD：

- **派生**：loop 从 ``tools_schemas`` 派生终止工具——末轮 ``tool_choice`` 指向它、
  终止判定按它、收尾轮仅传它的 wire schema；``RunResult.stop_reason`` 为**实际工具名**
  （骨架不再硬编码场景名）。
- **零终止工具**：末轮不强制定向、收尾直接 exhausted（不发起收尾 LLM 调用）。
- **多终止工具**：runtime 入口 fail-fast（``ValueError``）——provider 无法表达
  「强制其中之一」，属场景配置错误。
- **契约迁出**：``ScenarioHooks`` 不再承载 ``terminal_tool`` 字段（单一来源=注册表）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.database import DatabaseManager, set_database_manager
from app.services.agent import loop
from app.services.agent.execution.tools import ToolDefinition, ToolRegistry
from app.services.agent.protocols import ToolSchema
from app.services.agent.run import runtime
from app.services.agent.run.scenario import ScenarioHooks
from app.services.llm.models import Message, StreamChunk
from tests.services.agent.stream_script import (
    scripted_executor,
    terminal_schema,
)

_SEED = [Message(role="user", content="hello")]


@pytest.fixture
def dbm(tmp_path: Path):
    instance = DatabaseManager(str(tmp_path / "terminal_single_channel.db"))
    set_database_manager(instance)
    yield instance
    if instance._connection._conn is not None:
        instance._connection._conn.close()
    set_database_manager(None)


def _terminal_tool_call(name: str):
    async def _stream(messages, *, tools=None, tool_choice=None):
        yield StreamChunk(type="tool_use_start", tool_use_id="s1", tool_name=name)
        yield StreamChunk(
            type="tool_use_delta", tool_use_id="s1", partial_json='{"ok": true}'
        )
        yield StreamChunk(type="tool_use_stop", tool_use_id="s1")
        yield StreamChunk(type="stop", stop_reason="tool_use", model="m")

    return _stream


def test_stop_reason_is_actual_terminal_tool_name():
    """骨架不得硬编码场景名：自定义终止工具名应原样成为 stop_reason。"""
    terminal = terminal_schema("finish_x")

    async def run():
        return await loop.run_loop(
            stream_fn=_terminal_tool_call("finish_x"),
            tools_schemas=[terminal],
            max_iterations=2,
            seed_messages=_SEED,
        )

    result = asyncio.run(run())

    assert result.stop_reason == "finish_x", (
        f"stop_reason 应为实际终止工具名，实际 {result.stop_reason!r}"
    )
    assert result.terminal_args == {"ok": True}


def test_zero_terminal_tools_natural_exhaustion_without_recovery_call():
    """零终止工具：末轮不强制定向，且收尾不发起 LLM 调用（自然 exhausted）。"""
    calls: list[dict] = []

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        calls.append({"tools": tools, "tool_choice": tool_choice})
        yield StreamChunk(type="tool_use_start", tool_use_id="t1", tool_name="read_x")
        yield StreamChunk(type="tool_use_delta", tool_use_id="t1", partial_json="{}")
        yield StreamChunk(type="tool_use_stop", tool_use_id="t1")
        yield StreamChunk(type="stop", stop_reason="tool_use", model="m")

    async def run():
        return await loop.run_loop(
            stream_fn=stream_fn,
            tools_schemas=[
                ToolSchema(name="read_x", description="", parameters={}, access="read")
            ],
            max_iterations=1,
            seed_messages=_SEED,
            executor_factory=scripted_executor(lambda tc: {"ok": tc.id}),
        )

    result = asyncio.run(run())

    assert result.stop_reason == "exhausted"
    assert result.last_response is not None
    assert len(calls) == 1, f"零终止工具不应发起收尾调用，实际流调用 {len(calls)} 次"
    # 末轮（唯一一轮）不得强制定向到任何工具
    assert calls[0]["tool_choice"] is None, (
        f"零终止工具时不应强制 tool_choice，实际 {calls[0]['tool_choice']!r}"
    )


def _two_terminal_defns(registry: ToolRegistry):
    defns = []
    for name in ("finish_a", "finish_b"):
        defn = ToolDefinition(
            name=name,
            description="",
            parameters={},
            handler=lambda args: {},
            access="terminal",
        )
        registry.register(defn, quiet=True)
        defns.append(defn)
    return defns


def test_multi_terminal_tools_fail_fast_at_runtime_entry(dbm):
    """多终止工具：runtime.run 入口 fail-fast（provider 无法表达「强制其一」）。"""
    dbm.agent_runs.create_pending("run-two-terminals", "match")

    class _Hooks:
        task_type = "match"

        def resolve_thinking_level(self) -> str:
            return "medium"

        def resolve_max_iterations(self, level: str) -> int:
            return 2

        def register_tools(self, registry, ctx):
            return _two_terminal_defns(registry)

        def build_stream_fn(self, level: str):
            raise AssertionError("fail-fast 应在构造 stream_fn 之前发生")

        async def handle_terminal(self, run_id, result, ctx, **kwargs):
            raise AssertionError("fail-fast 不应进入终局处理")

    with pytest.raises(ValueError, match="终止工具"):
        asyncio.run(
            runtime.run(
                "run-two-terminals",
                hooks=_Hooks(),  # type: ignore[arg-type]
                ctx=type("_Ctx", (), {"seed": _SEED})(),
                thinking_level="medium",
            )
        )


def test_scenario_hooks_has_no_terminal_tool_field():
    """ScenarioHooks 不再承载 terminal_tool（单一来源=注册表 access="terminal"）。"""
    assert "terminal_tool" not in ScenarioHooks.__dataclass_fields__, (
        "terminal_tool 字段应已删除（终止集合由注册表派生）"
    )


def test_run_result_fields_are_skeleton_only():
    """RunResult 字段清单：终止载荷名为 terminal_args（骨架不含场景词 suggestion）。"""
    from dataclasses import fields

    from app.services.agent.loop import RunResult

    assert {f.name for f in fields(RunResult)} == {
        "stop_reason",
        "text",
        "terminal_args",
        "last_response",
    }


def test_end_turn_run_result_keeps_terminal_args_none():
    """非终止终态（end_turn）：terminal_args 为 None（场景层 or {} 兼容）。"""

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        yield StreamChunk(type="text_delta", text="done")
        yield StreamChunk(type="stop", stop_reason="end_turn", model="m")

    async def run():
        return await loop.run_loop(
            stream_fn=stream_fn,
            tools_schemas=[terminal_schema()],
            max_iterations=2,
            seed_messages=_SEED,
        )

    result = asyncio.run(run())

    assert result.stop_reason == "end_turn"
    assert result.terminal_args is None
