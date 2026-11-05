"""LLM 数据模型（纯数据结构层）。

定义聊天交互的核心 Pydantic 模型：Message、ContentBlock、Usage 和 ChatResponse。

Message.content 支持纯文本（str，旧用法）或 content blocks。
ContentBlock 是内部归一化模型，形状对齐 Anthropic
Messages API 的 content blocks，各 provider 负责与自己的 wire 格式互转。
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ThinkingLevel = Literal["off", "low", "medium", "high"]


class TextBlock(BaseModel):
    """文本内容块。"""

    type: Literal["text"] = "text"
    text: str


class ThinkingBlock(BaseModel):
    """思考内容块（Anthropic extended thinking）。"""

    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str | None = None  # Anthropic 的 thinking signature


class RedactedThinkingBlock(BaseModel):
    """被遮蔽的思考内容块（Anthropic 签名验证安全机制）。"""

    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str


class ToolUseBlock(BaseModel):
    """工具调用请求块（Anthropic tool_use / OpenAI tool_calls 归一化）。

    - id: 工具调用唯一标识，下游 ToolResult 据此关联
    - name: 工具名称（与 ToolRegistry 注册名一致）
    - input: 工具入参（JSON 对象，默认空 dict）
    """

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict = {}


class ToolResultBlock(BaseModel):
    """工具执行结果块（Anthropic tool_result / OpenAI role=tool 归一化）。

    - tool_use_id: 对应的 ToolUseBlock.id
    - content: 结果文本（可为 JSON 字符串）
    - is_error: 工具执行是否失败（供循环自我纠正）
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


# 向后兼容：Text/Thinking/Redacted 现有行为不变；扩展追加
# ToolUse/ToolResult 两类工具协议块。
ContentBlock = (
    TextBlock | ThinkingBlock | RedactedThinkingBlock | ToolUseBlock | ToolResultBlock
)


class Message(BaseModel):
    """单条聊天消息，包含角色和内容。

    content 兼容旧用法（纯文本 str）；富内容场景使用 list[ContentBlock]。
    """

    role: Literal["system", "user", "assistant"]
    content: str | list[ContentBlock]


