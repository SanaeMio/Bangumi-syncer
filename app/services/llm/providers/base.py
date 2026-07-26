"""LLM provider 抽象基类。

所有 LLM provider 实现必须继承 BaseProvider 并实现 async chat() 方法。
"""

from abc import ABC, abstractmethod
from typing import Any

from app.services.llm.models import ChatResponse, Message


class BaseProvider(ABC):
    """LLM 服务提供者抽象基类。

    子类必须实现 async chat() 方法，接收消息列表和额外关键字参数，
    返回 ChatResponse。
    """

    @abstractmethod
    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """向 LLM 发送消息并返回响应。

        Args:
            messages: 表示对话历史的 Message 对象列表。
            **kwargs: provider 特定的额外参数。

        Returns:
            包含助手回复和可选用量统计的 ChatResponse。
        """
        ...
