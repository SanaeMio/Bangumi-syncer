"""流式工具执行器：停点驱动提前执行 + 提交闸门 + 受控兜底。

业界模式（流式解析 + 提交闸门 + 受控执行）在 Agent 循环中的落地：

- **提前执行**：幂等工具在其参数**停点**（``StreamChunk.type == "tool_use_stop"``）
  到达时立即启动 ``asyncio.create_task``，与后续 tool_call 的生成**重叠**，缩短端到端时延。
- **提交闸门**：停点到达时对累积的 ``partial_json`` 做 ``json.loads`` 校验——非法 JSON
  记 error result 且**绝不执行**（不把畸形参数喂给 handler）。
- **能力分级（按协议停点时序，三种）**：
  - **anthropic / responses**：**逐工具停点**（每个 tool_use 参数生成完即发
    ``tool_use_stop``）→ 真正提前执行，且与后续 tool_call 的生成**重叠**。
  - **openai_compat**：``finish_reason`` 时**流末集中补发**全部 ``tool_use_stop``
    → 仍会触发提前执行，但**无重叠收益**（停点已在流末）；其轮内并发模型为
    「幂等工具全部并行（受 ``max_parallel`` 上限）→ deferred 再执行」，与旧
    ``execute_batch`` 的分段交错（连续幂等段并行、非幂等串行）**不完全相同**。
  - **eval replay**：真正**无 stop** → 纯轮级降级，全部工具留到 :meth:`finalize`
    按 ``execute_batch`` 语义执行，行为与改造前**等价**。
- **terminal 抑制**：终止工具（``is_terminal`` 为真）不执行 handler，由 loop 在流结束后
  走既有捕获逻辑；且一旦出现 terminal，后续停点不再启动新任务（同轮其他工具不执行，
  与改造前「终止工具优先」语义一致）。已提前启动的幂等任务 await 完成，其结果无害但被忽略。

依赖注入（不耦合具体 ``ToolRegistry`` 内部）：

- ``run_tool``：唯一执行入口（``ToolRunner`` 协议），单工具执行返回
  ``ToolResultBlock | TerminalCapture``（包装/span/错误文本由入口收口），
  异常自行抛出，由本组件统一包装为 ``ToolResultBlock(is_error=True)``。
- ``is_idempotent`` / ``is_terminal``：``name -> bool``，用于调度决策。
- ``on_recorder``：可选 :class:`~app.services.agent.protocols.ToolSpanRecorder`，
  为**提前执行**的工具落 span（延迟执行走 ``batch_execute_fn`` 自带的 span 包裹）。
- ``batch_execute_fn``：``list[ToolUseBlock] -> Awaitable[BatchResults]``，延迟执行的批量
  路径（复用 ``ToolRegistry.execute_batch`` 的分段并行/保序语义）。为 ``None`` 时退化为
  经 ``execute_fn`` 串行执行。
- ``max_parallel``：提前执行路径的并发上限（默认 ``tools._MAX_PARALLEL_TOOLS``，与
  ``execute_batch`` 幂等段同源），防停点密集时无界并发打爆下游。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import logger
from app.services.agent.execution.tools import (
    _MAX_PARALLEL_TOOLS,
)
from app.services.agent.protocols import (
    StreamingExecutor,
    TerminalCapture,
    ToolRunner,
    ToolSpanRecorder,
)
from app.services.llm.models import StreamChunk, ToolResultBlock, ToolUseBlock

# 未执行工具的占位错误块（terminal 轮的非终止工具、或异常缺失结果）。
# 正常业务路径不会消费它（terminal 轮 loop 直接走终止分支），仅作协议闭合兜底。
_NOT_EXECUTED_CONTENT = "skipped: not executed in this round"


@dataclass
class _ToolState:
    """单个工具调用的流式累积状态。"""

    tool_use_id: str
    name: str
    seq: int
    json_parts: list[str] = field(default_factory=list)
    args: dict | None = None
    parse_ok: bool = False
    stop_seen: bool = False
    task: asyncio.Task | None = None
    result: ToolResultBlock | TerminalCapture | None = None


class StreamingToolExecutor(StreamingExecutor):
    """停点驱动的工具执行器（见模块 docstring 的调度决策树）。"""

    def __init__(
        self,
        *,
        run_tool: ToolRunner,
        is_idempotent: Callable[[str], bool],
        is_terminal: Callable[[str], bool],
        on_recorder: ToolSpanRecorder | None = None,
        batch_execute_fn: Callable[[list[ToolUseBlock]], Awaitable[Any]] | None = None,
        max_parallel: int = _MAX_PARALLEL_TOOLS,
    ) -> None:
        self._run_tool = run_tool
        self._is_idempotent = is_idempotent
        self._is_terminal = is_terminal
        self._on_recorder = on_recorder
        self._batch_execute_fn = batch_execute_fn
        # 提前执行路径的并发上限（与 execute_batch 的幂等段上限同源，防停点风暴打爆下游）
        self._max_parallel = max_parallel
        self._sem = asyncio.Semaphore(max_parallel)
        # tool_use_id 首次出现顺序（重复 id first-wins，与 StreamAggregator 对齐）
        self._order: list[str] = []
        self._states: dict[str, _ToolState] = {}
        self._tasks: list[asyncio.Task] = []
        self._terminal_seen = False

    # -- 流式喂入 -----------------------------------------------------------

    def feed(self, chunk: StreamChunk) -> None:
        """喂入一条流式事件（非工具事件忽略）。"""
        if chunk.type == "tool_use_start":
            self._on_start(chunk)
        elif chunk.type == "tool_use_delta":
            self._on_delta(chunk)
        elif chunk.type == "tool_use_stop":
            self._on_stop(chunk)

    def _on_start(self, chunk: StreamChunk) -> None:
        tid = chunk.tool_use_id
        if tid in self._states:
            logger.warning(
                "StreamingToolExecutor 收到重复 tool_use_start（first-wins）: %s", tid
            )
            return
        self._states[tid] = _ToolState(
            tool_use_id=tid, name=chunk.tool_name, seq=len(self._order)
        )
        self._order.append(tid)

    def _on_delta(self, chunk: StreamChunk) -> None:
        st = self._states.get(chunk.tool_use_id)
        if st is None:
            # 防御兜底：缺 start 的增量（与 StreamAggregator 的宽容行为对齐）
            logger.warning(
                "StreamingToolExecutor 收到无 start 的 tool_use_delta: %s",
                chunk.tool_use_id,
            )
            st = _ToolState(
                tool_use_id=chunk.tool_use_id, name="", seq=len(self._order)
            )
            self._states[chunk.tool_use_id] = st
            self._order.append(chunk.tool_use_id)
        st.json_parts.append(chunk.partial_json)

    def _on_stop(self, chunk: StreamChunk) -> None:
        """停点 = 提交闸门：校验参数 → 幂等提前启动 / 非幂等延迟 / terminal 抑制。"""
        st = self._states.get(chunk.tool_use_id)
        if st is None:
            logger.warning(
                "StreamingToolExecutor 收到无 start 的 tool_use_stop: %s",
                chunk.tool_use_id,
            )
            return
        st.stop_seen = True
        if self._terminal_seen:
            logger.debug(
                "StreamingToolExecutor terminal 已出现，抑制停点启动: %s", st.name
            )
            return
        if self._is_terminal(st.name):
            self._terminal_seen = True
            return
        if not self._parse_args(st):
            return
        if self._is_idempotent(st.name):
            st.task = asyncio.create_task(self._run_early(st))
            self._tasks.append(st.task)
            return
        logger.debug(
            "StreamingToolExecutor 非幂等工具 %s 延迟到 finalize 执行", st.name
        )

    def _parse_args(self, st: _ToolState) -> bool:
        """闸门：解析累积 JSON；失败记 error result 且不执行，返回 False。"""
        raw = "".join(st.json_parts)
        try:
            st.args = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError) as e:
            st.result = ToolResultBlock(
                tool_use_id=st.tool_use_id,
                content=f"工具参数不是合法 JSON: {type(e).__name__}",
                is_error=True,
            )
            logger.warning(
                "StreamingToolExecutor 工具 %s 参数非法 JSON，已拦截不执行: %s",
                st.name,
                e,
            )
            return False
        st.parse_ok = True
        return True

    # -- 提前执行 -----------------------------------------------------------

    async def _run_early(self, st: _ToolState) -> None:
        """提前执行单个幂等工具，结果/异常统一包装为 ToolResultBlock。

        提前执行路径受 ``max_parallel`` 信号量约束（与 ``execute_batch`` 幂等段
        同一上限），避免停点密集时无界并发打爆下游。
        """
        async with self._sem:
            st.result = await self._exec_single(st)

    async def _exec_single(self, st: _ToolState) -> ToolResultBlock | TerminalCapture:
        """单条执行（薄委托）：包装 / span / 错误文本统一由 ``run_tool`` 收口。

        显式处理 capture（Option ②）：终止工具被**正常捕获**时，置位终止语义并
        原样保留结果（绝不序列化成字符串塞进 tool_result）。
        """
        tool_use = ToolUseBlock(id=st.tool_use_id, name=st.name, input=st.args or {})
        outcome = await self._run_tool.run_tool(
            tool_use,
            recorder=self._on_recorder,
            sequence=st.seq,
        )
        if isinstance(outcome, TerminalCapture):
            self._terminal_seen = True
        return outcome

    # -- 收尾：await 提前任务 + 延迟执行 + 保序对齐 --------------------------

    async def finalize(self) -> list[ToolResultBlock | TerminalCapture]:
        """流结束后收尾，返回按 tool_use 首次出现顺序对齐的完整结果列表。"""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        deferred = self._collect_deferred()
        if deferred:
            await self._execute_deferred(deferred)

        return [self._result_for(tid) for tid in self._order]

    def _collect_deferred(self) -> list[_ToolState]:
        """收集留到 finalize 执行的工具（非幂等 / 无停点降级的轮级执行）。

        terminal 工具不执行（由 loop 处理）；terminal 轮的其他工具也不执行
        （保持「终止工具优先，同轮其他工具不执行」的既有语义）。
        """
        deferred: list[_ToolState] = []
        degraded: list[str] = []
        for tid in self._order:
            st = self._states[tid]
            if st.result is not None or st.task is not None:
                continue  # 已提前执行 / 已闸门拦截
            if self._is_terminal(st.name):
                continue
            if self._terminal_seen:
                logger.debug(
                    "StreamingToolExecutor terminal 轮跳过延迟执行: %s", st.name
                )
                continue
            if not st.stop_seen:
                # 无停点协议的轮级降级：仍按原 execute_batch 语义执行
                # （聚合到本轮一条 info，避免逐工具刷屏）
                degraded.append(st.name)
            if not st.parse_ok and not self._parse_args(st):
                # 未收到停点且累积 JSON 非法：error result 已就位，不执行
                logger.debug(
                    "StreamingToolExecutor 延迟工具 %s 参数非法，已拦截", st.name
                )
                continue
            deferred.append(st)
        if degraded:
            logger.info(
                "StreamingToolExecutor 本轮无停点（协议降级为轮级执行）: %s",
                ", ".join(degraded),
            )
        return deferred

    async def _execute_deferred(self, states: list[_ToolState]) -> None:
        if self._batch_execute_fn is not None:
            tool_calls = [
                ToolUseBlock(id=st.tool_use_id, name=st.name, input=st.args or {})
                for st in states
            ]
            results = await self._batch_execute_fn(tool_calls)
            by_id = self._index_batch_results(results, states)
            for st in states:
                st.result = by_id.get(st.tool_use_id) or self._not_executed(st)
            return
        # 无批量执行器注入：串行兜底（保序）
        for st in states:
            st.result = await self._exec_single(st)

    @staticmethod
    def _index_batch_results(
        results: Any, states: list[_ToolState]
    ) -> dict[str, ToolResultBlock | TerminalCapture]:
        """把 ``execute_batch`` 返回值按 tool_use_id 建索引（**只认 ordered**）。

        参数刻意用 ``Any``：这是**非契约注入的鸭子边界**（注入方可能传
        ``BatchResults`` 或测试替身），本函数只以 ``getattr`` 探测 ``ordered``，
        不做 isinstance 收窄。

        生产契约：``BatchResults.ordered``（``list[tuple[tool_use_id, result]]``）。
        ``ordered`` 缺失（非契约实现）→ 记 warning 并返回空索引（下游逐槽回填
        占位块）——不再提供 ``get`` 回退取值（视图收敛，避免双通道）。
        """
        out: dict[str, ToolResultBlock | TerminalCapture] = {}
        ordered = getattr(results, "ordered", None)
        if not isinstance(ordered, list):
            logger.warning(
                "StreamingToolExecutor 批量结果无 ordered 槽位（非契约实现），"
                "本批 %d 个 deferred 工具将回填占位块",
                len(states),
            )
            return out
        for oid, item in ordered:
            if isinstance(item, (ToolResultBlock, TerminalCapture)):
                out.setdefault(oid, item)
        return out

    def _result_for(self, tid: str) -> ToolResultBlock | TerminalCapture:
        st = self._states[tid]
        if st.result is not None:
            return st.result
        # 防御兜底：无结果（terminal 轮被抑制 / 异常路径）→ 协议闭合占位块
        if self._terminal_seen:
            # 终止工具优先：同轮其他工具**按设计**不执行 → 正常路径
            logger.debug(
                "StreamingToolExecutor terminal 轮抑制未执行工具 %s，回填占位块",
                st.name,
            )
        else:
            # 非 terminal 轮却无结果 = 异常路径兜底，必须默认可见
            logger.warning(
                "StreamingToolExecutor 工具 %s 无执行结果（异常路径），回填占位块",
                st.name,
            )
        return self._not_executed(st)

    @staticmethod
    def _not_executed(st: _ToolState) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id=st.tool_use_id,
            content=_NOT_EXECUTED_CONTENT,
            is_error=True,
        )
