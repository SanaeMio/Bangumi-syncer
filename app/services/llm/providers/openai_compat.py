"""OpenAI 兼容 API provider。

基于 httpx 实现的 BaseProvider，与任何遵循 OpenAI /v1/chat/completions
API 规范的端点通信。

内部中立模型 → OpenAI wire 格式的差异收敛在 _build_request /
_parse_response 两个方法内（与 AnthropicProvider 结构对称）：
- content 为 list[ContentBlock] 时取 text block 拼接（OpenAI wire 为字符串；
  thinking/tool 等 block 不适用于当前端点）
- thinking_level → reasoning_effort（仅 o 系列模型生效，其余忽略并告警）
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logging import logger
from app.services.llm.models import (
    ChatResponse,
    Message,
    TextBlock,
    ThinkingLevel,
    Usage,
)
from app.services.llm.providers.base import BaseProvider
from app.utils.http_client import create_async_client


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

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """向 API 发送聊天补全请求。

        Args:
            messages: 对话消息列表。
            **kwargs: 覆盖默认的 model、max_tokens 或 temperature。

        Returns:
            包含助手回复内容和可选用量的 ChatResponse。

        Raises:
            httpx.HTTPStatusError: HTTP 错误响应。
            httpx.TimeoutException: 请求超时。
            ValueError: JSON 解码失败。
        """
        url = f"{self.api_base}/chat/completions"
        model = kwargs.get("model", self.model)
        proxy_label = f", proxy={self.proxy}" if self.proxy else ""
        logger.debug(
            f"LLM request: url={url}, model={model}, "
            f"timeout={self.timeout}s{proxy_label}"
        )

        body = self._build_request(messages, **kwargs)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
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
        """内部模型 → OpenAI wire 格式（请求体）。"""
        body: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": [self._to_wire_message(m) for m in messages],
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }

        # thinking_level：每任务 kwargs 覆盖 > 全局默认；非 o 系列模型忽略并告警；
        # 端点拒绝过扩展参数时（_extras_disabled）不再发送
        level = kwargs.get("thinking_level", self.thinking_level)
        effort = (
            None
            if self._extras_disabled
            else self._reasoning_effort(level, body["model"])
        )
        if effort is not None:
            body["reasoning_effort"] = effort
            # H3：o 系列推理模型拒绝非 1 的 temperature（硬 400），
            # 与 Anthropic thinking 开启时的处理对齐（anthropic.py 强制 1）
            body["temperature"] = 1
        return body

    def _to_wire_message(self, m: Message) -> dict:
        """内部消息 → OpenAI wire 消息（content 为字符串）。

        content 为 list[ContentBlock] 时取 text block 拼接（与 Anthropic 侧
        _system_text 同一分隔语义）；thinking/tool 等 block 不适用于当前端点，
        跳过（正式工具协议见 Phase 4）。
        """
        if isinstance(m.content, str):
            return {"role": m.role, "content": m.content}
        parts = [b.text for b in m.content if isinstance(b, TextBlock)]
        skipped = len(m.content) - len(parts)
        if skipped:
            logger.debug(
                f"OpenAI wire 不支持 {skipped} 个非 text content block，已跳过"
            )
        return {"role": m.role, "content": "\n\n".join(parts)}

    def _reasoning_effort(self, level: str, model: str) -> str | None:
        """thinking_level → reasoning_effort；off/不支持时返回 None（不传字段）。"""
        if level == "off":
            return None
        # reasoning_effort 仅 o 系列模型支持（o1/o3/o4-mini 等）。用 ^o\d 而非
        # startswith("o")：避免 "ollama/…"、"openrouter/…" 等第三方网关模型名
        # 误中导致向不支持的端点发送未知参数。OpenAI 对未知参数的行为因 API 版本
        # 而异，不冒险传给非 o 系列。
        if not re.match(r"^o\d", model):
            # L6：配置/模型不匹配是静态事实，warning 刷屏无益——降为 debug
            # （用户可通过 stats/日志在调优期定位）
            logger.debug(
                f"model {model} 非 o 系列不支持 reasoning_effort，"
                f"已忽略 thinking_level={level}"
            )
            return None
        return self._REASONING_EFFORT.get(level)

    def _parse_response(self, data: dict) -> ChatResponse:
        """OpenAI wire 格式 → 内部模型。"""
        choice = data["choices"][0]
        message = choice.get("message", {})
        content = message.get("content")
        refusal = message.get("refusal")
        # M10：finish_reason → stop_reason（与 Anthropic 对齐，P4 判断 max_tokens 截断用）
        stop_reason = choice.get("finish_reason") or ""

        if content is None:
            if refusal:
                raise ValueError(f"模型拒绝响应: {refusal}")
            content = ""
        model = data.get("model", "")

        usage: Usage | None = None
        if "usage" in data:
            u = data["usage"]
            usage = Usage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
            )

        return ChatResponse(
            content=content, model=model, usage=usage, stop_reason=stop_reason
        )
