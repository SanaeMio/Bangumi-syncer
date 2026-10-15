"""流式注入测试辅助。

把 ``test_loop.py`` 中旧 ``chat_fn`` / ``tool_calls_fn`` 注入用例迁移到生产主路径
（``stream_fn`` + ``executor_factory``）：

- :func:`scripted_stream`：把 ``ChatResponse`` 序列逐轮转成 ``StreamChunk`` 序列，
  等价旧 ``chat_fn`` 的按轮返回；响应耗尽后再被调用会**显式报错**（不静默）。
- :func:`scripted_executor`：零参工厂，返回 ``StreamingToolExecutor``，等价旧
  ``tool_calls_fn``。``handler(tool_use)`` 返回**原始值**（由执行器按生产语义
  ``serialize_tool_result`` 包装为 ``ToolResultBlock``、异常包装为 ``is_error`` 结果）
  或 ``ToolResultBlock``（直传，精确控制内容/is_error）；terminal 工具不执行 handler
  （由 loop 捕获参数）。

与既有 ``_stream_*`` 内联辅助（``test_loop.py``）职责互补：本模块负责「按轮脚本化
响应/执行器」，``_stream_*`` 负责「手写单轮事件流」。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.agent.execution.streaming import StreamingToolExecutor
from app.services.agent.execution.tools import serialize_tool_result
from app.services.agent.protocols import TerminalCapture
from app.services.llm.models import (
    ChatResponse,
    Message,
    RedactedThinkingBlock,
    StreamChunk,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def response_to_chunks(resp: ChatResponse) -> list[StreamChunk]:
    """把一条 ``ChatResponse`` 转为等价的 ``StreamChunk`` 序列。

    - ``TextBlock`` → ``text_delta``；``ThinkingBlock`` → ``thinking_delta``（含 signature）
    - ``RedactedThinkingBlock`` → ``redacted_thinking_delta``（data 原样保留）
    - ``ToolUseBlock`` → ``tool_use_start`` + ``tool_use_delta``（``json.dumps(input)``）
      + ``tool_use_stop``
    - ``blocks`` 无 ``TextBlock`` 但 ``content`` 非空（旧 ``_resp`` 常只填 content）→
      补一条 ``text_delta``，保证聚合后 ``content`` 一致
    - ``usage`` → ``usage`` 事件；结尾 ``stop`` 事件（``stop_reason`` / ``model``）

    ``block_index`` 按块在 ``blocks`` 中的实际位置填充，聚合器据此分桶——省略会让
    ``text → tool_use → text`` 这类交错块塌陷成单块，回放结构与 live 不一致。

    不支持的块类型显式报错，避免静默丢内容。
    """
    chunks: list[StreamChunk] = []
    has_text_block = any(isinstance(b, TextBlock) for b in resp.blocks)
    if resp.content and not has_text_block:
        chunks.append(StreamChunk(type="text_delta", text=resp.content))
    for index, block in enumerate(resp.blocks):
        if isinstance(block, ThinkingBlock):
            chunks.append(
                StreamChunk(
                    type="thinking_delta",
                    thinking=block.thinking,
                    signature=block.signature or "",
                    block_index=index,
                )
            )
        elif isinstance(block, TextBlock):
            chunks.append(
                StreamChunk(type="text_delta", text=block.text, block_index=index)
            )
        elif isinstance(block, RedactedThinkingBlock):
            chunks.append(
                StreamChunk(
                    type="redacted_thinking_delta",
                    redacted_data=block.data,
                    block_index=index,
                )
            )
        elif isinstance(block, ToolUseBlock):
            chunks.append(
                StreamChunk(
                    type="tool_use_start",
                    tool_use_id=block.id,
                    tool_name=block.name,
                    block_index=index,
                )
            )
            chunks.append(
                StreamChunk(
                    type="tool_use_delta",
                    tool_use_id=block.id,
                    partial_json=json.dumps(block.input, ensure_ascii=False),
                    block_index=index,
                )
            )
            chunks.append(
                StreamChunk(
                    type="tool_use_stop",
                    tool_use_id=block.id,
                    block_index=index,
                )
            )
        else:
            raise AssertionError(
                f"response_to_chunks 不支持的块类型: {type(block).__name__}"
            )
    if resp.usage is not None:
        chunks.append(StreamChunk(type="usage", usage=resp.usage))
    chunks.append(
        StreamChunk(type="stop", stop_reason=resp.stop_reason, model=resp.model)
    )
    return chunks


@dataclass
class ScriptedStream:
    """按轮返回预置响应的 ``stream_fn``（等价旧 ``chat_fn``）。

    ``calls`` 记录每次被调用时的 messages 快照（``list()`` 浅拷贝）与
    ``tools`` / ``tool_choice``，供「LLM 收到的内容」类断言；``count`` 为被调用次数。
    """

    responses: list[ChatResponse]
    calls: list[tuple[list[Message], dict]] = field(default_factory=list)

    @property
    def count(self) -> int:
        """被调用（开始消费）次数。"""
        return len(self.calls)

    async def __call__(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(
            (list(messages), {"tools": tools, "tool_choice": tool_choice})
        )
        if not self.responses:
            raise AssertionError(
                "scripted_stream 预置响应已耗尽，但被再次调用（测试脚本轮次不足）"
            )
        for chunk in response_to_chunks(self.responses.pop(0)):
            yield chunk


def scripted_stream(responses: list[ChatResponse]) -> ScriptedStream:
    """构造按轮依次返回 ``responses`` 的 ``stream_fn``。"""
    return ScriptedStream(responses=list(responses))


def terminal_schema(name: str = "submit_suggestion"):
    """测试用终止工具 schema（``access="terminal"``，供 loop 派生终止集合）。"""
    from app.services.agent.protocols import ToolSchema

    return ToolSchema(name=name, description="", parameters={}, access="terminal")


def raw_handler_runner(handler: Callable[[ToolUseBlock], Any]):
    """把「原始值 handler」适配为 ``run_tool`` 契约（**仅测试**）。

    生产归一化在 ``ToolRegistry.run_tool``（唯一执行入口）；本适配器供测试脚本
    注入任意 handler（同步或 awaitable），语义与生产一致：

    - 原始值 → ``serialize_tool_result`` 包装为成功块
    - ``ToolResultBlock`` / ``TerminalCapture`` → 直传（精确控制内容/is_error/捕获）
    - 异常 → ``is_error`` 块（文案与 ``run_tool`` 相同）
    """

    class _RawHandlerRunner:
        async def run_tool(self, tool_use, *, recorder=None, sequence=0):
            try:
                result = handler(tool_use)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:  # 与 run_tool 同文案
                return ToolResultBlock(
                    tool_use_id=tool_use.id,
                    content=f"工具执行失败: {type(exc).__name__}: {exc}",
                    is_error=True,
                )
            if isinstance(result, (ToolResultBlock, TerminalCapture)):
                return result
            return ToolResultBlock(
                tool_use_id=tool_use.id,
                content=serialize_tool_result(result),
                is_error=False,
            )

    return _RawHandlerRunner()


def scripted_executor(
    handler: Callable[[ToolUseBlock], Any],
    *,
    is_idempotent: Callable[[str], bool] | None = None,
    is_terminal: Callable[[str], bool] | None = None,
) -> Callable[[], StreamingToolExecutor]:
    """构造按轮返回 ``StreamingToolExecutor`` 的 ``executor_factory``。

    ``handler(tool_use)`` 返回**原始值**（同步或 awaitable）或 ``ToolResultBlock``：
    原始值按生产语义 ``serialize_tool_result`` 包装，异常包装为 ``is_error`` 结果；
    ``ToolResultBlock`` 直传（精确控制结果内容/is_error）。
    默认 ``is_idempotent = name.startswith("read")``、
    ``is_terminal = name == "submit_suggestion"``（与既有 ``_streaming_executor`` 一致）。
    """
    idempotent = is_idempotent or (lambda name: name.startswith("read"))
    terminal = is_terminal or (lambda name: name == "submit_suggestion")

    def factory() -> StreamingToolExecutor:
        return StreamingToolExecutor(
            run_tool=raw_handler_runner(handler),
            is_idempotent=idempotent,
            is_terminal=terminal,
        )

    return factory
