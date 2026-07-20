"""LLM client with retry logic and usage logging (Task 1.4).

Provides LLMClient -- a wrapper around the OpenAI-compatible provider
that adds automatic retries with backoff and cost logging to the
database.  Also exports a module-level singleton via get_llm_client().
"""

from __future__ import annotations

import asyncio
import time

from app.core.config import config_manager
from app.core.logging import logger

from .models import ChatResponse, Message
from .providers.openai_compat import OpenAICompatProvider


class LLMClient:
    """Singleton LLM client with retry logic and usage logging.

    Wraps an OpenAICompatProvider and adds:
    - Retry with exponential-like backoff (1 s, 3 s)
    - Fire-and-forget usage logging to the database
    - Structured logger output for each call
    """

    MAX_RETRIES = 2
    RETRY_BACKOFF: list[int] = [1, 3]  # seconds

    def __init__(self) -> None:
        cfg = config_manager.get_llm_config()
        self._provider = OpenAICompatProvider(
            api_base=cfg["api_base"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            temperature=cfg["temperature"],
            timeout=cfg["timeout"],
        )

    async def chat(  # noqa: PLR0913
        self,
        messages: list[Message],
        *,
        job_id: int | None = None,
        job_name: str | None = None,
        **kwargs,
    ) -> ChatResponse:
        """Send chat request with retry logic.  Logs usage to database.

        Args:
            messages: The conversation messages.
            job_id: Optional job identifier for usage tracking.
            job_name: Optional job name for usage tracking.
            **kwargs: Provider-specific overrides (temperature, max_tokens, etc.).

        Returns:
            A ChatResponse on success, or an empty ChatResponse if all
            retries are exhausted.
        """
        last_error: Exception | None = None
        t_start = time.time()

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = await self._provider.chat(messages, **kwargs)
                latency_ms = int((time.time() - t_start) * 1000)
                self._log_usage(
                    job_id=job_id,
                    job_name=job_name,
                    model=response.model,
                    prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                    completion_tokens=response.usage.completion_tokens
                    if response.usage
                    else 0,
                    total_tokens=response.usage.total_tokens if response.usage else 0,
                    latency_ms=latency_ms,
                    status="success",
                )
                logger.info(
                    f"LLM call: model={response.model} "
                    f"tokens={response.usage.total_tokens if response.usage else 0} "
                    f"latency={latency_ms}ms"
                )
                return response
            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES:
                    delay = self.RETRY_BACKOFF[attempt]
                    logger.warning(
                        f"LLM retry {attempt + 1}/{self.MAX_RETRIES} "
                        f"after {delay}s: {e}"
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted -- log error and return empty response.
        latency_ms = int((time.time() - t_start) * 1000)
        error_msg = str(last_error)
        logger.error(f"LLM call failed after {self.MAX_RETRIES} retries: {error_msg}")
        self._log_usage(
            job_id=job_id,
            job_name=job_name,
            model=config_manager.get_llm_config()["model"],
            latency_ms=latency_ms,
            status="error",
            error_message=error_msg,
        )
        return ChatResponse(content="", model="", usage=None)

    def _log_usage(  # noqa: PLR0913
        self,
        job_id: int | None = None,
        job_name: str | None = None,
        # review 这里应该传入 Response 而不是 response 的内部成员变量，除非是调用失败，才传入配置的 model。逻辑封装一下
        # review 或者在上层封装为 success 和 error 两种调用方式
        model: str = "",
        provider: str = "openai_compat",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
        status: str = "success",
        error_message: str | None = None,
    ) -> None:
        """Log usage to database (fire-and-forget, best-effort).

        This method never raises -- any failure is caught and logged.
        """
        try:
            from app.core.database import database_manager

            database_manager.llm_usage.log_usage(
                job_id=job_id,
                job_name=job_name or "",
                model=model,
                provider=provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                status=status,
                error_message=error_message,
            )
        except Exception as e:
            logger.error(f"Failed to log LLM usage: {e}")


# Module-level singleton ------------------------------------------------


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return the module-level LLMClient singleton, creating it on first call."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
