"""LLM 数据模型（纯数据结构层）。

定义聊天交互的核心 Pydantic 模型：Message、Usage 和 ChatResponse。
"""

from typing import Literal, Optional

from pydantic import BaseModel


class Message(BaseModel):
    """单条聊天消息，包含角色和内容。"""

    role: Literal["system", "user", "assistant"]
    content: str


class Usage(BaseModel):
    """聊天补全的 token 用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    """聊天补全请求的响应。

    包含助手的回复内容、使用的模型名称以及可选的 token 用量统计。
    """

    content: str
    model: str = ""
    usage: Optional[Usage] = None
    latency: int = 0
