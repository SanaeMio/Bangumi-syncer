"""预算策略：IterationStrategy 注册表。

轻量策略模式：每个 task_type 注册一个「思考强度 → 轮次上限」的映射，
通用 Agent 骨架（loop.py）只消费计算好的 max_iterations，不关心映射来源。

优先级：
    1. config_override（[sync] llm_match_max_iterations 显式整体覆盖，最高）
    2. task_type 对应策略映射
    3. 默认兜底 {"off":1, "low":2, "medium":3, "high":5}
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.logging import logger

# 默认兜底：未知 task_type 或未注册策略时使用
_DEFAULT_FALLBACK: dict[str, int] = {"off": 1, "low": 2, "medium": 3, "high": 5}
_DEFAULT_FALLBACK_LEVEL = 3

# 单工具执行超时：与 app/services/llm/tools.py 的 ToolRegistry.execute 默认超时（30s）同步
_TOOL_TIMEOUT_SECONDS = 30.0
# 兜底缓冲：覆盖兜底收尾调用 / 恢复补执行 / 落库开销
_RUN_TIMEOUT_BUFFER_SECONDS = 120.0

# task_type -> IterationStrategy 注册表
_ITERATION_STRATEGIES: dict[str, IterationStrategy] = {}


@runtime_checkable
class IterationStrategy(Protocol):
    """思考强度 → 轮次上限的映射协议（轻量策略模式，不过度工程化）。"""

    def max_iterations(self, thinking_level: str) -> int: ...


class _LogLike(Protocol):
    """极小日志协议：仅需 ``warning(message)``。

    兼容 ``logging.Logger``（其 ``warning(msg, *args, **kwargs)`` 可接受单参调用），
    也兼容测试注入的替身；用于收窄 ``resolve_match_iterations_override`` 的 ``log``。
    """

    def warning(self, message: str) -> None: ...


class MatchIterationStrategy(IterationStrategy):
    """match 场景预设（骨架默认注册）。

    medium=5 / high=10：思考模型（pro / eval）实测 3 轮偏紧，易在预算耗尽前
    未产出结论；放宽轮次以覆盖「多轮检索 + 收尾」完整路径。
    """

    PRESET = {"off": 1, "low": 2, "medium": 5, "high": 10}

    def max_iterations(self, thinking_level: str) -> int:
        return self.PRESET.get(
            thinking_level, self.PRESET["medium"]
        )  # 未知 level 回落 medium


def register_iteration_strategy(task_type: str, strategy: IterationStrategy) -> None:
    """注册（或覆盖）某 task_type 的迭代策略。"""
    _ITERATION_STRATEGIES[task_type] = strategy


def get_max_iterations(
    task_type: str,
    thinking_level: str,
    config_override: int | None = None,
) -> int:
    """按优先级计算循环轮次上限。

    优先级：config_override > task_type 策略映射 > 默认兜底。
    """
    if config_override is not None:
        return config_override

    strategy = _ITERATION_STRATEGIES.get(task_type)
    if strategy is not None:
        return strategy.max_iterations(thinking_level)

    return _DEFAULT_FALLBACK.get(thinking_level, _DEFAULT_FALLBACK_LEVEL)


def register_defaults() -> None:
    """注册骨架内置默认策略（幂等，供后续扩展或测试显式调用）。"""
    register_iteration_strategy("match", MatchIterationStrategy())


def resolve_match_iterations_override(
    raw_max: object,
    log: _LogLike | None = None,
    *,
    log_prefix: str = "[budget]",
) -> int | None:
    """解析 ``[sync] llm_match_max_iterations`` 覆盖值（本模块为唯一实现）。

    本函数是预算覆盖解析的**单一实现**（agent 骨架侧）；场景侧
    ``app/services/matching/llm_assist.py::resolve_max_iterations_override``
    仅做薄委托（传入 ``log_prefix="[llm_assist]"``），不再各自复刻、避免漂移。

    - 空值（None / 空串 / 纯空白）→ None（静默，属默认配置）
    - 非法整数 → None + 告警
    - 非正数（<=0）→ None + 告警（否则 max_iterations<=0 会让循环空跑）

    ``log_prefix`` 参数化告警前缀（默认 ``[budget]``），其余文案不变。
    """
    _log = log if log is not None else logger
    if raw_max is None:
        return None
    text = str(raw_max).strip()
    if text == "":
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        _log.warning(
            f"{log_prefix} llm_match_max_iterations={raw_max!r} 非法整数，"
            f"已忽略该覆盖（回退思考强度策略默认）"
        )
        return None
    if value <= 0:
        _log.warning(
            f"{log_prefix} llm_match_max_iterations={value} 必须为正整数，"
            f"已忽略该覆盖（回退思考强度策略默认）"
        )
        return None
    return value


def compute_match_run_timeout(match_cfg: dict, llm_timeout: float) -> float:
    """推算 llm_match 单 run 的最大执行时长（动态推算，不新增配置项）。

    ``run_timeout = max_iterations × (llm_timeout + TOOL_TIMEOUT) + BUFFER``

    - ``max_iterations``：按 thinking_level 策略映射，显式覆盖优先
    - ``TOOL_TIMEOUT=30s``：与 ``app/services/llm/tools.py`` 的
      ``ToolRegistry.execute`` 默认超时同步（每轮至多一次工具调用的耗时上限）
    - ``BUFFER=120s``：覆盖兜底收尾调用 / 恢复补执行 / 落库开销
    """
    thinking_level = match_cfg.get("llm_match_thinking_level", "medium")
    override = resolve_match_iterations_override(
        match_cfg.get("llm_match_max_iterations")
    )
    max_iterations = get_max_iterations(
        "match", thinking_level, config_override=override
    )
    return (
        max_iterations * (llm_timeout + _TOOL_TIMEOUT_SECONDS)
        + _RUN_TIMEOUT_BUFFER_SECONDS
    )


# 模块导入即注册 match 默认策略，确保通用骨架开箱可用
register_defaults()
