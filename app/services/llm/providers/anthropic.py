"""Anthropic Messages API provider。

基于 httpx 实现的 BaseProvider，与任何遵循 Anthropic /v1/messages
API 规范的端点通信（官方 API 或兼容代理/网关）。

内部中立模型 → Anthropic wire 格式的差异收敛在 _build_request /
_parse_response 两个方法内：
- system prompt 抽为顶层参数（多条用 \\n\\n 连接）
- content 统一为 content blocks 数组
- thinking_level 映射为 thinking.budget_tokens（模型不支持时降级）
"""

from __future__ import annotations

from typing import Any

from app.core.logging import logger
from app.services.llm.models import (
    ChatResponse,
    ContentBlock,
    Message,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ThinkingLevel,
    Usage,
)
from app.services.llm.providers.base import BaseProvider
from app.utils.http_client import create_async_client


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API 的 LLM provider。

    与任何遵循 Anthropic /v1/messages API 规范的端点通信。

    Attributes:
        api_base: API 的基础 URL（如 https://api.anthropic.com/v1）。
        api_key: 用于 Bearer token 认证的 API key。
        model: 默认使用的模型名称。
        max_tokens: 补全的默认最大 token 数（Anthropic API 必填）。
        temperature: 默认采样温度（thinking 开启时被强制为 1）。
        timeout: 请求超时时间（秒）。
        proxy: 可选的 HTTP 代理 URL。
        thinking_level: 思考强度 off/low/medium/high（每任务 kwargs 可覆盖）。
    """

    # thinking_level → Anthropic budget_tokens 映射
    # 键为 str：_thinking_enabled 需对无效值兜底为 0（ThinkingLevel 约束在构造参数）
    _THINKING_BUDGETS: dict[str, int] = {
        "off": 0,
        "low": 2048,
        "medium": 4096,
        "high": 8192,
    }

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

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """向 API 发送聊天补全请求。

        Args:
            messages: 对话消息列表。
            **kwargs: 覆盖默认的 model、max_tokens、temperature 或 thinking_level。

        Returns:
            包含助手回复内容和可选用量的 ChatResponse。

        Raises:
            httpx.HTTPStatusError: HTTP 错误响应。
            httpx.TimeoutException: 请求超时。
        """
        url = f"{self.api_base}/messages"
        model = kwargs.get("model", self.model)
        proxy_label = f", proxy={self.proxy}" if self.proxy else ""
        logger.info(
            f"LLM request: url={url}, model={model}, "
            f"timeout={self.timeout}s{proxy_label}"
        )

        body = self._build_request(messages, **kwargs)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        async with create_async_client(
            proxy=self.proxy,
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            response = await client.post(
                url,
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

        return self._parse_response(data)

    def _build_request(self, messages: list[Message], **kwargs: Any) -> dict:
        """内部模型 → Anthropic wire 格式（请求体）。"""
        system_parts = [
            self._system_text(m.content) for m in messages if m.role == "system"
        ]
        body: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "messages": [
                self._to_wire_message(m) for m in messages if m.role != "system"
            ],
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        # thinking_level：每任务 kwargs 覆盖 > 全局默认；模型不支持时降级
        level = kwargs.get("thinking_level", self.thinking_level)
        budget = self._thinking_enabled(level, kwargs.get("model", self.model))
        if budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["temperature"] = (
                1  # Anthropic 要求 thinking 开启时 temperature 必须为 1
            )
        return body

    def _thinking_enabled(self, level: str, model: str) -> int:
        """返回 budget_tokens；模型不支持或 level=off 时返回 0。"""
        if model.startswith("claude-haiku"):
            logger.warning(f"model {model} 不支持 extended thinking，已降级为 off")
            return 0
        return self._THINKING_BUDGETS.get(level, 0)

    def _system_text(self, content: str | list[ContentBlock]) -> str:
        """提取 system 消息文本：str 直接用，list 取 text block 拼接（其余类型跳过）。"""
        if isinstance(content, str):
            return content
        return "".join(b.text for b in content if isinstance(b, TextBlock))

    def _to_wire_message(self, m: Message) -> dict:
        """内部消息 → Anthropic wire 消息（content 统一为 blocks 数组）。"""
        if isinstance(m.content, str):
            blocks = [{"type": "text", "text": m.content}]
        else:
            blocks = [block.model_dump(exclude_none=True) for block in m.content]
        return {"role": m.role, "content": blocks}

    def _parse_response(self, data: dict) -> ChatResponse:
        """Anthropic wire 格式 → 内部模型。"""
        blocks: list[ContentBlock] = []
        text_parts: list[str] = []
        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "")
                blocks.append(TextBlock(text=text))
                text_parts.append(text)
            elif btype == "thinking":
                blocks.append(
                    ThinkingBlock(
                        thinking=block.get("thinking", ""),
                        signature=block.get("signature"),
                    )
                )
            elif btype == "redacted_thinking":
                blocks.append(RedactedThinkingBlock(data=block.get("data", "")))
            else:
                # 未知 block 类型（如 tool_use）：跳过 + warning，不崩溃
                # （正式解析在 Phase 2.1）
                logger.warning(f"未知 content block 类型 {btype!r}，已跳过")

        # Anthropic usage 字段映射：input_tokens → prompt_tokens,
        # output_tokens → completion_tokens
        usage: Usage | None = None
        if "usage" in data:
            u = data["usage"]
            prompt = u.get("input_tokens", 0)
            completion = u.get("output_tokens", 0)
            usage = Usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            )

        return ChatResponse(
            content="".join(text_parts),
            blocks=blocks,
            stop_reason=data.get("stop_reason", ""),
            model=data.get("model", ""),
            usage=usage,
        )
