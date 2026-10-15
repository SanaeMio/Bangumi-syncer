"""``StreamingToolExecutor`` 测试：停点驱动提前执行 + 提交闸门 + 受控兜底。

覆盖 BDD 场景：

1. 停点前不执行；停点后**幂等**工具立即启动（与生成重叠）
2. **非幂等**工具留到 finalize 后按序执行（串行）
3. **畸形 JSON** → error result 且不执行（提交闸门）
4. 结果按 tool_calls 原始顺序对齐；重复 tool_use_id first-wins；未注册工具 error
5. **terminal 抑制**：terminal 出现后不再启动新任务；terminal 不执行 handler
6. **无停点**的宽容执行：finalize 兜底按 execute_batch 语义执行（行为等价改造前）
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.agent.execution.streaming import StreamingToolExecutor
from app.services.agent.execution.tools import ToolError
from app.services.llm.models import StreamChunk, ToolResultBlock
from tests.services.agent.stream_script import raw_handler_runner


@pytest.fixture
def log_records():
    """捕获项目 Logger 的 ``(level, line)``（监听器不受级别阈值限制）。"""
    from app.core.logging import logger as app_logger

    records: list[tuple[str, str]] = []

    def _listener(line: str, level: str) -> None:
        records.append((level, line))

    app_logger.add_listener(_listener)
    yield records
    app_logger.remove_listener(_listener)


def _start(tid: str, name: str) -> StreamChunk:
    return StreamChunk(type="tool_use_start", tool_use_id=tid, tool_name=name)


def _delta(tid: str, partial: str) -> StreamChunk:
    return StreamChunk(type="tool_use_delta", tool_use_id=tid, partial_json=partial)


def _stop(tid: str) -> StreamChunk:
    return StreamChunk(type="tool_use_stop", tool_use_id=tid)


def _full_args(tid: str, name: str, args_json: str):
    return [_start(tid, name), _delta(tid, args_json), _stop(tid)]


class _BatchResults:
    """伪 BatchResults：仅 ordered 单视图（对齐 execute_batch 契约）。"""

    def __init__(self, ordered):
        self.ordered = list(ordered)


def _make_executor(**overrides):
    """默认：read_* 幂等，write_* 非幂等，submit_suggestion terminal。"""

    async def default_execute(tool_use):
        return {"echo": tool_use.name, "input": tool_use.input}

    execute_fn = overrides.pop("execute_fn", default_execute)
    kwargs: dict[str, Any] = {
        "run_tool": raw_handler_runner(execute_fn),
        "is_idempotent": lambda name: name.startswith("read"),
        "is_terminal": lambda name: name == "submit_suggestion",
    }
    kwargs.update(overrides)
    return StreamingToolExecutor(**kwargs)


# ---------------------------------------------------------------------------
# 1. 停点前不执行；停点后幂等立即执行（并行重叠）
# ---------------------------------------------------------------------------


async def test_idempotent_tool_not_executed_before_stop_then_starts_at_stop():
    started: list[str] = []
    gate = asyncio.Event()

    async def execute_fn(tool_use):
        started.append(tool_use.id)
        await gate.wait()
        return {"ok": tool_use.id}

    ex = _make_executor(execute_fn=execute_fn)

    ex.feed(_start("a", "read_a"))
    ex.feed(_delta("a", '{"x": 1}'))
    await asyncio.sleep(0)
    assert started == [], "停点前不得执行"

    ex.feed(_stop("a"))
    await asyncio.sleep(0)
    assert started == ["a"], "停点后幂等工具应立即启动"

    gate.set()
    results = await ex.finalize()
    assert len(results) == 1
    assert results[0].tool_use_id == "a"
    assert results[0].is_error is False
    assert '"a"' in results[0].content


async def test_two_idempotent_tools_overlap_after_their_stops():
    """两个幂等工具各自停点到达后并发等待同一闸门 → 二者已同时启动（重叠）。"""
    started: list[str] = []
    gate = asyncio.Event()

    async def execute_fn(tool_use):
        started.append(tool_use.id)
        await gate.wait()
        return tool_use.id

    ex = _make_executor(execute_fn=execute_fn)
    for tid in ("a", "b"):
        ex.feed(_start(tid, f"read_{tid}"))
        ex.feed(_delta(tid, "{}"))
    ex.feed(_stop("a"))
    await asyncio.sleep(0)
    # b 尚未到停点：只有 a 启动
    assert started == ["a"]
    ex.feed(_stop("b"))
    await asyncio.sleep(0)
    # a 未完成（仍 await gate）时 b 已启动 → 证明重叠
    assert started == ["a", "b"]

    gate.set()
    results = await ex.finalize()
    assert [r.tool_use_id for r in results] == ["a", "b"]


# ---------------------------------------------------------------------------
# 2. 非幂等工具留到 finalize，按序串行执行
# ---------------------------------------------------------------------------


async def test_non_idempotent_tools_deferred_to_finalize_in_order():
    order: list[str] = []

    async def execute_fn(tool_use):
        order.append(tool_use.id)
        await asyncio.sleep(0)
        return tool_use.id

    ex = _make_executor(execute_fn=execute_fn)
    for tid in ("w1", "w2"):
        ex.feed(_start(tid, f"write_{tid}"))
        ex.feed(_delta(tid, "{}"))
        ex.feed(_stop(tid))
    await asyncio.sleep(0)

    assert order == [], "非幂等工具在 finalize 前不得执行"

    results = await ex.finalize()
    assert order == ["w1", "w2"], "非幂等工具应按原始顺序串行执行"
    assert [r.tool_use_id for r in results] == ["w1", "w2"]


# ---------------------------------------------------------------------------
# 3. 畸形 JSON → error result，不执行
# ---------------------------------------------------------------------------


async def test_malformed_json_is_gated_and_not_executed():
    called: list[str] = []

    async def execute_fn(tool_use):
        called.append(tool_use.id)
        return "should-not-run"

    ex = _make_executor(execute_fn=execute_fn)
    ex.feed(_start("a", "read_a"))
    ex.feed(_delta("a", '{"x": '))  # 截断的非法 JSON
    ex.feed(_stop("a"))

    results = await ex.finalize()

    assert called == [], "非法参数不得执行"
    assert results[0].is_error is True
    assert "JSON" in results[0].content


# ---------------------------------------------------------------------------
# 4. 保序 / 重复 id first-wins / 未注册工具 error
# ---------------------------------------------------------------------------


async def test_results_kept_in_original_order_across_early_and_deferred():
    ex = _make_executor()
    # a 幂等提前；b 非幂等延迟；c 幂等提前
    for tid, name in (("a", "read_a"), ("b", "write_b"), ("c", "read_c")):
        ex.feed(_start(tid, name))
        ex.feed(_delta(tid, '{"n": 1}'))
        ex.feed(_stop(tid))

    results = await ex.finalize()

    assert [r.tool_use_id for r in results] == ["a", "b", "c"]
    assert all(not r.is_error for r in results)


async def test_duplicate_tool_use_id_first_wins():
    async def execute_fn(tool_use):
        return tool_use.id

    ex = _make_executor(execute_fn=execute_fn)
    ex.feed(_start("dup", "read_dup"))
    ex.feed(_start("dup", "read_dup"))  # 重复 start：first-wins
    ex.feed(_delta("dup", "{}"))
    ex.feed(_stop("dup"))

    results = await ex.finalize()

    assert len(results) == 1, "重复 tool_use_id 只保留首个槽位"


async def test_unregistered_tool_returns_error_result():
    async def execute_fn(tool_use):
        raise ToolError(f"未注册的工具: {tool_use.name}")

    ex = _make_executor(
        execute_fn=execute_fn,
        is_idempotent=lambda name: False,  # 未注册 → 非幂等 → 延迟执行
    )
    ex.feed(_start("u", "unknown_tool"))
    ex.feed(_delta("u", "{}"))
    ex.feed(_stop("u"))

    results = await ex.finalize()

    assert results[0].is_error is True
    assert "ToolError" in results[0].content


# ---------------------------------------------------------------------------
# 5. terminal 抑制
# ---------------------------------------------------------------------------


async def test_terminal_not_executed_and_suppresses_subsequent_starts():
    called: list[str] = []

    async def execute_fn(tool_use):
        called.append(tool_use.id)
        return tool_use.id

    ex = _make_executor(execute_fn=execute_fn)
    # terminal 先到；随后幂等工具停点不得启动
    ex.feed(_start("s", "submit_suggestion"))
    ex.feed(_delta("s", '{"subject_id": "1"}'))
    ex.feed(_stop("s"))
    ex.feed(_start("a", "read_a"))
    ex.feed(_delta("a", "{}"))
    ex.feed(_stop("a"))

    results = await ex.finalize()

    assert called == [], "terminal 不执行 handler，且抑制后续工具启动"
    assert [r.tool_use_id for r in results] == ["s", "a"]


async def test_early_started_tool_before_terminal_completes_and_is_kept():
    """terminal 前已启动的幂等任务 await 完成，结果保留（loop 走 terminal 时忽略）。"""
    called: list[str] = []

    async def execute_fn(tool_use):
        called.append(tool_use.id)
        return tool_use.id

    ex = _make_executor(execute_fn=execute_fn)
    ex.feed(_start("a", "read_a"))
    ex.feed(_delta("a", "{}"))
    ex.feed(_stop("a"))  # 提前启动
    ex.feed(_start("s", "submit_suggestion"))
    ex.feed(_delta("s", "{}"))
    ex.feed(_stop("s"))  # terminal 出现
    ex.feed(_start("b", "read_b"))
    ex.feed(_delta("b", "{}"))
    ex.feed(_stop("b"))  # 被抑制

    results = await ex.finalize()

    assert called == ["a"], "仅 terminal 前已启动的 a 执行；b 被抑制"
    assert [r.tool_use_id for r in results] == ["a", "s", "b"]


# ---------------------------------------------------------------------------
# 6. 无停点协议 → 轮级降级（finalize 兜底，行为等价改造前）
# ---------------------------------------------------------------------------


async def test_no_stop_signal_degrades_to_round_level_batch_execution(log_records):
    """无 tool_use_stop：幂等工具也不提前执行，finalize 经 batch 路径执行；记 info。"""
    batch_calls: list[list[str]] = []

    async def batch_execute_fn(tool_calls):
        batch_calls.append([tc.id for tc in tool_calls])
        return _BatchResults(
            [
                (
                    tc.id,
                    ToolResultBlock(
                        tool_use_id=tc.id, content=f"batch:{tc.id}", is_error=False
                    ),
                )
                for tc in tool_calls
            ]
        )

    ex = _make_executor(batch_execute_fn=batch_execute_fn)
    for tid in ("a", "b"):
        ex.feed(_start(tid, f"read_{tid}"))
        ex.feed(_delta(tid, "{}"))
        # 不喂 stop（模拟无停点信号协议）

    results = await ex.finalize()

    assert batch_calls == [["a", "b"]]
    assert [r.content for r in results] == ["batch:a", "batch:b"]
    # 协议降级（停点机制不可用）是关键分支：聚合为本轮一条 info，默认级别可见
    infos = [line for level, line in log_records if level == "INFO"]
    assert any("本轮无停点" in line and "read_a" in line for line in infos), (
        f"轮级降级应记 info，实际 {infos}"
    )


async def test_result_for_placeholder_warns_only_on_exception_path(log_records):
    """无结果回填占位块：异常路径 → warning；terminal 抑制（按设计）→ debug。"""
    # ① 异常路径：非 terminal 轮且无结果 → 兜底回填，必须默认可见
    ex = _make_executor()
    ex.feed(_start("a", "read_a"))
    ex.feed(_delta("a", "{}"))
    ex._result_for("a")
    warns = [line for level, line in log_records if level == "WARNING"]
    assert any("无执行结果" in line and "read_a" in line for line in warns), warns

    # ② terminal 抑制：同轮其他工具按设计不执行 → 占位回填走 debug，不新增 warning
    before = len(warns)
    ex2 = _make_executor()
    ex2.feed(_start("s", "submit_suggestion"))
    ex2.feed(_delta("s", "{}"))
    ex2.feed(_stop("s"))
    ex2.feed(_start("b", "read_b"))
    ex2.feed(_delta("b", "{}"))
    await ex2.finalize()

    after = [line for level, line in log_records if level == "WARNING"]
    assert len(after) == before, (
        f"terminal 抑制属正常路径，不应新增 warning：{after[before:]}"
    )
    debugs = [line for level, line in log_records if level == "DEBUG"]
    assert any("terminal 轮抑制未执行工具 read_b" in line for line in debugs), debugs


async def test_batch_path_used_for_deferred_non_idempotent_only():
    """混合：幂等提前执行，非幂等经 batch 路径（仅延迟子集）。"""
    batch_ids: list[str] = []

    async def batch_execute_fn(tool_calls):
        batch_ids.extend(tc.id for tc in tool_calls)
        return _BatchResults(
            [
                (
                    tc.id,
                    ToolResultBlock(
                        tool_use_id=tc.id, content="batched", is_error=False
                    ),
                )
                for tc in tool_calls
            ]
        )

    ex = _make_executor(batch_execute_fn=batch_execute_fn)
    for tid, name in (("a", "read_a"), ("b", "write_b")):
        ex.feed(_start(tid, name))
        ex.feed(_delta(tid, "{}"))
        ex.feed(_stop(tid))

    results = await ex.finalize()

    assert batch_ids == ["b"], "仅非幂等工具走 batch 路径"
    assert [r.tool_use_id for r in results] == ["a", "b"]


async def test_no_stop_malformed_json_is_gated_at_finalize():
    """无停点 + 非法参数：finalize 兜底解析仍拦截，不执行。"""
    called: list[str] = []

    async def execute_fn(tool_use):
        called.append(tool_use.id)
        return "nope"

    ex = _make_executor(execute_fn=execute_fn)
    ex.feed(_start("a", "read_a"))
    ex.feed(_delta("a", "{bad"))  # 不喂 stop

    results = await ex.finalize()

    assert called == []
    assert results[0].is_error is True


# ---------------------------------------------------------------------------
# 提前执行路径受并发上限约束（对齐 _MAX_PARALLEL_TOOLS）
# ---------------------------------------------------------------------------


async def test_early_execution_caps_parallelism_at_max_parallel():
    """注入超过上限的幂等停点 → 提前执行峰值并发不超过 max_parallel。"""
    active = 0
    max_active = 0
    gate = asyncio.Event()

    async def execute_fn(tool_use):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await gate.wait()
        active -= 1
        return tool_use.id

    ex = _make_executor(execute_fn=execute_fn, max_parallel=10)
    for i in range(15):
        tid = f"t{i}"
        ex.feed(_start(tid, "read_x"))
        ex.feed(_delta(tid, "{}"))
        ex.feed(_stop(tid))
    # 让全部已 create_task 的协程都有机会被调度
    for _ in range(5):
        await asyncio.sleep(0)

    assert max_active <= 10, f"提前执行并发应受上限约束，峰值 {max_active}"

    gate.set()
    results = await ex.finalize()
    assert len(results) == 15
    assert all(not r.is_error for r in results)


async def test_early_execution_default_cap_limits_peak_concurrency():
    """默认构造（不注入 max_parallel）下峰值并发**恰好**为 tools._MAX_PARALLEL_TOOLS。

    行为断言：喂入上限 +3 个幂等停点，观测到的峰值并发必须等于默认上限（不是 ≤，
    否则默认值被改小/改大都会漏检）。
    """
    from app.services.agent.execution.tools import _MAX_PARALLEL_TOOLS

    limit = _MAX_PARALLEL_TOOLS
    active = 0
    max_active = 0
    gate = asyncio.Event()

    async def execute_fn(tool_use):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await gate.wait()
        active -= 1
        return tool_use.id

    ex = _make_executor(execute_fn=execute_fn)  # 不注入 max_parallel，走默认上限
    for i in range(limit + 3):
        tid = f"t{i}"
        ex.feed(_start(tid, "read_x"))
        ex.feed(_delta(tid, "{}"))
        ex.feed(_stop(tid))
    # 让全部已 create_task 的协程都有机会被调度
    for _ in range(10):
        await asyncio.sleep(0)

    assert max_active == limit, f"默认上限应恰好压到 {limit}，实际峰值 {max_active}"

    gate.set()
    results = await ex.finalize()
    assert len(results) == limit + 3
    assert all(not r.is_error for r in results)


# ---------------------------------------------------------------------------
# BatchResults.ordered 缺失时的回退取值（非契约批量执行器）
# ---------------------------------------------------------------------------


async def test_batch_results_without_ordered_fills_placeholders_with_warning(
    log_records,
):
    """非契约批量结果（无 ordered）→ warning + 全批占位块（不再回退 get 取值）。"""

    async def batch_execute_fn(tool_calls):
        return {
            tc.id: ToolResultBlock(
                tool_use_id=tc.id, content=f"fallback:{tc.id}", is_error=False
            )
            for tc in tool_calls
        }

    ex = _make_executor(
        batch_execute_fn=batch_execute_fn,
        is_idempotent=lambda name: False,  # 全部延迟到 finalize
    )
    for tid in ("a", "b"):
        ex.feed(_start(tid, f"write_{tid}"))
        ex.feed(_delta(tid, "{}"))
        ex.feed(_stop(tid))

    results = await ex.finalize()

    assert len(results) == 2
    assert all(r.is_error for r in results)
    assert all("fallback" not in r.content for r in results)
    warns = [line for level, line in log_records if level == "WARNING"]
    assert any("无 ordered" in line for line in warns), warns


async def test_batch_results_ordered_only_never_consults_get():
    """ordered 是唯一契约：即便替身带 get，也绝不回退取值。"""

    class _Results:
        def __init__(self):
            self.ordered = [
                (
                    "a",
                    ToolResultBlock(tool_use_id="a", content="ordered", is_error=False),
                )
            ]

        def get(self, key, default=None):  # pragma: no cover - 不应被走到
            raise AssertionError("ordered 存在时不应回退 get 取值")

    async def batch_execute_fn(tool_calls):
        return _Results()

    ex = _make_executor(
        batch_execute_fn=batch_execute_fn,
        is_idempotent=lambda name: False,
    )
    ex.feed(_start("a", "write_a"))
    ex.feed(_delta("a", "{}"))
    ex.feed(_stop("a"))

    results = await ex.finalize()

    assert [r.content for r in results] == ["ordered"]


# ---------------------------------------------------------------------------
# 8. capture 的显式处理（Option ②：执行层正常返回 capture，绝不序列化成字符串）
# ---------------------------------------------------------------------------


async def test_capture_is_preserved_not_serialized():
    """提前执行返回 capture：原样保留（不得 str 化塞进 tool_result 内容）。"""
    from app.services.agent.protocols import TerminalCapture

    async def capture_fn(tool_use):
        return TerminalCapture(name=tool_use.name, args=tool_use.input)

    ex = _make_executor(execute_fn=capture_fn)
    ex.feed(_start("a", "read_a"))
    ex.feed(_delta("a", '{"x": 1}'))
    ex.feed(_stop("a"))

    results = await ex.finalize()

    assert len(results) == 1
    assert isinstance(results[0], TerminalCapture), (
        f"capture 应原样保留，实际 {type(results[0]).__name__}: {results[0]!r}"
    )
    assert results[0].args == {"x": 1}


async def test_batch_capture_not_dropped_to_placeholder():
    """batch 结果含 capture：不得被 isinstance(ToolResultBlock) 过滤成占位块。"""
    from app.services.agent.protocols import TerminalCapture

    async def batch_execute_fn(tool_calls):
        return _BatchResults(
            [(tc.id, TerminalCapture(name=tc.name, args=tc.input)) for tc in tool_calls]
        )

    ex = _make_executor(batch_execute_fn=batch_execute_fn)
    ex.feed(_start("b", "write_b"))  # 非幂等 → 延迟执行（batch 路径）
    ex.feed(_delta("b", "{}"))

    results = await ex.finalize()

    assert len(results) == 1
    assert isinstance(results[0], TerminalCapture), (
        f"batch 的 capture 不应被丢弃成占位块，实际 {results[0]!r}"
    )