class Usage(BaseModel):
    """聊天补全的 token 用量统计。

    缓存字段（Anthropic prompt caching）语义：

    - ``cache_creation_input_tokens``：本次因写入缓存而额外计费的输入 token
    - ``cache_read_input_tokens``：本次从缓存命中读取的输入 token

    两者为 0 表示端点未返回缓存信息或未启用缓存。缓存命中的 token 仍计入
    ``prompt_tokens``（Anthropic 的 input_tokens 已含缓存部分），故
    ``total_tokens`` 口径不变，下游计费/配额逻辑无需改动。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class ChatResponse(BaseModel):
    """聊天补全请求的响应。

    content 为纯文本（无 tool call 时），旧代码照常用；blocks 携带完整内容块
    （含 thinking），供 agent 循环消费；stop_reason 为统一结束原因
    （end_turn / tool_use / max_tokens）。
    """

    content: str
    blocks: list[ContentBlock] = Field(default_factory=list)
    stop_reason: str = ""
    model: str = ""
    usage: Usage | None = None
    latency: int = 0


@dataclass
class StreamChunk:
    """provider 无关的流式归一化事件（SSE 单条增量）。

    各 provider 负责把自己的 wire 流式事件映射到本模型：

    - text_delta / thinking_delta: 增量文本，分别填 text / thinking（signature 亦增量）
    - redacted_thinking_delta: 被遮蔽的思考块（Anthropic 安全机制），填 redacted_data。
      该块**不可解码但必须原样回传**，否则多轮对话被端点拒绝
    - tool_use_start: 新建工具调用槽，填 tool_use_id / tool_name
    - tool_use_delta: 工具入参 JSON 增量片段，填 tool_use_id / partial_json
    - tool_use_stop: **停点事件**——该工具（tool_use_id）参数已完整、可校验并执行。
      能力分级：支持 per-tool 停点信号的 provider（anthropic content_block_stop、
      openai_responses output_item.done）原生映射；openai_compat 无该信号，
      在 finish_reason 出现时对尚未补发停点的 tool_calls 补发。
    - usage: 填 usage
    - stop: 填 stop_reason

    ``model`` 为真实模型名（provider 从流事件顶层字段提取），通常首个带 model 的
    事件填入一次即可，后续留空；供 client/聚合器回填 ChatResponse.model。

    ``block_index`` 为 wire 侧**内容块序号**（Anthropic content_block 的 index、
    Responses 的 item 序号等）。同一序号的多段增量属同一个块，聚合器据此分桶——
    这是 ``text(A) → tool_use → text(B)`` 这类交错输出不被压平成单个 TextBlock、
    多段 thinking 的 signature 不被拼成非法值的前提。未提供时取 ``UNINDEXED``，
    全部增量归入**单一槽**合并成单块：openai_compat 的 chat.completion delta 只有
    一条连续 content 流、无块概念，合并即其 wire 语义。
    """

    #: 未声明块序号的哨兵值。真实 wire 序号从 0 起，故不能用 0，
    #: 否则「未提供」会与「第 0 块」混为一谈、把不同来源的增量错误合桶。
    UNINDEXED: ClassVar[int] = -1

    type: Literal[
        "text_delta",
        "thinking_delta",
        "redacted_thinking_delta",
        "tool_use_start",
        "tool_use_delta",
        "tool_use_stop",
        "usage",
        "stop",
    ]
    text: str = ""
    thinking: str = ""
    signature: str = ""
    redacted_data: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    partial_json: str = ""
    usage: Usage | None = None
    stop_reason: str | None = None
    model: str = ""
    block_index: int = UNINDEXED


@dataclass
class StreamAggregator:
    """把 StreamChunk 序列聚合为等价 ChatResponse。

    聚合规则：

    - text_delta / thinking_delta 按 ``StreamChunk.block_index`` **分桶**：同一序号的
      多段增量合并为一个 TextBlock / ThinkingBlock（thinking 与 signature 各自拼接）。
      分桶是保持 wire 块边界的前提——``text(A) → tool_use → text(B)`` 必须产出三块
      （顺序 A、tool、B），不能压平成 ``[TextBlock("AB"), tool_use]``；多段 thinking
      的 signature 也必须各自独立，拼接后的 ``"sig1sig2"`` 会被 Anthropic 端点拒绝。
    - 序号为 ``StreamChunk.UNINDEXED``（provider 未提供块概念，如 openai_compat 的
      单条 content 流）时全部增量归入同一槽，产出单个块——即该 wire 的真实语义。
    - 空文本增量不建桶、不产出空块（部分端点会发 text_delta(text="")）
    - redacted_thinking_delta → 每个序号一个 RedactedThinkingBlock（按序号分桶，
      多段遮蔽思考各自成块；data 原样保留，不做拼接）
    - tool_use_start → 新建 ToolUseBlock 槽；tool_use_delta 按 tool_use_id
      累积 partial_json，finalize 时 json.loads，失败兜底 {"raw": ...}
    - tool_use_stop 为停点标记，不参与参数累积（参数照常从 delta 累积）
    - usage / stop 事件透传到 ChatResponse 的 usage / stop_reason
    - 非空 model 事件跟踪为真实模型名，finalize 回填 ChatResponse.model
    - blocks 顺序 = 各块「首次出现」的事件顺序（**不按序号重排**：序号仅用于分桶，
      顺序以事件到达为准，与 wire 一致）
    """

    _order: list[tuple[str, str]] = field(default_factory=list)
    _text_parts: dict[int, list[str]] = field(default_factory=dict)
    _thinking_parts: dict[int, list[str]] = field(default_factory=dict)
    _signature_parts: dict[int, list[str]] = field(default_factory=dict)
    _redacted_parts: dict[int, list[str]] = field(default_factory=dict)
    _tool_names: dict[str, str] = field(default_factory=dict)
    _tool_json_parts: dict[str, list[str]] = field(default_factory=dict)
    _usage: Usage | None = None
    _stop_reason: str | None = None
    _model: str = ""

    def feed(self, chunk: StreamChunk) -> None:
        """累积一个流式事件。"""
        if chunk.model:
            self._model = chunk.model
        if chunk.type == "text_delta":
            self._append_text(chunk)
        elif chunk.type == "thinking_delta":
            self._append_thinking(chunk)
        elif chunk.type == "redacted_thinking_delta":
            self._append_redacted(chunk)
        elif chunk.type == "tool_use_start":
            self._start_tool(chunk.tool_use_id, chunk.tool_name)
        elif chunk.type == "tool_use_delta":
            self._append_tool_json(chunk.tool_use_id, chunk.partial_json)
        elif chunk.type == "tool_use_stop":
            # 停点事件：参数已从 tool_use_delta 累积完毕，无需额外处理
            pass
        elif chunk.type == "usage":
            self._usage = chunk.usage
        elif chunk.type == "stop":
            self._stop_reason = chunk.stop_reason
        else:
            # 防御兜底：Literal 已限定类型，未知事件仅记录不中断
            logger.warning("StreamAggregator 收到未知事件类型: %r", chunk.type)

    def _append_text(self, chunk: StreamChunk) -> None:
        """按 block_index 追加文本增量；首次出现该序号时登记一次块位。"""
        # 空文本不建桶：否则会产出 text="" 的空 TextBlock，回传时是无意义的协议噪声
        if not chunk.text:
            return
        index = chunk.block_index
        if index not in self._text_parts:
            self._order.append(("text", str(index)))
            self._text_parts[index] = []
        self._text_parts[index].append(chunk.text)

    def _append_thinking(self, chunk: StreamChunk) -> None:
        """按 block_index 追加思考增量与签名增量，各自独立成块。"""
        index = chunk.block_index
        if index not in self._thinking_parts:
            self._order.append(("thinking", str(index)))
            self._thinking_parts[index] = []
            self._signature_parts[index] = []
        self._thinking_parts[index].append(chunk.thinking)
        self._signature_parts[index].append(chunk.signature)

    def _append_redacted(self, chunk: StreamChunk) -> None:
        """按 block_index 追加被遮蔽的思考数据；每个序号一个独立块。

        data 是端点加密后的不透明串，**不做跨块拼接**——拼接后的值不是任何
        合法签名，回传即被拒。
        """
        index = chunk.block_index
        if index not in self._redacted_parts:
            self._order.append(("redacted_thinking", str(index)))
            self._redacted_parts[index] = []
        self._redacted_parts[index].append(chunk.redacted_data)

    def _start_tool(self, tool_use_id: str, tool_name: str) -> None:
        if tool_use_id in self._tool_names:
            logger.warning("StreamAggregator 收到重复 tool_use_start: %s", tool_use_id)
            return
        self._tool_names[tool_use_id] = tool_name
        self._tool_json_parts[tool_use_id] = []
        self._order.append(("tool_use", tool_use_id))

    def _append_tool_json(self, tool_use_id: str, partial_json: str) -> None:
        if tool_use_id not in self._tool_json_parts:
            # 防御兜底：缺少 tool_use_start 的增量片段，按新槽处理并记录
            logger.warning(
                "StreamAggregator 收到无 start 的 tool_use_delta: %s", tool_use_id
            )
            self._tool_names[tool_use_id] = ""
            self._tool_json_parts[tool_use_id] = []
            self._order.append(("tool_use", tool_use_id))
        self._tool_json_parts[tool_use_id].append(partial_json)

    def _build_tool_block(self, tool_use_id: str) -> ToolUseBlock:
        raw = "".join(self._tool_json_parts.get(tool_use_id, []))
        try:
            parsed = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            # 与 providers/openai_compat.py 的解析失败兜底对齐
            parsed = {"raw": raw}
        return ToolUseBlock(
            id=tool_use_id,
            name=self._tool_names.get(tool_use_id, ""),
            input=parsed,
        )

    def finalize(self) -> ChatResponse:
        """产出终态 ChatResponse（blocks 按首次出现顺序）。"""
        blocks: list[ContentBlock] = []
        for kind, key in self._order:
            if kind == "text":
                blocks.append(TextBlock(text="".join(self._text_parts[int(key)])))
            elif kind == "thinking":
                blocks.append(
                    ThinkingBlock(
                        thinking="".join(self._thinking_parts[int(key)]),
                        signature="".join(self._signature_parts[int(key)]) or None,
                    )
                )
            elif kind == "redacted_thinking":
                blocks.append(
                    RedactedThinkingBlock(data="".join(self._redacted_parts[int(key)]))
                )
            elif kind == "tool_use":
                blocks.append(self._build_tool_block(key))
            else:
                # 防御兜底：未知块标记仅记录不中断
                logger.warning("StreamAggregator 遇到未知块标记: %r", kind)

        text = "".join(b.text for b in blocks if isinstance(b, TextBlock))
        return ChatResponse(
            content=text,
            blocks=blocks,
            stop_reason=self._stop_reason or "",
            model=self._model,
            usage=self._usage,
        )


async def collect(stream: AsyncIterator[StreamChunk]) -> ChatResponse:
    """把事件流消费至结束并聚合为完整响应（消费方式之一，非独立模式）。"""
    aggregator = StreamAggregator()
    async for chunk in stream:
        aggregator.feed(chunk)
    return aggregator.finalize()
