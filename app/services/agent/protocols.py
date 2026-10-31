"""Agent 层**跨层能力契约**与类型别名。

**归位判据**（决定一个契约放本文件，还是随其概念模块走）：

- 若契约的「消费方」与「实现方」**双向都不应 import 对方模块**（依赖防火墙）→ 放本
  文件：中立契约层，任何一侧都可依赖，两侧互不成环；
- 若消费方 import 概念模块天经地义、且概念模块不反向依赖消费方 → 契约**随概念归位**。
  例：``chat_span.ChatSpan`` 与其两种实现同处 :mod:`app.services.agent.chat_span`。

本文件现有：:data:`StreamFn`（LLM 调用边界）、:class:`ToolSchema` / :data:`ToolAccess`
（工具 schema 内部形态 + wire 出口）、:class:`TerminalCapture`、
:class:`StreamingExecutor`、:class:`ToolSpanRecorder`、:class:`ToolRunner`、
:class:`BudgetRecorder`——被 ``loop`` / ``execution`` / ``streaming_tool_executor`` /
``runtime`` 多层消费，实现方分散在各自模块，故留中立层。

历史：本文件曾集中全部 agent 协议（含 ``ChatSpan``）；``ChatSpan`` 随实现归位后，
本文件收窄为「跨层能力契约」。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from app.services.agent.chat_span import ChatSpan
from app.services.llm.models import StreamChunk, ToolResultBlock, ToolUseBlock

# ---------------------------------------------------------------------------
# 流式 LLM 调用
# ---------------------------------------------------------------------------

#: 流式 LLM 调用：``(messages, tools=, tool_choice=) -> AsyncIterator[StreamChunk]``。
StreamFn = Callable[..., AsyncIterator[StreamChunk]]

# ---------------------------------------------------------------------------
# 工具 schema（agent 内部形态 → provider wire 形态）
# ---------------------------------------------------------------------------

#: 工具副作用语义：read（只读）/ write（写，执行前审计）/ terminal（终止性，仅捕获参数）。
ToolAccess = Literal["read", "write", "terminal"]


@dataclass(frozen=True)
class ToolSchema:
    """工具的内部 schema：wire 三字段 + 执行语义 ``access``。

    - ``name`` / ``description`` / ``parameters``：provider ``tools`` 参数所需字段；
    - ``access``：**执行语义**（终止工具派生等），仅供 agent 内部使用，
      ``to_wire()`` **绝不输出**（LLM 不可见，天然防诱导）。

    wire 形态是跨层契约（三家 provider 的 ``_normalize_*`` 与 eval cassette 指纹都
    依赖它）——调用 provider 前必须经 :meth:`to_wire` 转换，且输出键序与历史 flat
    形态逐字一致。
    """

    name: str
    description: str
    parameters: dict
    access: ToolAccess

    def to_wire(self) -> dict:
        """转换为 provider ``tools`` 参数条目（仅三字段，键序固定）。"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# ---------------------------------------------------------------------------
# 工具执行结果类型（agent 内部契约）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TerminalCapture:
    """终止性工具（access=terminal）的捕获结果：**正常结束**语义。

    仅记录参数、不执行 handler（无副作用）；由循环收到即终止（终止工具优先）。
    """

    name: str
    args: dict


# ---------------------------------------------------------------------------
# 流式工具执行器
# ---------------------------------------------------------------------------


class StreamingExecutor(Protocol):
    """流式工具执行器协议。

    本地声明（不 import 具体 ``StreamingToolExecutor``），保持通用骨架对执行器实现的
    运行时解耦；返回值需实现 ``feed`` / ``finalize``。

    ``finalize`` 的槽位元素为 ``ToolResultBlock | TerminalCapture``（后者为终止工具的
    **正常捕获**，由终止层消费）。
    """

    def feed(self, chunk: StreamChunk) -> None: ...

    async def finalize(self) -> list[ToolResultBlock | TerminalCapture]: ...


# ---------------------------------------------------------------------------
# 记录器协议（chat span / 工具 span / 预算）
# ---------------------------------------------------------------------------


class ToolSpanRecorder(Protocol):
    """工具执行 span 记录协议（``start_tool`` / ``end_tool`` 单一来源）。

    只需「为工具执行落 span」的组件（``tools.execute_batch`` /
    ``streaming_tool_executor``）依赖本协议即可，**不要求** budget 能力。
    实现者（如 ``recorder.TraceRecorder``）需持有可注入的时钟（构造参数注入 clock，
    默认真实时钟），在 ``start_tool`` 记 t0、``end_tool`` 记 t1；包裹层只负责在正确
    时机（await 前 start、完成后 end）调用，不持有时钟、不感知时间。
    """

    def start_tool(self, tool_use: ToolUseBlock, *, sequence: int) -> str | None:
        """记录工具执行开始；返回 span_id（或 None 表示无需记录）。"""

    def end_tool(
        self,
        span_id: str,
        *,
        result: ToolResultBlock | None = None,
        error: str = "",
    ) -> None:
        """记录工具执行结束。

        - ``result=None 且 error 非空``：执行异常（异常类型名）
        - ``result=ToolResultBlock``：正常结果（含 is_error 的占位块）
        - ``result=None 且 error=""``：terminal 捕获等非结果路径
        """


class ToolRunner(Protocol):
    """工具执行入口协议（**唯一**）：调用方不得绕过它直调 ``execute``。

    三分类结果（包装 / span / 错误文本单点收口）：

    - 成功 → ``ToolResultBlock``（内容为 JSON 序列化结果）
    - 异常（ToolError / handler 异常 / 超时）→ ``ToolResultBlock(is_error=True)``，**不上抛**
    - terminal → :class:`TerminalCapture`（**正常结束**：捕获参数、不执行 handler、无副作用）
    """

    async def run_tool(
        self,
        tool_use: ToolUseBlock,
        *,
        recorder: ToolSpanRecorder | None = None,
        sequence: int = 0,
    ) -> ToolResultBlock | TerminalCapture: ...


class BudgetRecorder(ToolSpanRecorder, Protocol):
    """通用循环要求的记录器协议：chat span + 预算消息 + 工具 span（继承 ``ToolSpanRecorder``）。

    仅 ``loop.run_loop`` 需要 ``record_budget``；``start_tool`` / ``end_tool`` 的签名
    **不在此重复声明**，统一由基类 :class:`ToolSpanRecorder` 提供（避免此前
    ``tools.ToolSpanRecorder`` 与本协议逐字重复的两份定义）。
    """

    def record_budget(self, budget_message: str) -> None: ...

    def chat_span(self) -> ChatSpan:
        """开一轮 chat span（``async with`` 上下文）。

        轮次编号由实现自持（构造时的起始轮次 / 恢复锚定），**不接收调用方下标**：
        loop 的本地轮次与持久化历史编号可能不同源（续跑从 ``executed_iterations``
        起算，loop 只看剩余预算）。
        """
