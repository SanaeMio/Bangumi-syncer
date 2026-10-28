"""OpenAI Responses API provider。

基于 httpx 实现的 BaseProvider，与遵循 OpenAI /v1/responses API 规范的端点通信。

Responses API 与 /v1/chat/completions 差异较大，全部收敛在
_build_request / stream 内：

- system 消息抽为顶层 ``instructions``（多条用 ``\\n\\n`` 连接）
- 对话历史为扁平 ``input`` 数组：普通消息 ``{role, content}``；工具调用与结果
  为顶层项 ``{type: function_call}`` / ``{type: function_call_output}``
  （不再有 chat completions 的 ``tool_calls`` / ``role=tool`` 概念）
- 工具 schema 为扁平形态（无 ``function`` 包裹）
- reasoning 为 ``{effort: ...}``；思考摘要走 reasoning summary 事件
- 认证沿用 ``Authorization: Bearer <key>``
"""

from __future__ import annotations

import json
import re
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
from app.services.llm.providers.base import BaseProvider, open_stream_response
from app.services.llm.sse import SSEEvent, iter_sse_events


@dataclass
class _ResponsesStreamState:
    """Responses 流式的跨事件状态（模块私有）。

    - ``item_call_ids``：item.id → call_id 映射（function_call_arguments.delta
      只带 item_id）；
    - ``stopped``：已补发 tool_use_stop 的 call_id，避免重复补发；
    - ``model_sent``：真实模型名是否已附到首个产出事件上。
    """

    item_call_ids: dict[str, str] = field(default_factory=dict)
    stopped: set[str] = field(default_factory=set)
    model_sent: bool = False


