"""chat span 概念模块：契约 + 两种实现（纯聚合 / 落库）。

一轮 LLM 调用对应一个 :class:`ChatSpan`（``async with`` 上下文）。三个构件同处本
模块，使「chat span」这一概念的契约与实现内聚：

- :class:`ChatSpan`：loop（消费方）与记录器（实现方）共用的四个契约点；
- :class:`PlainChatSpan`：纯聚合实现（不落库），``recorder=None`` 时 loop 的默认；
- :class:`RecorderChatSpan`：落库实现，由 ``TraceRecorder.chat_span()`` 产出，
  经 ``recorder.begin_chat_span()`` / ``finish_chat_span()`` 两个同伴 API 写 span。

单聚合契约（消除「同一轮流被两处聚合」）：

- ``feed(chunk)`` 是**唯一**聚合点（调用方不再另建 ``StreamAggregator``）；
- ``__aexit__`` 正常退出做**唯一**一次 ``StreamAggregator.finalize()``，调用方在
  ``async with`` 块外读 ``resp`` 供控制流——控制流输入与落库内容同源；
- 异常退出（流异常 / 取消）写 error span、**不** finalize、原异常照常透传。

落库时机是**轮粒度**（崩溃恢复依赖）：进入时 start_span、退出时 end_span；
轮次编号由记录器自持（见 ``TraceRecorder.chat_span``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

from app.core.logging import logger
from app.services.llm.models import (
    ChatResponse,
    StreamAggregator,
    StreamChunk,
)

if TYPE_CHECKING:  # 仅类型引用，避免 recorder ↔ chat_span 运行时环
    from app.services.agent.observe.recorder import TraceRecorder


class ChatSpan(Protocol):
    """一轮 chat span 上下文：既是聚合视图，也是 ``async with`` 上下文管理器。

    单聚合契约（消除「同一轮流被两处聚合」）：

    - ``__aenter__``：落本轮 span 起点（iteration / 父级上下文由实现自持）并返回 self；
    - ``feed(chunk)``：逐事件聚合（调用方**唯一**的馈入点，不再另建 aggregator）；
    - ``__aexit__``：正常退出做**唯一**一次 ``StreamAggregator.finalize()`` 并写 span
      终态；异常退出（流异常 / 取消）写 error span、不吞异常、不产出响应。

    ``resp`` 仅在 ``__aexit__`` 正常退出后有效（异常路径为 ``None``）。
    实现者（:class:`PlainChatSpan` / :class:`RecorderChatSpan`）需保证：
    正常退出必有响应，且每个实例的聚合恰好一次。
    """

    def feed(self, chunk: StreamChunk) -> None:
        """馈入一个流事件（唯一聚合点）。"""

    @property
    def resp(self) -> ChatResponse | None:
        """本轮聚合响应；仅 ``__aexit__`` 正常退出后有效。"""

    async def __aenter__(self) -> ChatSpan: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...


@dataclass(frozen=True)
class ChatSpanStart:
    """``TraceRecorder.begin_chat_span()`` 的返回值：本轮 span 的坐标与计时起点。

    以字段名承载三元组（而非裸 tuple），避免位置解包错序。
    """

    span_id: str
    iteration: int
    t0: float


class PlainChatSpan(ChatSpan):
    """纯聚合 chat span（不落库）：``recorder`` 缺省时的默认实现。

    loop 控制流需要本轮响应与是否落库无关，故无记录器时也必须聚合；语义与
    :class:`RecorderChatSpan` 一致（``feed`` 单点聚合、``__aexit__`` 唯一一次
    finalize），仅不做 span 写库。可被测试替身复用（``chat_span()`` 返回本类实例）。
    """

    def __init__(self) -> None:
        self._aggregator = StreamAggregator()
        self._resp: ChatResponse | None = None

    async def __aenter__(self) -> PlainChatSpan:
        return self

    def feed(self, chunk: StreamChunk) -> None:
        self._aggregator.feed(chunk)

    @property
    def resp(self) -> ChatResponse | None:
        return self._resp

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # 异常退出不 finalize（半截流不作为响应交给控制流）；原异常照常透传。
        if exc_type is None:
            self._resp = self._aggregator.finalize()
        return False


class RecorderChatSpan(ChatSpan):
    """落库实现（``TraceRecorder.chat_span()`` 产出）：聚合 + 唯一 finalize + 轮末入库。

    单聚合契约：``feed`` 是**唯一**聚合点，``__aexit__`` 做**唯一**一次
    ``StreamAggregator.finalize()``——调用方（loop）在块外读 ``resp`` 供控制流，
    与落库内容同源。

    落库时机（轮粒度，崩溃恢复依赖）：``__aenter__`` 立即 start_span（使流进行中
    提前执行的工具 span 与本轮 chat span 同 iteration）；``__aexit__`` 立即 end_span
    （成功写 response delta / model / tokens；异常写 error span，不吞异常）。
    """

    def __init__(self, recorder: TraceRecorder) -> None:
        self._recorder = recorder
        self._span_id = ""
        self._iteration = 0
        self._t0 = 0.0
        self._aggregator = StreamAggregator()
        self._resp: ChatResponse | None = None

    async def __aenter__(self) -> RecorderChatSpan:
        start = self._recorder.begin_chat_span()
        self._span_id = start.span_id
        self._iteration = start.iteration
        self._t0 = start.t0
        return self

    def feed(self, chunk: StreamChunk) -> None:
        self._aggregator.feed(chunk)

    @property
    def resp(self) -> ChatResponse | None:
        return self._resp

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # 异常退出（流异常 / 取消）：不 finalize——半截流不得记成成功响应；
        # finish_chat_span 走 error 分支落 span，原异常照常透传。
        resp = None if exc_type is not None else self._aggregator.finalize()
        self._resp = resp
        try:
            self._recorder.finish_chat_span(
                self._span_id, iteration=self._iteration, t0=self._t0, resp=resp
            )
        except Exception as e:
            # 观测层 best-effort：落库自身失败绝不遮蔽主流程（尤其原始流异常）
            logger.warning(f"[agent] chat span 落库失败（已忽略）: {e}")
        return False
