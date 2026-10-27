"""LLM provider 抽象基类。

所有 LLM provider 实现必须继承 BaseProvider 并实现 async stream() 方法
（流式为唯一调用形态；聚合由 models.collect() 复用消费）。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.logging import logger
from app.services.llm.models import Message, StreamChunk
from app.utils.http_client import create_async_client


class ProviderStreamError(Exception):
    """Provider 在 200 流内报告的错误事件。

    ``retryable`` 由 provider 按错误类型判定；LLMClient 统一决定重试或终态。
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@asynccontextmanager
async def open_stream_response(
    *,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    proxy: str | None = None,
) -> AsyncIterator[httpx.Response]:
    """打开 LLM 流式响应，统一读取错误体后再抛状态错误。

    httpx 的流式响应在未读取时访问 ``response.text`` 会抛
    ``ResponseNotRead``。这里在 ``raise_for_status()`` 前 ``aread()``，
    保证 LLMClient 的参数降级（``_is_param_rejection``）与流式终态判定
    （``_is_stream_rejection``）能读到 body。

    三个 provider 共用本方法，避免各自重复实现时遗漏读取。
    """
    async with create_async_client(
        proxy=proxy,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        async with client.stream(
            "POST",
            url,
            json=body,
            headers=headers,
            timeout=timeout,
        ) as response:
            if not response.is_success:
                error_body = (await response.aread()).decode("utf-8", "replace")
                logger.warning(
                    f"LLM 流式请求失败: status={response.status_code}, "
                    f"body={error_body[:500]}"
                )
            response.raise_for_status()
            yield response


class BaseProvider(ABC):
    """LLM 服务提供者抽象基类。

    子类必须实现 async stream() 方法，接收消息列表和额外关键字参数，
    逐条产出归一化的 StreamChunk。

    Attributes:
        _extras_disabled: 端点拒绝扩展参数（thinking/reasoning 等）后由
            LLMClient 置位的降级标记——置位后 _build_request 不再构造
            扩展字段（双重保险第二道，见 client._is_param_rejection）。
        _force_tool_choice_degraded: 端点拒绝强制工具选择（如 thinking 模式
            下的服务端约束："Thinking mode does not support this
            tool_choice"）后由 LLMClient 置位的降级标记——置位后
            _build_request 将强制 tool_choice 降级为 auto。
    """

    _extras_disabled: bool = False
    _force_tool_choice_degraded: bool = False

    @abstractmethod
    def stream(
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:
        """流式调用（SSE 增量事件）。

        Args:
            messages: 表示对话历史的 Message 对象列表。
            **kwargs: provider 特定的额外参数。

        Yields:
            provider 无关的归一化流式事件 StreamChunk。
        """
        ...
