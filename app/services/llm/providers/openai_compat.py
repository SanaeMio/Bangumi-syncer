"""OpenAI 兼容 API provider。

基于 httpx 实现的 BaseProvider，与任何遵循 OpenAI /v1/chat/completions
API 规范的端点通信。
"""

from typing import Any, Optional

from app.core.logging import logger
from app.services.llm.models import ChatResponse, Message, Usage
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
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        timeout: int = 60,
        proxy: Optional[str] = None,
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
        """
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.proxy = proxy

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

        body = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }

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

        choice = data["choices"][0]
        message = choice.get("message", {})
        content = message.get("content")
        refusal = message.get("refusal")

        if content is None:
            if refusal:
                raise ValueError(f"模型拒绝响应: {refusal}")
            content = ""
        model = data.get("model", "")

        usage: Optional[Usage] = None
        if "usage" in data:
            u = data["usage"]
            usage = Usage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
            )

        return ChatResponse(content=content, model=model, usage=usage)
