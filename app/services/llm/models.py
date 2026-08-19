"""LLM 数据模型（纯数据结构层）。

定义聊天交互的核心 Pydantic 模型：Message、ContentBlock、Usage 和 ChatResponse。

Message.content 支持纯文本（str，旧用法）或 content blocks。
ContentBlock 是内部归一化模型，形状对齐 Anthropic
Messages API 的 content blocks，各 provider 负责与自己的 wire 格式互转。
"""

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

ThinkingLevel = Literal["off", "low", "medium", "high"]


class TextBlock(BaseModel):
    """文本内容块。"""

    type: Literal["text"] = "text"
    text: str


class ThinkingBlock(BaseModel):
    """思考内容块（Anthropic extended thinking）。"""

    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: Optional[str] = None  # Anthropic 的 thinking signature


class RedactedThinkingBlock(BaseModel):
    """被遮蔽的思考内容块（Anthropic 签名验证安全机制）。"""

    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str


ContentBlock = Union[TextBlock, ThinkingBlock, RedactedThinkingBlock]


class Message(BaseModel):
    """单条聊天消息，包含角色和内容。

    content 兼容旧用法（纯文本 str）；富内容场景使用 list[ContentBlock]。
    """

    role: Literal["system", "user", "assistant"]
    content: Union[str, list[ContentBlock]]


class Usage(BaseModel):
    """聊天补全的 token 用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    """聊天补全请求的响应。

    content 为纯文本（无 tool call 时），旧代码照常用；blocks 携带完整内容块
    （含 thinking），供 Phase 3 的 agent 循环消费；stop_reason 为统一结束原因
    （end_turn / tool_use / max_tokens）。
    """

    content: str
    blocks: list[ContentBlock] = Field(default_factory=list)
    stop_reason: str = ""
    model: str = ""
    usage: Optional[Usage] = None
    latency: int = 0
