"""OpenAI 兼容 API provider。

基于 httpx 实现的 BaseProvider，与任何遵循 OpenAI /v1/chat/completions
API 规范的端点通信。

流式（唯一调用形态）：``stream()`` 把 wire SSE 事件映射为归一化 StreamChunk；
内部中立模型 → OpenAI wire 格式的差异收敛在 ``_build_request`` 内：
- content 为 list[ContentBlock] 时取 text block 拼接（OpenAI wire 为字符串；
  thinking/tool 等 block 不适用于当前端点）
- thinking_level → reasoning_effort（仅 o 系列模型生效，其余忽略并告警）
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.logging import logger
from app.services.llm.models import (
    ContentBlock,
    Message,
    StreamChunk,
    TextBlock,
    ThinkingLevel,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from app.services.llm.providers.base import BaseProvider, open_stream_response
from app.services.llm.sse import iter_sse_events


@dataclass
class _CompatStreamState:
    """openai_compat 流式的跨事件状态（模块私有）。

    - ``model_seen``：真实模型名仅首个带 model 的事件填一次；
    - ``stopped``：已补发 tool_use_stop 的 tool_call index，避免重复补发。
    """

    model_seen: bool = False
    stopped: set[int] = field(default_factory=set)


class OpenAICompatProvider(BaseProvider):
    """OpenAI 兼容聊天补全 API 的 LLM provider。

    与任何遵循 OpenAI /v1/chat/completions API 规范的端点通信。

    Attributes:
        api_base: API 的基础 URL（如 https://api.openai.com/v1）。
        api_key: 用于认证的 Bearer token。
        model: 默认使用的模型名称。
        max_tokens: 补全的默认最大 token 数。
        temperature: 默认采样温度。
        timeout: 请求超时时间（秒）。
        proxy: 可选的 HTTP 代理 URL。
        thinking_level: 思考强度 off/low/medium/high（映射 reasoning_effort，
            仅 o 系列模型生效；每任务 kwargs 可覆盖）。
    """

    # thinking_level → OpenAI reasoning_effort 映射（off 不传 = 现状行为）
    _REASONING_EFFORT: dict[str, str] = {
        "low": "low",
        "medium": "medium",
        "high": "high",
    }

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        timeout: int = 60,
        proxy: str | None = None,
        thinking_level: ThinkingLevel = "off",
    ) -> None:
        """初始化 OpenAI 兼容 provider。

        Args:
            api_base: API 端点的基础 URL。
            api_key: 用于 Bearer token 认证的 API key。
            model: 补全使用的模型名称。
            max_tokens: 最大生成 token 数。
            temperature: 采样温度 (0.0-2.0)。
            timeout: HTTP 请求超时时间（秒）。
            proxy: 可选的 HTTP 代理 URL。
            thinking_level: 思考强度（reasoning_effort 映射，仅 o 系列）。
        """
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.proxy = proxy
        self.thinking_level = thinking_level

    async def stream(
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:
        """以 SSE 流式方式发送聊天补全请求。

        请求构造复用 :meth:`_build_request`（reasoning_effort 映射、
        extras_disabled / force_tool_choice_degraded 降级、tools 规范化
        全部沿用），并追加 stream / stream_options 以启用增量与 usage 上报。

        provider 层不重试、不吞异常：httpx 异常/状态错误直接向上抛，
        由 client 层负责重试与降级；已产出部分事件后发生的异常在
        迭代中自然抛出。

        Args:
            messages: 对话消息列表。
            **kwargs: 覆盖默认的 model、max_tokens、temperature、tools 等。

        Yields:
            provider 无关的归一化流式事件 StreamChunk。

        Raises:
            httpx.HTTPStatusError: HTTP 错误响应（非 2xx）。
            httpx.TimeoutException: 请求超时。
        """
        url = f"{self.api_base}/chat/completions"
        model = kwargs.get("model", self.model)
        proxy_label = f", proxy={self.proxy}" if self.proxy else ""
        logger.debug(
            f"LLM stream request: url={url}, model={model}, "
            f"timeout={self.timeout}s{proxy_label}"
        )

        body = self._build_request(messages, **kwargs)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # index → (id, name)：OpenAI 仅在首个 tool_call 分片给出 id/name，
        # 后续 arguments 增量需据此还原归属。
        tool_index: dict[int, tuple[str, str]] = {}
        # 跨事件状态：model_seen（真实模型名仅首个带 model 的事件填一次）、
        # stopped（已补发 tool_use_stop 的 tool_call index，避免重复补发）。
        state = _CompatStreamState()

        async with open_stream_response(
            url=url,
            body=body,
            headers=headers,
            timeout=self.timeout,
            proxy=self.proxy,
        ) as response:
            async for event in iter_sse_events(response.aiter_lines()):
                if event.data == "[DONE]":
                    logger.debug("LLM 流式收到 [DONE]，结束迭代")
                    return
                for chunk in self._map_stream_event(event.data, tool_index, state):
                    yield chunk

    def _map_stream_event(
        self,
        raw: str,
        tool_index: dict[int, tuple[str, str]],
        state: _CompatStreamState,
    ) -> list[StreamChunk]:
        """单个 OpenAI chat.completion.chunk → 零或多个 StreamChunk。

        tool_index 为跨事件维护的 index → (id, name) 映射（原地更新）。
        state 为跨事件状态：``model_seen`` 记录是否已填过真实 model；
        ``stopped`` 记录已补发 tool_use_stop 的 index。

        openai_compat 无 per-tool 停点信号：在 ``finish_reason`` 出现时为所有
        已收到但尚未补发停点的 tool_calls（按 index 升序）补发 tool_use_stop，
        并在其**之后**产出 stop 事件。
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # 坏事件不中断流：记录后跳过
            logger.warning("LLM 流式事件 JSON 解析失败，已忽略：%r", raw)
            return []
        if not isinstance(data, dict):
            logger.warning("LLM 流式事件非对象，已忽略：%r", data)
            return []

        chunks: list[StreamChunk] = []
        choices = data.get("choices") or []
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                chunks.append(StreamChunk(type="text_delta", text=content))
            chunks.extend(
                self._map_tool_call_deltas(delta.get("tool_calls") or [], tool_index)
            )
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                # tool_calls → tool_use，其余透传
                stop_reason = (
                    "tool_use" if finish_reason == "tool_calls" else finish_reason
                )
                chunks.extend(self._emit_tool_stops(tool_index, state))
                chunks.append(StreamChunk(type="stop", stop_reason=stop_reason))

        usage = data.get("usage")
        if usage:
            chunks.append(
                StreamChunk(
                    type="usage",
                    usage=Usage(
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                    ),
                )
            )

        model = data.get("model") or ""
        if model and not state.model_seen and chunks:
            # 真实模型名：首个带 model 的事件内首个产出事件填一次即可
            chunks[0].model = model
            state.model_seen = True
        return chunks

    @staticmethod
    def _emit_tool_stops(
        tool_index: dict[int, tuple[str, str]], state: _CompatStreamState
    ) -> list[StreamChunk]:
        """为所有已收到但尚未补发停点的 tool_calls 补发 tool_use_stop。"""
        stopped = state.stopped
        chunks: list[StreamChunk] = []
        for index in sorted(tool_index):
            if index in stopped:
                continue
            stopped.add(index)
            chunks.append(
                StreamChunk(type="tool_use_stop", tool_use_id=tool_index[index][0])
            )
        return chunks

    @staticmethod
    def _map_tool_call_deltas(
        tool_calls: list, tool_index: dict[int, tuple[str, str]]
    ) -> list[StreamChunk]:
        """tool_calls 分片数组 → tool_use_start / tool_use_delta 事件。"""
        chunks: list[StreamChunk] = []
        for tc in tool_calls:
            index = tc.get("index", 0)
            fn = tc.get("function") or {}
            name = fn.get("name")

            if index not in tool_index:
                # 首次出现即建槽，不依赖 name：首分片可能只带 index+id，name 缺失
                # （如某些兼容端点在 arguments 才补齐）。若等待 name 才建槽，后续
                # arguments 分片会产出无前置 tool_use_start 的孤立 delta，且丢失真实 id。
                call_id = tc.get("id") or f"call_{index}"
                tool_index[index] = (call_id, name or "")
                chunks.append(
                    StreamChunk(
                        type="tool_use_start",
                        tool_use_id=call_id,
                        tool_name=name or "",
                    )
                )

            arguments = fn.get("arguments")
            if arguments:
                call_id = tool_index.get(index, (tc.get("id") or f"call_{index}", ""))[
                    0
                ]
                chunks.append(
                    StreamChunk(
                        type="tool_use_delta",
                        tool_use_id=call_id,
                        partial_json=arguments,
                    )
                )
        return chunks

    def _build_request(self, messages: list[Message], **kwargs: Any) -> dict:
        """内部模型 → OpenAI wire 格式（请求体）。"""
        body: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": [wm for m in messages for wm in self._to_wire_messages(m)],
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }

        # thinking_level：每任务 kwargs 覆盖 > 全局默认；非 o 系列模型忽略并告警；
        # 本次调用被端点拒绝过扩展参数时（extras_disabled）不再发送
        extras_disabled = bool(kwargs.get("extras_disabled", False))
        force_tool_choice_degraded = bool(
            kwargs.get("force_tool_choice_degraded", False)
        )
        level = kwargs.get("thinking_level", self.thinking_level)
        effort = (
            None if extras_disabled else self._reasoning_effort(level, body["model"])
        )
        if effort is not None:
            body["reasoning_effort"] = effort
            # o 系列推理模型拒绝非 1 的 temperature（硬 400），
            # 与 Anthropic thinking 开启时的处理对齐（anthropic.py 强制 1）
            body["temperature"] = 1

        # 工具协议 wire 规范化（provider 拥有 wire 格式，agent/场景层保持 provider 无关）。
        # - tools 元素已是 {"type":"function","function":{...}} → 透传；否则（内部
        #   flat 形态 name/description/parameters）→ 包装为 function 形式。
        # - tool_choice "none"/"auto"/"required" 原样透传；其它字符串（工具名）
        #   → {"type":"function","function":{"name":<str>}}；dict 透传；
        #   None → 不发送该键（避免序列化为 null）。
        if "tools" in kwargs:
            tools = kwargs["tools"]
            if tools is not None:
                body["tools"] = [self._normalize_openai_tool(t) for t in tools]
        if "tool_choice" in kwargs:
            tc = kwargs["tool_choice"]
            if tc is not None:
                normalized_tc = self._normalize_openai_tool_choice(tc)
                if (
                    force_tool_choice_degraded
                    and isinstance(normalized_tc, dict)
                    and normalized_tc.get("type") == "function"
                ):
                    # 端点级降级（client 依据服务端拒绝置位）：该端点不支持
                    # 强制工具选择（如 thinking 模式约束），改为 auto。
                    logger.warning(
                        "已按端点约束将强制 tool_choice 降级为 auto"
                        "（该端点不支持强制工具选择）"
                    )
                    normalized_tc = "auto"
                body["tool_choice"] = normalized_tc
        # cache_control 是 Anthropic 专属参数，OpenAI 无此字段——忽略不发送，
        # 即便调用方误传也不得进入请求体（否则部分端点报错）
        body.pop("cache_control", None)
        return body

    def _to_wire_messages(self, m: Message) -> list[dict]:
        """内部消息 → 一条或多条 OpenAI wire 消息（工具协议拆并）。

        - str 内容：单条 {role, content}
        - assistant 消息含 ToolUseBlock：聚合为单条 assistant 消息
          （content 与 tool_calls 共存；无文本时 content=null）
        - user 消息含 ToolResultBlock：文本先输出为独立 user 消息，
          再逐条拆为 role=tool 消息（is_error 加 [ERROR] 前缀）

        多段 TextBlock 的拼接口径：**空串连接**，与聚合器 ``ChatResponse.content``
        一致。chat completions 的 content 是单个字符串，协议无法表达
        「文本 → 工具调用 → 文本」的交错，只能降级为拼接；此时若注入 wire 上
        不存在的 ``"\\n\\n"``，模型读到的语义会被改写（"A\\n\\nB 然后调工具"
        vs 实际的"A 调工具，然后说 B"）。system 等**无工具**消息走末尾的纯文本
        分支，其多块仍以 ``"\\n\\n"`` 连接 —— 多块本就代表多条独立规则，
        与 Anthropic 侧 ``_system_text`` 同一分隔语义。
        """
        if isinstance(m.content, str):
            return [{"role": m.role, "content": m.content}]

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[ToolResultBlock] = []
        for block in m.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            # arguments 必须为 JSON 字符串（OpenAI wire 约定）
                            "arguments": json.dumps(block.input, ensure_ascii=False),
                        },
                    }
                )
            elif isinstance(block, ToolResultBlock):
                tool_results.append(block)

        if m.role == "assistant" and tool_calls:
            content = "".join(text_parts) if text_parts else None
            return [{"role": "assistant", "content": content, "tool_calls": tool_calls}]

        if m.role == "user" and tool_results:
            result: list[dict] = []
            if text_parts:
                result.append({"role": "user", "content": "".join(text_parts)})
            for tr in tool_results:
                prefix = "[ERROR] " if tr.is_error else ""
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.tool_use_id,
                        "content": prefix + tr.content,
                    }
                )
            return result

        # 默认：纯文本（或未知组合）→ 单条文本消息。
        # 分隔符按role 分层（协议无 block 结构，只能降级为单字符串）：
        # - system：多块本就代表多条独立规则，用 ``\n\n`` 保留其边界
        #   （与 Anthropic 侧 ``_system_text`` 同一分隔语义）；
        # - assistant / user：多段文本是**同一次发言被 wire 切开**的结果
        #   （模型发出的是连续 delta），注入 ``\n\n`` 会凭空改写语义
        #   （"A\n\nB" vs 模型实际说出的 "AB"），故空串连接、忠实还原。
        separator = "\n\n" if m.role == "system" else ""
        return [{"role": m.role, "content": separator.join(text_parts)}]

    @staticmethod
    def _normalize_openai_tool(tool: dict) -> dict:
        """将工具 schema 规范化为 OpenAI wire 形态。

        - 已是 {"type":"function","function":{...}} → 透传；
        - 否则（内部 flat 形态 name/description/parameters）→ 包装为 function 形式。
        """
        if tool.get("type") == "function" and "function" in tool:
            return tool
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool.get("parameters", {}),
            },
        }

    _OPENAI_TOOL_CHOICE_LITERALS = frozenset({"none", "auto", "required"})

    @classmethod
    def _normalize_openai_tool_choice(cls, tool_choice: Any) -> Any:
        """规范化 tool_choice：

        - "none"/"auto"/"required" 原样透传；
        - 其它字符串（工具名）→ {"type":"function","function":{"name":<str>}}；
        - dict 透传。
        """
        if isinstance(tool_choice, str):
            if tool_choice in cls._OPENAI_TOOL_CHOICE_LITERALS:
                return tool_choice
            return {"type": "function", "function": {"name": tool_choice}}
        return tool_choice

    def _reasoning_effort(self, level: str, model: str) -> str | None:
        """thinking_level → reasoning_effort；off/不支持时返回 None（不传字段）。"""
        if level == "off":
            return None
        # reasoning_effort 仅 o 系列模型支持（o1/o3/o4-mini 等）。用 ^o\d 而非
        # startswith("o")：避免 "ollama/…"、"openrouter/…" 等第三方网关模型名
        # 误中导致向不支持的端点发送未知参数。OpenAI 对未知参数的行为因 API 版本
        # 而异，不冒险传给非 o 系列。
        if not re.match(r"^o\d", model):
            # 配置/模型不匹配是静态事实，warning 刷屏无益——降为 debug
            # （用户可通过 stats/日志在调优期定位）
            logger.debug(
                f"model {model} 非 o 系列不支持 reasoning_effort，"
                f"已忽略 thinking_level={level}"
            )
            return None
        return self._REASONING_EFFORT.get(level)

    def _wire_to_messages(self, wire_messages: list[dict]) -> list[Message]:
        """OpenAI wire 消息列表 → 内部消息列表（历史合并）。

        连续的 role=tool 消息合并回一条 user 消息的多个 ToolResultBlock
        （与 _to_wire_messages 的拆分方向互逆）；普通消息按文本/TextBlock 还原。
        """
        result: list[Message] = []
        i = 0
        n = len(wire_messages)
        while i < n:
            msg = wire_messages[i]
            role = msg.get("role")
            if role == "tool":
                # 收集连续 role=tool 块，合并为单条 user 消息
                # 元素类型用 ContentBlock 联合而非 ToolResultBlock：list 不变性下
                # 精确的 list[ToolResultBlock] 不可赋给 Message.content 的
                # list[ContentBlock]；运行时元素仍全是 ToolResultBlock。
                tool_blocks: list[ContentBlock] = []
                while i < n and wire_messages[i].get("role") == "tool":
                    tm = wire_messages[i]
                    raw = tm.get("content", "")
                    is_error = False
                    if isinstance(raw, str) and raw.startswith("[ERROR] "):
                        is_error = True
                        raw = raw[len("[ERROR] ") :]
                    tool_blocks.append(
                        ToolResultBlock(
                            tool_use_id=tm.get("tool_call_id", ""),
                            content=raw,
                            is_error=is_error,
                        )
                    )
                    i += 1
                result.append(Message(role="user", content=tool_blocks))
                continue

            # 收窄 wire role 为 Message 允许的 Literal（tool 已在上面分流）
            wire_role: Literal["system", "user", "assistant"]
            if role == "system":
                wire_role = "system"
            elif role == "user":
                wire_role = "user"
            elif role == "assistant":
                wire_role = "assistant"
            else:
                raise ValueError(f"非法 wire role: {role!r}")

            content = msg.get("content")
            if isinstance(content, str):
                result.append(Message(role=wire_role, content=content))
            elif isinstance(content, list):
                # 多模态/列表内容：仅取 text 部分还原为 TextBlock
                blocks: list[ContentBlock] = [
                    TextBlock(text=part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                result.append(Message(role=wire_role, content=blocks))
            else:
                # content 为 None（如纯 tool_call 的 assistant 消息）→ 空文本
                result.append(Message(role=wire_role, content=""))
            i += 1
        return result
