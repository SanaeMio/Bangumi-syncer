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
        proxy = config_manager.get("dev", "script_proxy", fallback="").strip() or None
        self._provider = OpenAICompatProvider(
            api_base=cfg["api_base"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            temperature=cfg["temperature"],
            timeout=cfg["timeout"],
            proxy=proxy,
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
                self._log_success(
                    response,
                    job_id=job_id,
                    job_name=job_name,
                    latency_ms=latency_ms,
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
        self._log_error(
            error_msg,
            job_id=job_id,
            job_name=job_name,
            latency_ms=latency_ms,
        )
        return ChatResponse(content="", model="", usage=None)

    def _log_success(
        self,
        response: ChatResponse,
        job_id: int | None = None,
        job_name: str | None = None,
        latency_ms: int = 0,
    ) -> None:
        """记录成功 LLM 调用（从 ChatResponse 提取用量信息）。"""
        try:
            from app.core.database import database_manager

            usage = response.usage
            database_manager.llm_usage.log_usage(
                job_id=job_id,
                job_name=job_name or "",
                model=response.model,
                provider="openai_compat",
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                latency_ms=latency_ms,
                status="success",
            )
        except Exception as e:
            logger.error(f"Failed to log LLM usage: {e}")

    def _log_error(
        self,
        error_message: str,
        job_id: int | None = None,
        job_name: str | None = None,
        latency_ms: int = 0,
    ) -> None:
        """记录失败 LLM 调用（model 取自配置）。"""
        try:
            from app.core.database import database_manager

            database_manager.llm_usage.log_usage(
                job_id=job_id,
                job_name=job_name or "",
                model=config_manager.get_llm_config()["model"],
                provider="openai_compat",
                latency_ms=latency_ms,
                status="error",
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


def reset_llm_client() -> None:
    """重置 LLM 单例，使下次调用 get_llm_client 时用最新配置重建。"""
    global _llm_client
    _llm_client = None