class OpenAIResponsesProvider(BaseProvider):
    """OpenAI Responses API 的 LLM provider。

    与任何遵循 OpenAI /v1/responses API 规范的端点通信。

    Attributes:
        api_base: API 的基础 URL（如 https://api.openai.com/v1）。
        api_key: 用于认证的 Bearer token。
        model: 默认使用的模型名称。
        max_tokens: 补全的默认最大输出 token 数（wire 字段 max_output_tokens）。
        temperature: 默认采样温度（thinking 开启时被强制为 1）。
        timeout: 请求超时时间（秒）。
        proxy: 可选的 HTTP 代理 URL。
        thinking_level: 思考强度 off/low/medium/high（映射 reasoning.effort，
            仅 o 系列模型生效；每任务 kwargs 可覆盖）。
    """

    # thinking_level → Responses reasoning.effort 映射（off 不传）
    _REASONING_EFFORT: dict[str, str] = {
        "low": "low",
        "medium": "medium",
        "high": "high",
    }

    # tool_choice 字面量（其余字符串视为工具名 → {"type":"function","name":...}）
    _TOOL_CHOICE_LITERALS = frozenset({"none", "auto", "required"})

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
        """初始化 OpenAI Responses provider。

        Args:
            api_base: API 端点的基础 URL。
            api_key: 用于 Bearer token 认证的 API key。
            model: 补全使用的模型名称。
            max_tokens: 最大输出 token 数（wire 字段 max_output_tokens）。
            temperature: 采样温度 (0.0-2.0)。
            timeout: HTTP 请求超时时间（秒）。
            proxy: 可选的 HTTP 代理 URL。
            thinking_level: 思考强度（reasoning.effort 映射，仅 o 系列）。
        """
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.proxy = proxy
        self.thinking_level = thinking_level

    @property
    def _url(self) -> str:
        """Responses 端点 URL。"""
        return f"{self.api_base}/responses"

    def _headers(self) -> dict[str, str]:
        """认证与内容类型请求头。"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_request(self, messages: list[Message], **kwargs: Any) -> dict:
        """内部模型 → Responses wire 格式（请求体）。"""
        body: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "max_output_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }

        # system 消息 → 顶层 instructions（多条 \n\n 连接）
        system_parts = [
            self._text_of(m.content) for m in messages if m.role == "system"
        ]
        if system_parts:
            body["instructions"] = "\n\n".join(system_parts)

        # 其余消息 → 扁平 input 项数组
        body["input"] = [
            item
            for m in messages
            if m.role != "system"
            for item in self._to_input_items(m)
        ]

        # reasoning.effort：每任务 kwargs 覆盖 > 全局默认；非 o 系列模型忽略；
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
            body["reasoning"] = {"effort": effort}
            # 推理模型拒绝非 1 的 temperature（硬 400），与 openai_compat 对齐
            body["temperature"] = 1

        # tools / tool_choice / reasoning 同属扩展参数：本次调用被拒后整体降级不发
        if extras_disabled:
            return body

        if "tools" in kwargs:
            tools = kwargs["tools"]
            if tools is not None:
                body["tools"] = [self._normalize_tool(t) for t in tools]
        if "tool_choice" in kwargs:
            tc = kwargs["tool_choice"]
            if tc is not None:
                normalized_tc = self._normalize_tool_choice(tc)
                if (
                    force_tool_choice_degraded
                    and isinstance(normalized_tc, dict)
                    and normalized_tc.get("type") == "function"
                ):
                    # 端点级降级（client 依据服务端拒绝置位）：该端点不支持
                    # 强制工具选择，改为 auto。
                    logger.warning(
                        "已按端点约束将强制 tool_choice 降级为 auto"
                        "（该端点不支持强制工具选择）"
                    )
                    normalized_tc = "auto"
                body["tool_choice"] = normalized_tc

        return body

    @staticmethod
    def _text_of(content: str | list[ContentBlock]) -> str:
        """提取消息文本：str 直接用，list 取 text block 拼接（其余跳过）。"""
        if isinstance(content, str):
            return content
        return "\n\n".join(b.text for b in content if isinstance(b, TextBlock))

    def _to_input_items(self, m: Message) -> list[dict]:
        """单条内部消息 → Responses input 项（可拆为多项）。

        - 文本（str 或 TextBlock）→ {role, content} 消息项
        - assistant 的 ToolUseBlock → {type: function_call, call_id, name, arguments}
        - ToolResultBlock → {type: function_call_output, call_id, output}
        - ThinkingBlock：忽略（Responses 多轮不要求回传 reasoning）
        文本与工具项按块出现顺序输出（连续文本块聚合为单项）。
        """
        if isinstance(m.content, str):
            return [{"role": m.role, "content": m.content}]

        items: list[dict] = []
        text_parts: list[str] = []

        def _flush_text() -> None:
            if text_parts:
                # 分隔符与 ``openai_compat`` 同口径按 role 分层：
                # - system：多块本就代表多条独立规则，\n\n 保留其边界
                #   （与 ``_text_of`` / Anthropic ``_system_text`` 一致）；
                # - assistant / user：连续文本块是同一次发言被 wire 切开的结果
                #   （``StreamAggregator`` 亦以 "" 聚合为单块），注入 \n\n 会把
                #   "AB" 改写成 "A\n\nB"，故空串连接。
                # 刻意**不**拆成多条 message item：模型从未在此处发出第二条消息，
                # 凭空造出的消息边界同样是不忠实。
                separator = "\n\n" if m.role == "system" else ""
                items.append({"role": m.role, "content": separator.join(text_parts)})
                text_parts.clear()

        for block in m.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                _flush_text()
                items.append(
                    {
                        "type": "function_call",
                        "call_id": block.id,
                        "name": block.name,
                        # arguments 必须为 JSON 字符串（Responses wire 约定）
                        "arguments": json.dumps(block.input, ensure_ascii=False),
                    }
                )
            elif isinstance(block, ToolResultBlock):
                _flush_text()
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.tool_use_id,
                        "output": block.content,
                    }
                )
            else:
                # ThinkingBlock 等：Responses 多轮不要求回传，忽略并记录
                logger.debug(
                    f"Responses input 忽略 content block 类型 {type(block).__name__}"
                )
        _flush_text()
        return items

    @staticmethod
    def _normalize_tool(tool: dict) -> dict:
        """将工具 schema 规范化为 Responses 扁平形态。

        - 已是扁平形态（name/description/parameters）→ 直接取用；
        - OpenAI 包裹形态 {"type":"function","function":{...}} → 解包为扁平。
        """
        fn = tool.get("function")
        if isinstance(fn, dict):
            return {
                "type": "function",
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        return {
            "type": "function",
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {}),
        }

    @classmethod
    def _normalize_tool_choice(cls, tool_choice: Any) -> Any:
        """规范化 tool_choice：

        - "none"/"auto"/"required" 原样透传；
        - 其它字符串（工具名）→ {"type":"function","name":<str>}；
        - 含 OpenAI 包裹形态的 dict → 解包为 {"type":"function","name":<str>}；
        - 其余 dict 透传。
        """
        if isinstance(tool_choice, str):
            if tool_choice in cls._TOOL_CHOICE_LITERALS:
                return tool_choice
            return {"type": "function", "name": tool_choice}
        if (
            isinstance(tool_choice, dict)
            and "name" not in tool_choice
            and isinstance(tool_choice.get("function"), dict)
        ):
            return {
                "type": "function",
                "name": tool_choice["function"].get("name", ""),
            }
        return tool_choice

    def _reasoning_effort(self, level: str, model: str) -> str | None:
        """thinking_level → reasoning.effort；off/不支持时返回 None（不传字段）。

        与 openai_compat 保持一致：用 ^o\\d 而非 startswith("o")，避免
        "ollama/…"、"openrouter/…" 等第三方网关模型名误中。
        """
        if level == "off":
            return None
        if not re.match(r"^o\d", model):
            logger.debug(
                f"model {model} 非 o 系列不支持 reasoning.effort，"
                f"已忽略 thinking_level={level}"
            )
            return None
        return self._REASONING_EFFORT.get(level)

    @staticmethod
    def _usage_from(usage: dict | None) -> Usage | None:
        """Responses usage → 内部 Usage（input/output/total_tokens）。"""
        if not usage:
            return None
        prompt = usage.get("input_tokens", 0)
        completion = usage.get("output_tokens", 0)
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=usage.get("total_tokens", prompt + completion),
        )

    async def stream(
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:
        """流式调用（Responses SSE 增量事件）。

        Args:
            messages: 对话消息列表。
            **kwargs: provider 特定的额外参数。

        Yields:
            provider 无关的归一化流式事件 StreamChunk。

        Raises:
            httpx.HTTPStatusError: HTTP 错误响应。
            ValueError: 收到 response.failed / error 事件。
        """
        url = self._url
        body = self._build_request(messages, **kwargs)
        body["stream"] = True

        # 跨事件状态：item.id → call_id 映射 + 已补发停点集合 + 模型名是否已附
        state = _ResponsesStreamState()

        def _attach_model(chunk: StreamChunk, response_data: dict) -> StreamChunk:
            if not state.model_sent:
                model = response_data.get("model") or ""
                if model:
                    chunk.model = model
                    state.model_sent = True
            return chunk

        async with open_stream_response(
            url=url,
            body=body,
            headers=self._headers(),
            timeout=self.timeout,
            proxy=self.proxy,
        ) as response:
            async for sse in iter_sse_events(response.aiter_lines()):
                if sse.data == "[DONE]":
                    # OpenAI Responses 正常以 response.completed 结束，
                    # [DONE] 仅作宽容兜底
                    return
                payload = self._decode_payload(sse)
                if payload is None:
                    continue
                # 事件名在 event: 字段或 data JSON 的 type 字段，两者兼容
                name = sse.event or payload.get("type")
                response_data = payload.get("response") or {}
                if name == "response.output_item.added":
                    item = payload.get("item") or {}
                    if item.get("type") == "function_call":
                        state.item_call_ids[item.get("id", "")] = item.get(
                            "call_id", ""
                        )
                        yield _attach_model(
                            StreamChunk(
                                type="tool_use_start",
                                tool_use_id=item.get("call_id", ""),
                                tool_name=item.get("name", ""),
                            ),
                            response_data,
                        )
                    else:
                        logger.debug(
                            f"Responses output_item.added 忽略 item 类型 "
                            f"{item.get('type')!r}"
                        )
                elif name == "response.output_text.delta":
                    yield _attach_model(
                        StreamChunk(type="text_delta", text=payload.get("delta", "")),
                        response_data,
                    )
                elif name == "response.function_call_arguments.delta":
                    item_id = payload.get("item_id", "")
                    tool_use_id = payload.get("call_id") or state.item_call_ids.get(
                        item_id, item_id
                    )
                    yield _attach_model(
                        StreamChunk(
                            type="tool_use_delta",
                            tool_use_id=tool_use_id,
                            partial_json=payload.get("delta", ""),
                        ),
                        response_data,
                    )
                elif name == "response.reasoning_summary_text.delta":
                    yield _attach_model(
                        StreamChunk(
                            type="thinking_delta", thinking=payload.get("delta", "")
                        ),
                        response_data,
                    )
                elif name == "response.output_item.done":
                    # function_call 项完成 → 参数已完整、可校验：停点事件
                    item = payload.get("item") or {}
                    if item.get("type") == "function_call":
                        call_id = item.get("call_id", "")
                        if call_id not in state.stopped:
                            state.stopped.add(call_id)
                            yield _attach_model(
                                StreamChunk(type="tool_use_stop", tool_use_id=call_id),
                                response_data,
                            )
                    else:
                        logger.debug(
                            f"Responses output_item.done 忽略 item 类型 "
                            f"{item.get('type')!r}"
                        )
                elif name in ("response.completed", "response.incomplete"):
                    # incomplete（截断）与 completed 同路径产出 usage + stop，
                    # stop_reason 由 _stop_reason_of 依 status 判定
                    usage = self._usage_from(response_data.get("usage"))
                    if usage is not None:
                        yield _attach_model(
                            StreamChunk(type="usage", usage=usage), response_data
                        )
                    yield _attach_model(
                        StreamChunk(
                            type="stop",
                            stop_reason=self._stop_reason_of(response_data),
                        ),
                        response_data,
                    )
                elif name in ("response.failed", "error"):
                    raise ValueError(self._error_message(name, payload))
                else:
                    logger.debug(f"Responses SSE 忽略事件 {name!r}")

    @staticmethod
    def _decode_payload(sse: SSEEvent) -> dict | None:
        """解析 SSE data 为 JSON dict；空/非 JSON 返回 None（宽容跳过）。"""
        if not sse.data:
            return None
        try:
            parsed = json.loads(sse.data)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Responses SSE data 非 JSON，忽略：{sse.data!r}")
            return None
        if not isinstance(parsed, dict):
            logger.warning(f"Responses SSE data 非 JSON 对象，忽略：{sse.data!r}")
            return None
        return parsed

    @staticmethod
    def _stop_reason_of(response_data: dict) -> str:
        """从 response.completed / response.incomplete 的 response 对象推导 stop_reason。

        优先级：有 function_call → ``tool_use``；``status == "incomplete"``（截断）
        → ``max_tokens``；否则 ``end_turn``。
        """
        has_function_call = any(
            item.get("type") == "function_call"
            for item in (response_data.get("output") or [])
        )
        if has_function_call:
            return "tool_use"
        if response_data.get("status") == "incomplete":
            return "max_tokens"
        return "end_turn"

    @staticmethod
    def _error_message(name: str, payload: dict) -> str:
        """从失败事件提取可读错误信息。"""
        if name == "response.failed":
            err = (
                (payload.get("response") or {}).get("error")
                or payload.get("error")
                or payload
            )
        else:
            err = payload.get("message") or payload.get("error") or payload
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err)
