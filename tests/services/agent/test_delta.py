"""replay_delta 判别联合：四形态模型与单点编解码边界。

BDD：

- **编解码往返恒等**：seed / response / tool_result / budget_message 四种形态
  encode → decode 得到同型模型（``delta_version=1`` 保留）；
- **非法数据显式降级**：缺 kind / 未知 kind / 字段类型错 / 未知版本 / 坏 JSON
  → ``None`` + warning（不静默、不猜测）；
- **合法空值静默**：``None`` / ``""`` / ``{}`` → ``None``（不告警）。
"""

from __future__ import annotations

import json

import pytest

from app.services.agent.observe.delta import (
    BudgetMessageDelta,
    ResponseDelta,
    SeedDelta,
    ToolResultDelta,
    decode_replay_delta,
    encode_replay_delta,
)


@pytest.fixture
def log_records():
    """捕获自定义 Logger 的日志行（与既有测试同模式）。"""
    from app.core.logging import logger as app_logger

    records: list[tuple[str, str]] = []

    def _listener(line: str, level: str) -> None:
        records.append((level, line))

    app_logger.add_listener(_listener)
    yield records
    app_logger.remove_listener(_listener)


def _warns(log_records) -> list[str]:
    return [line for level, line in log_records if level == "WARNING"]


_CASES = [
    SeedDelta(seed_messages=[{"role": "system", "content": "sys"}]),
    ResponseDelta(
        response={
            "stop_reason": "tool_use",
            "content": "",
            "tool_calls": [{"id": "t1", "name": "x", "input": {}}],
            "blocks": [{"type": "tool_use", "id": "t1"}],
        }
    ),
    ToolResultDelta(
        tool_result={"tool_use_id": "t1", "content": "ok", "is_error": False}
    ),
    BudgetMessageDelta(budget_message="[剩余轮次：2]"),
]


@pytest.mark.parametrize("delta", _CASES, ids=lambda d: d.kind)
def test_encode_decode_roundtrip_is_identity(delta):
    raw = encode_replay_delta(delta)

    assert raw["kind"] == delta.kind
    assert raw["delta_version"] == 1
    assert decode_replay_delta(raw) == delta


@pytest.mark.parametrize("delta", _CASES, ids=lambda d: d.kind)
def test_decode_accepts_json_string(delta):
    decoded = decode_replay_delta(json.dumps(encode_replay_delta(delta)))
    assert decoded == delta


def test_decode_empty_values_silently_returns_none(log_records):
    assert decode_replay_delta(None) is None
    assert decode_replay_delta("") is None
    assert decode_replay_delta({}) is None
    assert _warns(log_records) == []


def test_decode_missing_kind_degrades_with_warning(log_records):
    assert decode_replay_delta({"budget_message": "x"}) is None
    assert any("形态校验失败" in line for line in _warns(log_records))


def test_decode_unknown_kind_degrades_with_warning(log_records):
    assert decode_replay_delta({"kind": "mystery", "delta_version": 1}) is None
    assert any("形态校验失败" in line for line in _warns(log_records))


def test_decode_field_type_error_degrades_with_warning(log_records):
    payload = {"kind": "budget_message", "delta_version": 1, "budget_message": 123}
    assert decode_replay_delta(payload) is None
    assert any("形态校验失败" in line for line in _warns(log_records))


def test_decode_unknown_delta_version_degrades_with_warning(log_records):
    payload = {"kind": "budget_message", "delta_version": 99, "budget_message": "x"}
    assert decode_replay_delta(payload) is None
    assert any("delta_version" in line for line in _warns(log_records))


def test_decode_invalid_json_string_degrades_with_warning(log_records):
    assert decode_replay_delta("{not-json") is None
    assert any("形态校验失败" in line for line in _warns(log_records))
