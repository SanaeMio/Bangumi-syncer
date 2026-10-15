"""Anthropic Messages API provider。

基于 httpx 实现的 BaseProvider，与任何遵循 Anthropic /v1/messages
API 规范的端点通信（官方 API 或兼容代理/网关）。

流式（唯一调用形态）：``stream()`` 把 wire SSE 事件映射为归一化 StreamChunk；
内部中立模型 → Anthropic wire 格式的差异收敛在 ``_build_request`` 内：
- system prompt 抽为顶层参数（多条用 \\n\\n 连接）
- content 统一为 content blocks 数组
- thinking_level 映射为 thinking.budget_tokens（模型不支持时降级）
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

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
from app.services.llm.providers.base import BaseProvider
from app.services.llm.sse import SSEEvent, iter_sse_events
from app.utils.http_client import create_async_client


@dataclass
class _AnthropicStreamState:
    """Anthropic 流式的跨事件状态（模块私有）。

    - ``tool_index``：content block index → (tool_use_id, tool_name)
      （input_json_delta 只带 index，需据此回填 tool_use_id）；
    - ``input_tokens``：message_start 的输入用量，待 message_delta 合并；
    - ``cache_creation_input_tokens`` / ``cache_read_input_tokens``：message_start
      携带的 prompt cache 读写用量（可观测缓存命中率，默认 0）；
    - ``stopped``：已补发 tool_use_stop 的 index，避免重复补发。
    """

    tool_index: dict[int, tuple[str, str]] = field(default_factory=dict)
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    stopped: set[int] = field(default_factory=set)


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API 的 LLM provider。

    与任何遵循 Anthropic /v1/messages API 规范的端点通信。

    Attributes:
        api_base: API 的基础 URL（如 https://api.anthropic.com/v1）。
        api_key: 用于认证的 API key（随 x-api-key 与 Authorization: Bearer 双头发送，
            兼顾官方 API 与 OpenAI 风格网关）。
        model: 默认使用的模型名称。
        max_tokens: 补全的默认最大 token 数（Anthropic API 必填）。
        temperature: 默认采样温度（thinking 开启时被强制为 1）。
        timeout: 请求超时时间（秒）。
        proxy: 可选的 HTTP 代理 URL。
        thinking_level: 思考强度 off/low/medium/high（每任务 kwargs 可覆盖）。
        prompt_cache: 是否给 system 打 cache_control 断点（Anthropic prompt
            caching）。system 前缀逐轮稳定，开启后多轮对话不必重复计费
            system；关闭则不发断点（短 prompt 或按量计费场景）。
    """

    # thinking_level → Anthropic budget_tokens 映射
    # 键为 str：_thinking_enabled 需对无效值兜底为 0（ThinkingLevel 约束在构造参数）
    _THINKING_BUDGETS: dict[str, int] = {
        "off": 0,
        "low": 2048,
        "medium": 4096,
        "high": 8192,
    }

    # thinking 开启时 max_tokens 的最低抬升余量：Anthropic 约束 budget_tokens
    # 必须小于 max_tokens（思考 token 计入 max_tokens 上限），默认 max_tokens=2000
    # 小于三档 budget（2048/4096/8192），需自动抬升并留出实际输出空间。
    _THINKING_HEADROOM: int = 1024

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        timeout: int = 60,
        proxy: str | None = None,
        thinking_level: ThinkingLevel = "off",
        prompt_cache: bool = True,
    ) -> None:
        """初始化 Anthropic provider。"""
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.proxy = proxy
        self.thinking_level = thinking_level
        self.prompt_cache = prompt_cache

    async def stream(
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:
        """以 SSE 流式方式向 API 发送请求，逐条产出归一化事件。

        请求体复用 :meth:`_build_request`（thinking budget 映射、max_tokens
        抬升、temperature、tool_choice 降级等逻辑），并追加 ``stream: true``。
        响应逐行交给 :func:`iter_sse_events` 解析后映射为 provider 无关的
        :class:`StreamChunk`。

        Args:
            messages: 对话消息列表。
            **kwargs: 覆盖默认的 model、max_tokens、temperature 或 thinking_level。

        Yields:
            归一化流式事件（text/thinking/tool_use/usage/stop）。

        Raises:
            httpx.HTTPStatusError: 非 2xx 响应（不重试、不吞异常）。
            httpx.TimeoutException: 请求超时。
        """
        url = f"{self.api_base}/messages"
        model = kwargs.get("model", self.model)
        proxy_label = f", proxy={self.proxy}" if self.proxy else ""
        logger.debug(
            f"LLM stream request: url={url}, model={model}, "
            f"timeout={self.timeout}s{proxy_label}"
        )

        body = self._build_request(messages, **kwargs)
        body["stream"] = True
        headers = self._auth_headers()

        async with create_async_client(
            proxy=self.proxy,
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers=headers,
                timeout=self.timeout,
            ) as response:
                response.raise_for_status()
                async for chunk in self._iter_stream_chunks(response.aiter_lines()):
                    yield chunk

    def _auth_headers(self) -> dict[str, str]:
        """构造认证/内容头。

        x-api-key 为 Anthropic 官方文档标准认证头，Authorization: Bearer 兼容
        OpenAI 风格网关——双发对两类端点都兼容且无害。
        """
        return {
            "Authorization": f"Bearer {self.api_key}",
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

    async def _iter_stream_chunks(
        self, lines: AsyncIterator[str]
    ) -> AsyncIterator[StreamChunk]:
        """消费 SSE 文本行，按 Anthropic 事件语义产出 StreamChunk。

        跨事件状态：``tool_index`` 维护 content block index → (tool_use_id,
        tool_name)（input_json_delta 只带 index，需据此回填 tool_use_id）；
        ``input_tokens`` 暂存 message_start 的输入用量，待 message_delta 合并；
        ``pending_model`` 暂存 message_start 的真实模型名，附到首个产出事件上。
        """
        state = _AnthropicStreamState()
        pending_model = ""
        model_sent = False

        def _attach_model(chunk: StreamChunk) -> StreamChunk:
            nonlocal model_sent
            if pending_model and not model_sent:
                chunk.model = pending_model
                model_sent = True
            return chunk

        async for event in iter_sse_events(lines):
            payload = self._decode_sse_payload(event)
            if payload is None:
                continue
            etype = event.event or payload.get("type")

            if etype == "message_start":
                state.input_tokens = self._extract_input_tokens(payload)
                (
                    state.cache_creation_input_tokens,
                    state.cache_read_input_tokens,
                ) = self._extract_cache_tokens(payload)
                pending_model = self._extract_model(payload)
            elif etype == "content_block_start":
                chunk = self._map_content_block_start(payload, state)
                if chunk is not None:
                    yield _attach_model(chunk)
            elif etype == "content_block_delta":
                chunk = self._map_content_block_delta(payload, state)
                if chunk is not None:
                    yield _attach_model(chunk)
            elif etype == "content_block_stop":
                chunk = self._map_content_block_stop(payload, state)
                if chunk is not None:
                    yield _attach_model(chunk)
            elif etype == "message_delta":
                usage_chunk, stop_chunk = self._map_message_delta(payload, state)
                yield _attach_model(usage_chunk)
                yield _attach_model(stop_chunk)
            elif etype == "message_stop":
                return
            else:
                # 宽容处理：SSE 规范允许未知事件类型，忽略并记录
                logger.debug("忽略未知 SSE 事件类型：%r", etype)

    @staticmethod
    def _decode_sse_payload(event: SSEEvent) -> dict | None:
        """解析 SSE 事件的 data 为 dict；空 data 或非法 JSON 返回 None。"""
        if not event.data:
            logger.debug("SSE 事件无 data，忽略：event=%r", event.event)
            return None
        try:
            return json.loads(event.data)
        except json.JSONDecodeError:
            logger.warning("SSE data 非合法 JSON，忽略：%r", event.data)
            return None

    @staticmethod
    def _extract_input_tokens(payload: dict) -> int:
        """从 message_start 事件提取输入 token 数。"""
        message = payload.get("message") or {}
        usage = message.get("usage") or {}
        return int(usage.get("input_tokens", 0) or 0)

    @staticmethod
    def _extract_cache_tokens(payload: dict) -> tuple[int, int]:
        """从 message_start 提取 (缓存写入, 缓存读取) token 数。

        端点未返回该字段时为 (0, 0)——即无可观测的缓存活动。
        """
        message = payload.get("message") or {}
        usage = message.get("usage") or {}
        created = int(usage.get("cache_creation_input_tokens", 0) or 0)
        read = int(usage.get("cache_read_input_tokens", 0) or 0)
        return created, read

    @staticmethod
    def _extract_model(payload: dict) -> str:
        """从 message_start 事件提取真实模型名。"""
        message = payload.get("message") or {}
        return str(message.get("model") or "")

    @staticmethod
    def _map_content_block_start(
        payload: dict, state: _AnthropicStreamState
    ) -> StreamChunk | None:
        """content_block_start → 起止块对应事件（序号一并透传）。

        四类起止块：
        - ``tool_use`` → tool_use_start，并登记 index → (id, name) 供后续
          input_json_delta 回填 tool_use_id
        - ``text`` / ``thinking`` → 起止块**可能自带非空初始内容**（部分响应把首个
          片段放在 start 里），须产出等价增量事件，否则该内容永久丢失。空串则不产出
          事件（避免聚合出 text="" 的空块）
        - ``redacted_thinking`` → 被遮蔽的思考块，必须原样回传，不产出即多轮报错
        """
        block = payload.get("content_block") or {}
        index = int(payload.get("index", 0) or 0)
        btype = block.get("type")

        if btype == "tool_use":
            tool_use_id = block.get("id", "")
            tool_name = block.get("name", "")
            state.tool_index[index] = (tool_use_id, tool_name)
            return StreamChunk(
                type="tool_use_start",
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                block_index=index,
            )

        if btype == "text":
            text = block.get("text", "") or ""
            if not text:
                return None
            return StreamChunk(type="text_delta", text=text, block_index=index)

        if btype == "thinking":
            thinking = block.get("thinking", "") or ""
            signature = block.get("signature", "") or ""
            if not thinking and not signature:
                return None
            return StreamChunk(
                type="thinking_delta",
                thinking=thinking,
                signature=signature,
                block_index=index,
            )

        if btype == "redacted_thinking":
            data = block.get("data", "") or ""
            if not data:
                return None
            return StreamChunk(
                type="redacted_thinking_delta",
                redacted_data=data,
                block_index=index,
            )

        return None

    @staticmethod
    def _map_content_block_stop(
        payload: dict, state: _AnthropicStreamState
    ) -> StreamChunk | None:
        """content_block_stop → tool_use_stop（仅对 tool_use 块，且不重复补发）。

        Anthropic 的 content_block_stop 标志该 content block 结束，对 tool_use
        块即意味着其 input_json 已完整、可校验——映射为停点事件。
        """
        index = payload.get("index", 0)
        if index not in state.tool_index or index in state.stopped:
            return None
        state.stopped.add(index)
        return StreamChunk(type="tool_use_stop", tool_use_id=state.tool_index[index][0])

    @staticmethod
    def _map_content_block_delta(
        payload: dict, state: _AnthropicStreamState
    ) -> StreamChunk | None:
        """content_block_delta → 对应增量事件；未知 delta 类型返回 None。"""
        delta = payload.get("delta") or {}
        dtype = delta.get("type")
        # 序号透传：同一 content block 的增量须归入同一槽，交错块才不会互相压平
        index = int(payload.get("index", 0) or 0)
        if dtype == "text_delta":
            return StreamChunk(
                type="text_delta", text=delta.get("text", ""), block_index=index
            )
        if dtype == "thinking_delta":
            return StreamChunk(
                type="thinking_delta",
                thinking=delta.get("thinking", ""),
                block_index=index,
            )
        if dtype == "signature_delta":
            # thinking signature 增量：映射为 thinking_delta，仅填 signature 字段。
            # 签名需在同一 block_index 内完整拼接后随 thinking block 回传，否则多轮
            # 对话被端点拒绝；跨块拼接则是非法值，故序号必须透传。
            return StreamChunk(
                type="thinking_delta",
                signature=delta.get("signature", ""),
                block_index=index,
            )
        if dtype == "input_json_delta":
            tool_use_id = state.tool_index.get(index, ("", ""))[0]
            if not tool_use_id:
                logger.warning(
                    "input_json_delta 缺少 content_block_start 映射，index=%r", index
                )
            return StreamChunk(
                type="tool_use_delta",
                tool_use_id=tool_use_id,
                partial_json=delta.get("partial_json", ""),
                block_index=index,
            )
        logger.debug("忽略未知 content_block_delta 类型：%r", dtype)
        return None

    @staticmethod
    def _map_message_delta(
        payload: dict, state: _AnthropicStreamState
    ) -> tuple[StreamChunk, StreamChunk]:
        """message_delta → (usage 事件, stop 事件)。

        Anthropic usage 字段映射：input_tokens → prompt_tokens、
        output_tokens → completion_tokens；stop_reason 原样透传。
        """
        usage_data = payload.get("usage") or {}
        output_tokens = int(usage_data.get("output_tokens", 0) or 0)
        usage = Usage(
            prompt_tokens=state.input_tokens,
            completion_tokens=output_tokens,
            total_tokens=state.input_tokens + output_tokens,
            cache_creation_input_tokens=state.cache_creation_input_tokens,
            cache_read_input_tokens=state.cache_read_input_tokens,
        )
        stop_reason = (payload.get("delta") or {}).get("stop_reason")
        return (
            StreamChunk(type="usage", usage=usage),
            StreamChunk(type="stop", stop_reason=stop_reason),
        )

    def _build_request(self, messages: list[Message], **kwargs: Any) -> dict:
        """内部模型 → Anthropic wire 格式（请求体）。"""
        system_parts = [
            self._system_text(m.content) for m in messages if m.role == "system"
        ]
        body: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "messages": self._merge_tool_result_messages(
                [self._to_wire_message(m) for m in messages if m.role != "system"]
            ),
        }
        if system_parts:
            # system 以**块数组**发送（而非单个字符串）：Anthropic 的
            # cache_control 断点只能挂在块上，多条 system 先按既有 "\n\n"
            # 语义合并为单块，断点打在末块——保证 system 前缀逐字节稳定，
            # 多轮对话才能命中 prompt cache。
            system_block: dict[str, Any] = {
                "type": "text",
                "text": "\n\n".join(system_parts),
            }
            if self.prompt_cache:
                system_block["cache_control"] = {"type": "ephemeral"}
            body["system"] = [system_block]

        # tools / tool_choice：工具协议 wire 规范化（provider 拥有 wire 格式，
        # agent/场景层保持 provider 无关）。
        # - tools 元素含 input_schema → 原样透传；含 parameters（内部 flat 形态）
        #   → 转换为 {"name", "description", "input_schema": parameters}；其余字段
        #   （如 cache_control）一并无损保留。
        # - tool_choice 字符串 → {"type":"tool","name":<str>}；dict → 透传；
        #   None → 不发送该键。
        tools = kwargs.get("tools")
        if tools is not None:
            body["tools"] = [self._normalize_anthropic_tool(t) for t in tools]
        tool_choice = kwargs.get("tool_choice")
        if tool_choice is not None:
            normalized_tc = self._normalize_anthropic_tool_choice(tool_choice)
            if (
                self._force_tool_choice_degraded
                and isinstance(normalized_tc, dict)
                and normalized_tc.get("type") in ("tool", "any")
            ):
                # 端点级降级（client 依据服务端拒绝置位）：该端点不支持强制
                # 工具选择（如 thinking 模式约束），改为 auto。
                logger.warning(
                    "已按端点约束将强制 tool_choice 降级为 auto"
                    "（该端点不支持强制工具选择）"
                )
                normalized_tc = {"type": "auto"}
            body["tool_choice"] = normalized_tc
        # thinking_level：每任务 kwargs 覆盖 > 全局默认；模型不支持时降级；
        # 端点拒绝过扩展参数时（_extras_disabled）不再发送
        level = kwargs.get("thinking_level", self.thinking_level)
        budget = (
            0
            if self._extras_disabled
            else self._thinking_enabled(level, kwargs.get("model", self.model))
        )
        if budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # Anthropic 约束：budget_tokens 必须小于 max_tokens，且 max_tokens
            # 同时计入思考与输出 token。用户配置的 max_tokens 可能小于 budget
            # （默认 2000 < 三档 budget），自动抬升到 budget + 余量；
            # 用户配置更大时保持用户的值不变。
            body["max_tokens"] = max(
                body["max_tokens"], budget + self._THINKING_HEADROOM
            )
            body["temperature"] = (
                1  # Anthropic 要求 thinking 开启时 temperature 必须为 1
            )
            # thinking 模式不支持强制工具选择（Anthropic/DeepSeek 约束：
            # tool_choice 仅 auto/none 可用）→ 降级 auto 并告警；收尾依赖模型
            # 自行提交（场景侧 output_parser 兜底解析文本建议）。
            forced = body.get("tool_choice")
            if isinstance(forced, dict) and forced.get("type") in ("tool", "any"):
                logger.warning(
                    "thinking 模式不支持强制 tool_choice，已降级为 auto"
                    "（收尾依赖模型自行调用终止工具）"
                )
                body["tool_choice"] = {"type": "auto"}
        return body

    @staticmethod
    def _normalize_anthropic_tool(tool: dict) -> dict:
        """将工具 schema 规范化为 Anthropic wire 形态。

        - 含 input_schema → 原样透传（已是 Anthropic 形态）；
        - 含 parameters（内部 flat 形态）→ 转为 input_schema；
        - 其余字段（cache_control 等）一并无损保留。
        """
        if "input_schema" in tool:
            return tool
        result: dict[str, Any] = {
            "name": tool["name"],
            "description": tool["description"],
        }
        if "parameters" in tool:
            result["input_schema"] = tool["parameters"]
        for k, v in tool.items():
            if k not in ("name", "description", "parameters"):
                result[k] = v
        return result

    @staticmethod
    def _normalize_anthropic_tool_choice(tool_choice: Any) -> Any:
        """字符串 tool_choice → {"type":"tool","name":<str>}；dict → 透传。"""
        if isinstance(tool_choice, str):
            return {"type": "tool", "name": tool_choice}
        return tool_choice

    def _thinking_enabled(self, level: str, model: str) -> int:
        """返回 budget_tokens；模型不支持或 level=off 时返回 0。"""
        # 包含匹配而非前缀：兼容网关透传的带前缀模型名（如 "anthropic/claude-haiku-..."）
        if "claude-haiku" in model:
            logger.warning(f"model {model} 不支持 extended thinking，已降级为 off")
            return 0
        return self._THINKING_BUDGETS.get(level, 0)

    def _system_text(self, content: str | list[ContentBlock]) -> str:
        """提取 system 消息文本：str 直接用，list 取 text block 拼接（其余类型跳过）。

        多块拼接与多条 system 消息的合并（\n\n）保持同一分隔语义，避免
        记忆注入产出多块 system 时与多条 system 消息行为不一致。
        """
        if isinstance(content, str):
            return content
        return "\n\n".join(b.text for b in content if isinstance(b, TextBlock))

    @staticmethod
    def _merge_tool_result_messages(wire_messages: list[dict]) -> list[dict]:
        """合并连续的 tool_result user 消息为单条。

        Anthropic 协议要求 assistant 的全部 ``tool_use`` 由**紧随其后同一条
        消息**中的 ``tool_result`` 一一响应。agent 循环按 provider 无关契约
        逐条追加（assistant tool_use × N → user(tr1) → user(tr2) → ...），
        此处归一化收敛，避免第 2..N 个 tool_use 悬空（真实端点 400：
        "``tool_use`` ids were found without ``tool_result`` blocks
        immediately after"）。纯文本 user 消息（如预算提示）不合并，
        保持原有交替语义。
        """

        def _is_tool_result(msg: dict) -> bool:
            return msg.get("role") == "user" and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in (msg.get("content") or [])
            )

        merged: list[dict] = []
        for msg in wire_messages:
            if _is_tool_result(msg) and merged and _is_tool_result(merged[-1]):
                merged[-1]["content"] = list(merged[-1].get("content") or []) + list(
                    msg.get("content") or []
                )
                continue
            merged.append(msg)
        return merged

    def _to_wire_message(self, m: Message) -> dict:
        """内部消息 → Anthropic wire 消息（content 统一为 blocks 数组）。"""
        if isinstance(m.content, str):
            blocks = [{"type": "text", "text": m.content}]
        else:
            blocks = [self._to_wire_block(block) for block in m.content]
        return {"role": m.role, "content": blocks}

    def _to_wire_block(self, block: ContentBlock) -> dict:
        """内部 content block → Anthropic wire content block。

        工具协议：
        - ToolUseBlock → {"type": "tool_use", "id", "name", "input"}（assistant 消息，1:1）
        - ToolResultBlock → {"type": "tool_result", "tool_use_id", "content", "is_error"}（user 消息）
        其余类型沿用 model_dump（exclude_none）保持向后行为一致。block 若携带
        cache_control 等透传字段，model_dump 已含则一并透传。
        """
        if isinstance(block, ToolUseBlock):
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
        if isinstance(block, ToolResultBlock):
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.content,
                "is_error": block.is_error,
            }
        return block.model_dump(exclude_none=True)
