"""LLM 客户端（重试逻辑与用量日志记录）。

提供 LLMClient —— 对 OpenAI 兼容 provider 的封装，增加了自动重试（含退避等待）
和用量记录功能。同时通过 get_llm_client() 导出模块级单例。
"""

from __future__ import annotations

import asyncio
import time

import httpx

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


# 端点拒绝扩展参数的响应特征（大小写不敏感子串匹配）。
# 模式串已收窄到具体短语，避免网关无关文案（如 "model does not support streaming"、
# "unrecognized model"）被误判为参数拒绝。
# OpenAI canonical: "Unrecognized request argument supplied: <param>" → "unrecognized request argument"；
# 网关变体: "unrecognized parameter '<param>' is not supported" → "unrecognized parameter"。
# thinking/reasoning 类: "does not support thinking" / "does not support reasoning"。
# 其余（unknown parameter / unexpected keyword / invalid request argument / extra fields not permitted）保持裸短语。
# M8：状态码放行 400/422（pydantic 网关）。
_PARAM_REJECTION_PATTERNS = (
    "unrecognized request argument",
    "unrecognized parameter",
    "unknown parameter",
    "unexpected keyword",
    "invalid request argument",
    "extra fields not permitted",
    "does not support thinking",
    "does not support reasoning",
)

# Anthropic invalid_request_error 覆盖过广（消息格式错误等无关 400 也用该 type），
# 须与扩展参数关键字组合才判定为参数拒绝。
_PARAM_KEYWORDS = ("thinking", "reasoning", "budget_tokens")

_PARAM_REJECTION_STATUSES = (400, 422)


def _is_param_rejection(e: Exception) -> bool:
    """识别"端点不支持某请求参数"类错误（区别于鉴权/格式错误）。"""
    if not isinstance(e, httpx.HTTPStatusError):
        return False
    resp = getattr(e, "response", None)
    if resp is None or resp.status_code not in _PARAM_REJECTION_STATUSES:
        return False
    text = (resp.text or "").lower()
    if any(p in text for p in _PARAM_REJECTION_PATTERNS):
        return True
    # 复合判定：Anthropic invalid_request_error + 扩展参数关键字
    return "invalid_request_error" in text and any(k in text for k in _PARAM_KEYWORDS)


# M7/M9：确定性错误 —— 重试无意义（refusal / 鉴权 / 参数类 / 不存在）
_TERMINAL_STATUSES = (400, 401, 403, 404, 422)


def _is_terminal_error(e: Exception) -> bool:
    """refusal（ValueError）与确定性 4xx 不重试；其余（429/5xx/超时）可重试。"""
    if isinstance(e, ValueError):
        return True  # 解析失败/refusal，重试结果不变
    if isinstance(e, httpx.HTTPStatusError):
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        if status in _TERMINAL_STATUSES:
            # 参数类 400/422 由调用方先走降级路径，此处仅兜底其它确定性 4xx
            return not _is_param_rejection(e)
    return False


def _retry_delay(e: Exception, fallback: int) -> int:
    """429 优先读取 Retry-After（秒）；其余用固定退避。

    顺手项 2：Retry-After 钳制到 60s 上限，避免恶意/异常端点返回超大值导致
    请求长时间挂起（退避本就只用于吸收短暂限流，过长无收益）。
    """
    _RETRY_AFTER_CAP = 60
    if isinstance(e, httpx.HTTPStatusError):
        resp = getattr(e, "response", None)
        if resp is not None and getattr(resp, "status_code", None) == 429:
            ra = resp.headers.get("Retry-After")
            if ra and ra.isdigit():
                return min(int(ra), _RETRY_AFTER_CAP)
    return fallback


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
        # 双 provider 构造函数均接受 thinking_level（openai 侧映射 reasoning_effort）
        "thinking_level": cfg.get("thinking_level", "off"),
    }
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
        # L8：latency 只计量成功那次请求（不含退避睡眠墙钟）
        t_attempt = time.time()
        attempt = 0
        extras_degraded = False  # 参数类 400 降级只做一次，不计入退避次数

        while attempt <= self.MAX_RETRIES:
            try:
                response = await self._provider.chat(messages, **kwargs)
                latency_ms = int((time.time() - t_attempt) * 1000)
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
                # 双重保险第二道：端点拒绝扩展参数（thinking/reasoning 等）→
                # 置位降级标记后立即重试（该 provider 实例生命周期内不再发送）
                if not extras_degraded and _is_param_rejection(e):
                    extras_degraded = True
                    if hasattr(self._provider, "_extras_disabled"):
                        self._provider._extras_disabled = True
                    logger.warning(
                        "LLM endpoint rejected extra params, "
                        f"degraded retry without them: {_format_error_detail(e)}"
                    )
                    # 顺手项 1：降级重试前重置计时，避免把首次失败请求的耗时计入 latency
                    t_attempt = time.time()
                    continue
                # M7/M9：确定性错误（refusal/鉴权/参数类）不重试
                if _is_terminal_error(e):
                    break
                if attempt < self.MAX_RETRIES:
                    delay = _retry_delay(e, self.RETRY_BACKOFF[attempt])
                    logger.warning(
                        f"LLM retry {attempt + 1}/{self.MAX_RETRIES} "
                        f"after {delay}s: {_format_error_detail(e)}"
                    )
                    await asyncio.sleep(delay)
                attempt += 1
                t_attempt = time.time()  # L8：重试后重新计时

        # 所有重试耗尽 —— 记录错误并返回空响应（latency 仅最后尝试耗时）
        latency_ms = int((time.time() - t_attempt) * 1000)
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
