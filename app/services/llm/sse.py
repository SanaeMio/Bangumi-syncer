"""独立 SSE（Server-Sent Events）解析器。

纯解析逻辑，无网络依赖，供各 LLM provider 的流式实现复用。
项目不使用官方 SDK（纯 httpx 手写），因此按 SSE 规范自行解析文本行。

语义要点：
- 空行（``""`` 或纯 ``\\r``）为事件边界；
- 一行内第一个冒号分隔字段名与值，值前可选一个空格；
- 多次 ``data:`` 用 ``\\n`` 连接；
- 支持 ``event`` / ``data`` / ``id`` / ``retry`` 字段，未知字段忽略；
- 以 ``:`` 开头的行是注释，忽略；
- 行尾 ``\\r`` / ``\\n`` 剥离以兼容 CRLF；
- 流结束时若无结尾空行，宽容 dispatch 缓冲区中的残留事件；
- ``[DONE]`` 不做特殊处理，作为普通 data 原样传递。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 单个事件内多行 data 的连接符（SSE 规范）。
_DATA_SEPARATOR = "\n"


@dataclass
class SSEEvent:
    """一个解析后的 SSE 事件。"""

    event: str | None = None  # event: 字段值（未提供则 None）
    data: str = ""  # data: 拼接结果（多行用 \n 连接）
    id: str | None = None
    retry: int | None = None


def _strip_line_ending(line: str) -> str:
    """剥离行尾的 ``\\n`` / ``\\r``（兼容 CRLF 与单独 CR）。"""
    return line.rstrip("\r\n")


def _parse_retry(value: str) -> int | None:
    """解析 retry 字段，非法值返回 None（不抛异常）。"""
    try:
        return int(value)
    except ValueError:
        logger.warning("SSE retry 字段值非法，忽略：%r", value)
        return None


def _apply_field(event: SSEEvent, field: str, value: str) -> None:
    """把单个字段写入事件；未知字段静默忽略（SSE 规范要求）。"""
    if field == "data":
        event.data = f"{event.data}{_DATA_SEPARATOR}{value}" if event.data else value
    elif field == "event":
        event.event = value
    elif field == "id":
        event.id = value
    elif field == "retry":
        event.retry = _parse_retry(value)
    else:
        logger.debug("SSE 未知字段，忽略：%r", field)


def _split_field(line: str) -> tuple[str, str] | None:
    """按第一个冒号切分字段名与值。

    值前可选一个空格（``data: x`` 与 ``data:x`` 均得 ``"x"``）。
    无冒号的行视为坏行，返回 None（忽略）。
    """
    if ":" not in line:
        logger.debug("SSE 无冒号坏行，忽略：%r", line)
        return None
    field, _, value = line.partition(":")
    if value.startswith(" "):
        value = value[1:]
    return field, value


def parse_sse_lines(lines: Iterable[str]) -> Iterator[SSEEvent]:
    """逐行消费 SSE 文本行，按空行分隔产出事件（同步纯函数）。

    Args:
        lines: SSE 文本行序列（不含结尾换行亦可，内部会剥离）。

    Yields:
        解析出的 :class:`SSEEvent`。
    """
    event = SSEEvent()
    has_data = False

    for raw_line in lines:
        line = _strip_line_ending(raw_line)
        # 事件边界：空行（"" 或纯 "\r"）→ dispatch 当前事件。
        if line == "":
            if has_data:
                yield event
            event = SSEEvent()
            has_data = False
            continue
        # 注释行：以 ":" 开头，忽略。
        if line.startswith(":"):
            continue
        parsed = _split_field(line)
        if parsed is None:
            continue
        field, value = parsed
        _apply_field(event, field, value)
        if field == "data":
            has_data = True

    # 流结束时若无结尾空行，宽容 dispatch 残留事件。
    if has_data:
        yield event


async def iter_sse_events(lines: AsyncIterable[str]) -> AsyncIterator[SSEEvent]:
    """异步包装：消费异步行迭代器（``httpx.Response.aiter_lines()`` 兼容）。

    Args:
        lines: 异步 SSE 文本行迭代器。

    Yields:
        解析出的 :class:`SSEEvent`。
    """
    buffer: list[str] = []
    async for line in lines:
        buffer.append(line)
        # 空行是事件边界，可增量 dispatch；此处直接复用同步解析器语义。
        if _strip_line_ending(line) == "":
            for event in parse_sse_lines(buffer):
                yield event
            buffer.clear()
    # 处理结尾残留事件。
    for event in parse_sse_lines(buffer):
        yield event
