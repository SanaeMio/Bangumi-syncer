"""LLM 客户端（重试逻辑与用量日志记录）。

提供 LLMClient —— 对 OpenAI 兼容 provider 的封装，增加了自动重试（含退避等待）
和用量记录功能。同时通过 get_llm_client() 导出模块级单例。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator

import httpx

from app.core.config import config_manager
from app.core.logging import logger

from .models import (
    Message,
    StreamChunk,
    Usage,
)
from .providers.anthropic import AnthropicProvider
from .providers.base import BaseProvider, ProviderStreamError
from .providers.openai_compat import OpenAICompatProvider
from .providers.openai_responses import OpenAIResponsesProvider

_PROVIDER_MAP: dict[str, type] = {
    "openai_compat": OpenAICompatProvider,
    "anthropic_compat": AnthropicProvider,
    "openai_responses": OpenAIResponsesProvider,
}


def _format_error_detail(e: Exception) -> str:
    """从异常对象提取详细的错误信息，包含异常类型、消息、底层原因和请求 URL。"""
    parts = [f"{type(e).__name__}: {e}"]
    cause = getattr(e, "__cause__", None)
    if cause is not None:
        parts.append(f"[cause: {type(cause).__name__}: {cause}]")
    try:
        # httpx 的 .request property 在未附加请求时会抛 RuntimeError
        # （如连接建立前的 ConnectError/超时），此处兜底避免格式化本身失败
        req = getattr(e, "request", None)
    except RuntimeError:
        req = None
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
# 状态码放行 400/422（pydantic 网关）。
_PARAM_REJECTION_PATTERNS = (
    "unrecognized request argument",
    "unrecognized parameter",
    "unknown parameter",
    "unexpected keyword",
    "invalid request argument",
    "extra fields not permitted",
    "does not support thinking",
    "does not support reasoning",
    # 强制 tool_choice 被 thinking 模式拒绝的实测文案（DeepSeek anthropic 兼容层）：
    # "Thinking mode does not support this tool_choice"
    "does not support this tool_choice",
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


# 端点拒绝 stream 参数的响应特征（大小写不敏感子串匹配）。
# 与 _PARAM_REJECTION_PATTERNS 同风格，但语义更特定：命中后判定该端点**不支持
# 流式调用**——因流式为唯一形态，不再降级/伪装，直接抛确定性 LLMCallError。
# 故判定顺序须先于 _is_param_rejection——例如 "unknown parameter: stream"
# 同时命中通用模式，但按 stream 拒绝（终态）处理才是正确行为。
_STREAM_REJECTION_PATTERNS = (
    "stream is not supported",
    "streaming is not supported",
    "stream not supported",
    "does not support stream",
    "unsupported parameter: stream",
    "unknown parameter: stream",
    "stream: not supported",
    # 网关文案 "unrecognized parameter 'stream' is not supported"
    # 注意保留闭合引号，避免误伤 stream_options 等同前缀参数
    "unrecognized parameter 'stream'",
    'unrecognized parameter "stream"',
)


def _is_stream_rejection(e: Exception) -> bool:
    """识别"端点不支持 stream 参数"类错误（区别于普通扩展参数拒绝）。

    仅对 400/422（网关参数校验类状态码）生效；命中后调用方应判定该端点不支持
    流式（SSE），直接抛确定性 LLMCallError（不重试、不降级、不伪装成流）。
    """
    if not isinstance(e, httpx.HTTPStatusError):
        return False
    resp = getattr(e, "response", None)
    if resp is None or resp.status_code not in _PARAM_REJECTION_STATUSES:
        return False
    text = (resp.text or "").lower()
    return any(p in text for p in _STREAM_REJECTION_PATTERNS)


# 确定性错误 —— 重试无意义（refusal / 鉴权 / 参数类 / 不存在）
_TERMINAL_STATUSES = (400, 401, 403, 404, 422)


def _is_terminal_error(e: Exception | None) -> bool:
    """refusal（ValueError）与确定性 4xx 不重试；其余（429/5xx/超时）可重试。

    ``None``（尚未捕获到任何异常）视为可重试，与调用方语义一致。
    """
    if e is None:
        return False
    if isinstance(e, ValueError):
        return True  # 解析失败/refusal，重试结果不变
    if isinstance(e, httpx.HTTPStatusError):
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        if status in _TERMINAL_STATUSES:
            # 参数类 400/422 由调用方先走降级路径，此处仅兜底其它确定性 4xx
            return not _is_param_rejection(e)
    return False


class LLMCallError(Exception):
    """LLM 调用失败（重试耗尽或确定性错误）。

    ``retryable`` 指示调用方是否可以安全重试：
    - ``True``：429/5xx/超时类故障，下个调度周期重试可能恢复。
    - ``False``：401/403/400/参数类/refusal 等确定性错误，重试无意义。
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


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
        # 不再读取全局 [llm].thinking_level：provider 构造函数默认 "off"，
        # 思考强度只由每任务 kwargs（stream/chat 的 thinking_level=...）覆盖。
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

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        job_id: int | None = None,
        job_name: str | None = None,
        **kwargs,
    ) -> AsyncGenerator[StreamChunk, None]:
        """流式聊天入口：逐条产出归一化事件，正常耗尽后落库一次。

        轻量元数据跟踪（不聚合 blocks，聚合交由调用方 models.collect()）：
        model 取事件携带的真实模型名（非空覆盖），缺失时回退 provider 配置；
        usage 取 usage 事件，缺失时按 0 落库并记 warning（网关忽略
        stream_options 的退化可见）；latency 为本方法内流消费全程墙钟。

        Args:
            messages: 对话消息列表。
            job_id: 可选的 job 标识符，用于用量追踪。
            job_name: 可选的 job 名称，用于用量追踪。
            **kwargs: provider 特定的覆盖参数。

        Yields:
            provider 无关的归一化流式事件 StreamChunk。

        Raises:
            LLMCallError: 重试耗尽/确定性错误/已产出内容后中断（透传给消费方）。
        """
        t0 = time.time()
        model = kwargs.get("model") or getattr(self._provider, "model", "")
        usage: Usage | None = None
        completed = False
        inner = self._stream_with_retry(
            messages,
            job_id=job_id,
            job_name=job_name,
            **kwargs,
        )
        try:
            async for chunk in inner:
                if chunk.model:
                    model = chunk.model
                if chunk.type == "usage" and chunk.usage is not None:
                    usage = chunk.usage
                yield chunk
            completed = True
        finally:
            if not completed:
                # 消费方提前 aclose / 异常中断：关闭内层流并记 warning，不落成功。
                try:
                    await inner.aclose()
                except Exception as exc:  # noqa: BLE001 - 关闭失败不应掩盖原异常
                    logger.warning(f"关闭 LLM 流生成器时出错: {exc}")
                logger.warning(
                    "LLM 流式调用未正常耗尽（消费方提前关闭或异常中断），跳过成功落库"
                )
            else:
                latency_ms = int((time.time() - t0) * 1000)
                if usage is None:
                    logger.warning(
                        "LLM 流式响应未上报 usage（网关可能忽略 stream_options），"
                        "按 0 落库"
                    )
                self._log_success(
                    model=model,
                    usage=usage,
                    latency_ms=latency_ms,
                    job_id=job_id,
                    job_name=job_name,
                )
                logger.debug(
                    f"LLM stream call: model={model} "
                    f"tokens={usage.total_tokens if usage else 0} "
                    f"latency={latency_ms}ms"
                )

    async def _stream_with_retry(
        self,
        messages: list[Message],
        *,
        job_id: int | None = None,
        job_name: str | None = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """流式调用的重试/降级包装（唯一底层实现 = provider.stream）。

        决策树（``started`` 表示是否已产出首个 chunk）：

        - 首 chunk 前失败：
          stream 拒绝（400/422）→ 抛确定性 LLMCallError（不重试、不降级、不伪装）；
          参数拒绝 → 置降级标记后立即重试一次；
          429/5xx/连接超时 → 退避重试（MAX_RETRIES）；
          确定性 4xx/refusal → 终态。
        - 首 chunk 后失败：一律不重试（已产出内容不回滚），包装为
          ``LLMCallError(retryable=True)`` 抛出。
        """
        provider = self._provider
        last_error: Exception | None = None
        t_attempt = time.time()
        attempt = 0
        extras_degraded = False  # 参数类 400 降级只做一次，不计入退避次数
        started = False

        while attempt <= self.MAX_RETRIES:
            source = provider.stream(messages, **kwargs)
            try:
                async for chunk in source:
                    started = True
                    yield chunk
                return
            except Exception as e:  # noqa: BLE001 - 统一按重试/终态策略处理
                last_error = e
                if isinstance(e, LLMCallError):
                    # 确定性 LLMCallError 不套用重试策略，保留 retryable 原样抛出
                    logger.info(
                        "LLM stream 收到 LLMCallError，直接透传（不重试）: "
                        f"{_format_error_detail(e)}"
                    )
                    raise
                if isinstance(e, ProviderStreamError) and not started:
                    # provider 在 200 流内报告的错误事件：未产出内容时可按
                    # retryable 重试；已产出内容走下方 started 分支，不重试但报错。
                    error_detail = _format_error_detail(e)
                    if e.retryable and attempt < self.MAX_RETRIES:
                        delay = _retry_delay(e, self.RETRY_BACKOFF[attempt])
                        logger.warning(
                            f"LLM stream retry {attempt + 1}/{self.MAX_RETRIES} "
                            f"after {delay}s: {error_detail}"
                        )
                        await asyncio.sleep(delay)
                        attempt += 1
                        t_attempt = time.time()
                        continue
                    logger.error(
                        f"LLM stream 收到 provider 错误事件，不再重试: {error_detail}"
                    )
                    self._log_error(
                        error_detail,
                        job_id=job_id,
                        job_name=job_name,
                        latency_ms=int((time.time() - t_attempt) * 1000),
                    )
                    raise LLMCallError(error_detail, retryable=e.retryable) from e
                if started:
                    # 已产出内容：不回滚、不重试，包装为可重试错误抛出
                    latency_ms = int((time.time() - t_attempt) * 1000)
                    error_detail = _format_error_detail(e)
                    logger.error(
                        f"LLM stream 中断（已产出内容，不再重试）: {error_detail}"
                    )
                    self._log_error(
                        error_detail,
                        job_id=job_id,
                        job_name=job_name,
                        latency_ms=latency_ms,
                    )
                    raise LLMCallError(error_detail, retryable=True) from e

                # 首 chunk 前失败：按语义重试/降级
                if _is_stream_rejection(e):
                    # 流式为唯一形态：端点不支持 SSE → 确定性失败，不重试/不降级/不伪装
                    error_detail = _format_error_detail(e)
                    logger.error(
                        f"LLM 端点不支持流式调用（SSE），不再重试/降级: {error_detail}"
                    )
                    self._log_error(
                        error_detail,
                        job_id=job_id,
                        job_name=job_name,
                        latency_ms=int((time.time() - t_attempt) * 1000),
                    )
                    raise LLMCallError(
                        f"端点不支持流式调用（SSE）：{error_detail}，"
                        "请更换端点或升级网关",
                        retryable=False,
                    ) from e
                if not extras_degraded and _is_param_rejection(e):
                    extras_degraded = True
                    self._degrade_extras(e)
                    # 降级重试前重置计时，避免把首次失败请求耗时计入 latency
                    t_attempt = time.time()
                    continue
                # 已降级后再次命中参数类拒绝 → 该端点始终不支持该参数 → 终态
                if extras_degraded and _is_param_rejection(e):
                    break
                if _is_terminal_error(e):
                    break
                if attempt < self.MAX_RETRIES:
                    delay = _retry_delay(e, self.RETRY_BACKOFF[attempt])
                    logger.warning(
                        f"LLM stream retry {attempt + 1}/{self.MAX_RETRIES} "
                        f"after {delay}s: {_format_error_detail(e)}"
                    )
                    await asyncio.sleep(delay)
                attempt += 1
                t_attempt = time.time()
            finally:
                # 显式关闭上游生成器，避免 GeneratorExit/中断时 httpx 流泄漏。
                # 防御 source 非 async generator（无 aclose）时 AttributeError。
                aclose = getattr(source, "aclose", None)
                if aclose is not None:
                    await aclose()
                else:
                    logger.debug(
                        "LLM stream source 无 aclose 方法，跳过显式关闭: %s",
                        type(source).__name__,
                    )

        # 所有重试耗尽 —— 记录错误并抛 LLMCallError
        latency_ms = int((time.time() - t_attempt) * 1000)
        error_detail = _format_error_detail(last_error) if last_error else "unknown"
        logger.error(
            f"LLM stream failed after {self.MAX_RETRIES} retries: {error_detail}"
        )
        self._log_error(
            error_detail,
            job_id=job_id,
            job_name=job_name,
            latency_ms=latency_ms,
        )
        retryable = not _is_terminal_error(last_error)
        if (
            extras_degraded
            and last_error is not None
            and _is_param_rejection(last_error)
        ):
            retryable = False
        raise LLMCallError(error_detail, retryable=retryable) from last_error

    def _degrade_extras(self, e: Exception) -> None:
        """参数类 400/422 降级：置位 provider 的扩展参数/强制 tool_choice 标记。"""
        resp_text = (getattr(getattr(e, "response", None), "text", "") or "").lower()
        if "tool_choice" in resp_text and hasattr(
            self._provider, "_force_tool_choice_degraded"
        ):
            # 专用降级：部分端点（thinking 模式）拒绝强制工具选择，
            # 仅降级 tool_choice，保留 thinking 等其它参数
            self._provider._force_tool_choice_degraded = True
            logger.warning(
                "LLM endpoint rejected forced tool_choice, "
                f"degraded to auto: {_format_error_detail(e)}"
            )
        if hasattr(self._provider, "_extras_disabled"):
            self._provider._extras_disabled = True
        logger.warning(
            "LLM endpoint rejected extra params, "
            f"degraded retry without them: {_format_error_detail(e)}"
        )

    def _log_success(
        self,
        *,
        model: str,
        usage: Usage | None,
        latency_ms: int,
        job_id: int | None = None,
        job_name: str | None = None,
    ) -> None:
        """记录成功 LLM 调用（显式接收 model/usage/latency）。"""
        try:
            from app.core.database import database_manager

            database_manager.llm_usage.log_usage(
                job_id=job_id,
                job_name=job_name or "",
                model=model,
                provider=self._provider_name,
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
