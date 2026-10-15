"""LLM provider 抽象基类。

所有 LLM provider 实现必须继承 BaseProvider 并实现 async stream() 方法
（流式为唯一调用形态；聚合由 models.collect() 复用消费）。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from app.services.llm.models import Message, StreamChunk


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
