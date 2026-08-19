"""LLM 客户端（重试逻辑与用量日志记录）。

提供 LLMClient —— 对 OpenAI 兼容 provider 的封装，增加了自动重试（含退避等待）
和用量记录功能。同时通过 get_llm_client() 导出模块级单例。
"""

from __future__ import annotations

import asyncio
import time

from app.core.config import config_manager
from app.core.logging import logger

from .models import ChatResponse, Message
from .providers.anthropic import AnthropicProvider
from .providers.base import BaseProvider
from .providers.openai_compat import OpenAICompatProvider

_PROVIDER_MAP: dict[str, type] = {
    "openai_compat": OpenAICompatProvider,
    "anthropic_compat": AnthropicProvider,
}


def _format_error_detail(e: Exception) -> str:
    """从异常对象提取详细的错误信息，包含异常类型、消息、底层原因和请求 URL。"""
    parts = [f"{type(e).__name__}: {e}"]
    cause = getattr(e, "__cause__", None)
    if cause is not None:
        parts.append(f"[cause: {type(cause).__name__}: {cause}]")
    req = getattr(e, "request", None)
    if req is not None:
        parts.append(f"[url: {req.url}]")
    return " ".join(parts)


def _build_provider(
    provider: str, cfg: dict[str, object], proxy: str | None
) -> BaseProvider:
    cls = _PROVIDER_MAP.get(provider)
    if cls is None:
        supported = ", ".join(_PROVIDER_MAP)
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. Supported: {supported}"
        )
    kwargs: dict[str, object] = {
        "api_base": cfg["api_base"],
        "api_key": cfg["api_key"],
        "model": cfg["model"],
        "max_tokens": cfg["max_tokens"],
        "temperature": cfg["temperature"],
        "timeout": cfg["timeout"],
        "proxy": proxy,
    }
    # thinking_level 只传给 anthropic 分支（openai 分支构造函数无此参数）
    if provider == "anthropic_compat":
        kwargs["thinking_level"] = cfg.get("thinking_level", "off")
    return cls(**kwargs)


class LLMClient:
    """LLM 客户端单例（含重试逻辑与用量日志记录）。

    Provider 选择由 [llm] 配置节中的 ``provider`` 键驱动
    （默认 ``"openai_compat"``）。
    """

    MAX_RETRIES = 2
    RETRY_BACKOFF: list[int] = [1, 3]  # 秒

    def __init__(self) -> None:
        cfg = config_manager.get_llm_config()
        proxy = config_manager.get("dev", "script_proxy", fallback="").strip() or None
        self._provider_name = cfg["provider"]
        self._provider = _build_provider(self._provider_name, cfg, proxy)

    async def chat(  # noqa: PLR0913
        self,
        messages: list[Message],
        *,
        job_id: int | None = None,
        job_name: str | None = None,
        **kwargs,
    ) -> ChatResponse:
        """发送聊天请求（含重试逻辑），记录用量到数据库。

        Args:
            messages: 对话消息列表。
            job_id: 可选的 job 标识符，用于用量追踪。
            job_name: 可选的 job 名称，用于用量追踪。
            **kwargs: provider 特定的覆盖参数（temperature、max_tokens 等）。

        Returns:
            成功时返回 ChatResponse，所有重试耗尽时返回空的 ChatResponse。
        """
        last_error: Exception | None = None
        t_start = time.time()

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = await self._provider.chat(messages, **kwargs)
                latency_ms = int((time.time() - t_start) * 1000)
                response.latency = latency_ms
                self._log_success(response, job_id=job_id, job_name=job_name)
                logger.debug(
                    f"LLM call: model={response.model} "
                    f"tokens={response.usage.total_tokens if response.usage else 0} "
                    f"latency={response.latency}ms"
                )
                return response
            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES:
                    delay = self.RETRY_BACKOFF[attempt]
                    logger.warning(
                        f"LLM retry {attempt + 1}/{self.MAX_RETRIES} "
                        f"after {delay}s: {_format_error_detail(e)}"
                    )
                    await asyncio.sleep(delay)

        # 所有重试耗尽 —— 记录错误并返回空响应
        latency_ms = int((time.time() - t_start) * 1000)
        error_detail = _format_error_detail(last_error) if last_error else "unknown"
        logger.error(
            f"LLM call failed after {self.MAX_RETRIES} retries: {error_detail}"
        )
        self._log_error(
            error_detail,
            job_id=job_id,
            job_name=job_name,
            latency_ms=latency_ms,
        )
        return ChatResponse(content="", model="", usage=None, latency=latency_ms)

    def _log_success(
        self,
        response: ChatResponse,
        job_id: int | None = None,
        job_name: str | None = None,
    ) -> None:
        """记录成功 LLM 调用（从 ChatResponse 提取用量信息）。"""
        try:
            from app.core.database import database_manager

            usage = response.usage
            database_manager.llm_usage.log_usage(
                job_id=job_id,
                job_name=job_name or "",
                model=response.model,
                provider=self._provider_name,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                latency_ms=response.latency,
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
                provider=self._provider_name,
                latency_ms=latency_ms,
                status="error",
                error_message=error_message,
            )
        except Exception as e:
            logger.error(f"Failed to log LLM usage: {e}")


# 模块级单例 ----------------------------------------------------------------


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """返回模块级 LLMClient 单例，首次调用时创建。"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client() -> None:
    """重置 LLM 单例，使下次调用 get_llm_client 时用最新配置重建。"""
    global _llm_client
    _llm_client = None
