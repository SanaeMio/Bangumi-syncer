"""OpenAI-compatible API provider (Task 1.3).

Provides an implementation of BaseProvider that communicates with any
OpenAI-compatible chat completions endpoint via httpx.
"""

from typing import Any, Optional

import httpx

from app.services.llm.models import ChatResponse, Message, Usage
from app.services.llm.providers.base import BaseProvider


class OpenAICompatProvider(BaseProvider):
    """LLM provider for OpenAI-compatible chat completion APIs.

    Communicates with any endpoint that follows the OpenAI
    /v1/chat/completions API contract.

    Attributes:
        api_base: The base URL of the API (e.g. https://api.openai.com/v1).
        api_key: Bearer token for authentication.
        model: Default model name to use.
        max_tokens: Default maximum tokens for completion.
        temperature: Default sampling temperature.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        timeout: int = 60,
    ) -> None:
        """Initialize the OpenAI-compatible provider.

        Args:
            api_base: Base URL for the API endpoint.
            api_key: API key for bearer token authentication.
            model: Model name to use for completions.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0-2.0).
            timeout: HTTP request timeout in seconds.
        """
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """Send a chat completion request to the API.

        Args:
            messages: List of conversation messages.
            **kwargs: Override default model, max_tokens, or temperature.

        Returns:
            ChatResponse with the assistant's content and optional usage.

        Raises:
            httpx.HTTPStatusError: On HTTP error responses.
            httpx.TimeoutException: On request timeout.
            ValueError: On JSON decode failures.
        """
        url = f"{self.api_base}/chat/completions"

        body = {
            "model": kwargs.get("model", self.model),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
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
