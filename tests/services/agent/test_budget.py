"""IterationStrategy 预算策略注册表测试。

覆盖：
- MatchIterationStrategy 预设 off=1/low=2/medium=5/high=10，未知 level 兜底 medium=5
- register 后 get_max_iterations 返回对应策略；不同 task_type 互不影响；重复注册覆盖
- 优先级：config_override 最高 > 策略映射 > 默认兜底
- 未注册 task_type 仍走保守默认兜底 off=1/low=2/medium=3/high=5
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.agent.budget import (
    _ITERATION_STRATEGIES,
    IterationStrategy,
    MatchIterationStrategy,
    compute_match_run_timeout,
    get_max_iterations,
    register_iteration_strategy,
    resolve_match_iterations_override,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """隔离全局注册表，保证测试之间互不影响。"""
    saved = dict(_ITERATION_STRATEGIES)
    yield
    _ITERATION_STRATEGIES.clear()
    _ITERATION_STRATEGIES.update(saved)


class _DummyStrategy(IterationStrategy):
    """用于验证注册表行为的简单策略（对齐 IterationStrategy 协议签名）。"""

    def __init__(self, preset: dict[str, int], fallback: int = 3) -> None:
        self.PRESET = preset
        self._fallback = fallback

    def max_iterations(self, thinking_level: str) -> int:
        return self.PRESET.get(thinking_level, self._fallback)


# --- MatchIterationStrategy 预设 ---


def test_match_iteration_strategy_off_returns_1():
    strategy = MatchIterationStrategy()
    assert strategy.max_iterations("off") == 1


def test_match_iteration_strategy_low_returns_2():
    strategy = MatchIterationStrategy()
    assert strategy.max_iterations("low") == 2


def test_match_iteration_strategy_medium_returns_5():
    strategy = MatchIterationStrategy()
    assert strategy.max_iterations("medium") == 5


def test_match_iteration_strategy_high_returns_10():
    strategy = MatchIterationStrategy()
    assert strategy.max_iterations("high") == 10


def test_max_iterations_accepts_protocol_keyword():
    """协议兼容：keyword 传参 ``thinking_level`` 必须被接受（签名与 IterationStrategy 对齐）。"""
    assert MatchIterationStrategy().max_iterations(thinking_level="high") == 10


def test_match_iteration_strategy_unknown_level_falls_back_to_medium():
    strategy = MatchIterationStrategy()
    assert strategy.max_iterations("bogus") == 5
    assert strategy.max_iterations("") == 5
    assert strategy.max_iterations("ULTRA") == 5


# --- 默认注册（match 应随模块导入即注册） ---


def test_match_strategy_registered_by_default():
    assert "match" in _ITERATION_STRATEGIES
    assert isinstance(_ITERATION_STRATEGIES["match"], MatchIterationStrategy)


def test_get_max_iterations_match_default_preset_levels():
    assert get_max_iterations("match", "off") == 1
    assert get_max_iterations("match", "low") == 2
    assert get_max_iterations("match", "medium") == 5
    assert get_max_iterations("match", "high") == 10


# --- 注册与 get_max_iterations 行为 ---


def test_get_max_iterations_registered_task_type_returns_strategy_value():
    # fallback=6 用于验证：未知 level 走「策略自身兜底」而非全局默认（同为 3 时无法区分）
    register_iteration_strategy(
        "diagnostic",
        _DummyStrategy({"off": 2, "low": 4, "medium": 6, "high": 10}, fallback=6),
    )
    assert get_max_iterations("diagnostic", "off") == 2
    assert get_max_iterations("diagnostic", "high") == 10
    assert get_max_iterations("diagnostic", "weird") == 6  # 策略自身兜底


def test_get_max_iterations_different_task_types_isolated():
    register_iteration_strategy(
        "diagnostic", _DummyStrategy({"off": 9, "medium": 9, "high": 9})
    )
    # match 始终保持自己的预设，不受 diagnostic 影响
    assert get_max_iterations("match", "off") == 1
    assert get_max_iterations("match", "high") == 10
    assert get_max_iterations("diagnostic", "off") == 9


def test_register_iteration_strategy_duplicate_overrides():
    first = _DummyStrategy({"off": 1, "medium": 1})
    second = _DummyStrategy({"off": 7, "medium": 7})
    register_iteration_strategy("custom", first)
    assert get_max_iterations("custom", "off") == 1
    register_iteration_strategy("custom", second)  # 重复注册覆盖
    assert get_max_iterations("custom", "off") == 7
    assert get_max_iterations("custom", "medium") == 7


# --- 默认兜底（未注册 task_type） ---


def test_get_max_iterations_unregistered_task_type_returns_default_fallback():
    assert get_max_iterations("unknown_task", "off") == 1
    assert get_max_iterations("unknown_task", "low") == 2
    assert get_max_iterations("unknown_task", "medium") == 3
    assert get_max_iterations("unknown_task", "high") == 5


def test_get_max_iterations_unregistered_task_type_unknown_level_returns_3():
    assert get_max_iterations("unknown_task", "bogus") == 3


# --- 优先级：config_override > 策略 > 默认兜底 ---


def test_get_max_iterations_config_override_highest_priority():
    register_iteration_strategy("diagnostic", _DummyStrategy({"off": 5, "high": 5}))
    # config_override 应覆盖已注册策略
    assert get_max_iterations("diagnostic", "off", config_override=42) == 42
    # config_override 也应覆盖未注册 task_type 的默认兜底
    assert get_max_iterations("unknown_task", "high", config_override=99) == 99


def test_get_max_iterations_config_override_none_falls_through_to_strategy():
    register_iteration_strategy("diagnostic", _DummyStrategy({"off": 5}))
    # 显式传 None 应与不带参数一致（走策略/默认）
    assert (
        get_max_iterations("diagnostic", "off", config_override=None)
        == get_max_iterations("diagnostic", "off")
        == 5
    )


# --- 调度器单 run 超时推算（compute_match_run_timeout） ---
#
# run_timeout = max_iterations × (llm_timeout + TOOL_TIMEOUT=30) + BUFFER=120
# llm_timeout=60 时：off=1→210 / low=2→300 / medium=5→570 / high=10→1020


def _match_cfg(level: str, override="") -> dict:
    return {
        "llm_match_thinking_level": level,
        "llm_match_max_iterations": override,
    }


@pytest.mark.parametrize(
    "level,expected",
    [
        ("off", 210.0),
        ("low", 300.0),
        ("medium", 570.0),
        ("high", 1020.0),
    ],
)
def test_compute_match_run_timeout_by_thinking_level(level, expected):
    """按 thinking_level 推算：max_iterations × (llm_timeout + 工具超时) + 缓冲。"""
    assert compute_match_run_timeout(_match_cfg(level), 60) == expected


def test_compute_match_run_timeout_override_takes_priority():
    """显式 max_iterations 覆盖生效：override=3 → 3×90+120=390。"""
    assert compute_match_run_timeout(_match_cfg("high", "3"), 60) == 390.0


def test_compute_match_run_timeout_scales_with_llm_timeout():
    """llm_timeout 变化等比反映到 run_timeout（medium：5×90+120=570 → 5×150+120=870）。"""
    assert compute_match_run_timeout(_match_cfg("medium"), 120) == 870.0


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_compute_match_run_timeout_blank_override_silently_falls_back(blank):
    """空值覆盖（None/空串/空白）→ 静默回退 thinking_level 映射，不告警。"""
    log = MagicMock()
    with patch("app.services.agent.budget.logger", log):
        result = compute_match_run_timeout(_match_cfg("medium", blank), 60)
    assert result == 570.0
    log.warning.assert_not_called()


def test_compute_match_run_timeout_invalid_override_warns_and_falls_back():
    """非法整数覆盖 → warning + 回退 thinking_level 映射。"""
    log = MagicMock()
    with patch("app.services.agent.budget.logger", log):
        result = compute_match_run_timeout(_match_cfg("low", "abc"), 60)
    assert result == 300.0
    log.warning.assert_called_once()


@pytest.mark.parametrize("bad", ["0", "-2"])
def test_compute_match_run_timeout_non_positive_override_warns_and_falls_back(bad):
    """非正数覆盖（<=0）→ warning + 回退 thinking_level 映射。"""
    log = MagicMock()
    with patch("app.services.agent.budget.logger", log):
        result = compute_match_run_timeout(_match_cfg("off", bad), 60)
    assert result == 210.0
    log.warning.assert_called_once()


# --- 预算覆盖解析单一实现（resolve_match_iterations_override） ---
#
# 该函数是覆盖解析的唯一实现：场景侧 llm_assist.resolve_max_iterations_override
# 仅做薄委托并传 log_prefix="[llm_assist]"，故此处直接锁定解析语义与告警前缀。


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_resolve_match_iterations_override_blank_silently_returns_none(blank):
    """空值（None/空串/纯空白）→ None 且不告警（属默认配置）。"""
    log = MagicMock()
    assert resolve_match_iterations_override(blank, log) is None
    log.warning.assert_not_called()


def test_resolve_match_iterations_override_invalid_warns_and_returns_none():
    """非法整数 → None + 告警，默认前缀为 [budget]。"""
    log = MagicMock()
    assert resolve_match_iterations_override("abc", log) is None
    log.warning.assert_called_once()
    message = log.warning.call_args[0][0]
    assert message.startswith("[budget] llm_match_max_iterations='abc' 非法整数")
    assert "已忽略该覆盖（回退思考强度策略默认）" in message


@pytest.mark.parametrize("bad", ["0", "-2"])
def test_resolve_match_iterations_override_non_positive_warns_and_returns_none(bad):
    """非正数（<=0）→ None + 告警（否则 max_iterations<=0 会让循环空跑）。"""
    log = MagicMock()
    assert resolve_match_iterations_override(bad, log) is None
    log.warning.assert_called_once()
    message = log.warning.call_args[0][0]
    assert message.startswith(
        f"[budget] llm_match_max_iterations={int(bad)} 必须为正整数"
    )
    assert "已忽略该覆盖（回退思考强度策略默认）" in message


def test_resolve_match_iterations_override_positive_returns_value():
    """正整数（含前后空白）→ 返回解析后的 int，不告警。"""
    log = MagicMock()
    assert resolve_match_iterations_override("7", log) == 7
    assert resolve_match_iterations_override(" 10 ", log) == 10
    log.warning.assert_not_called()


def test_resolve_match_iterations_override_custom_log_prefix_in_message():
    """log_prefix 参数化告警前缀：场景侧传 [llm_assist] 时文案前缀随之外显。"""
    log = MagicMock()
    assert (
        resolve_match_iterations_override("abc", log, log_prefix="[llm_assist]") is None
    )
    message = log.warning.call_args[0][0]
    assert message.startswith("[llm_assist]")
    assert "[budget]" not in message
