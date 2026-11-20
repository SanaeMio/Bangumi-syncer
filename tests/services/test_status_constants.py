"""StepOutcome 状态常量的守卫用例。

状态字符串是**契约**：``SyncPipeline`` 见到 ``error`` 无条件终止，
``SyncOrchestrator`` 按 ``episode_resolve/error``、``cross_season/miss``、
``sync_action/error`` 分派兜底，``MatchPipeline`` 按 ``hit``/``low_confidence``
结算 final_*。任何一处拼错都会让该步「静默落到两条管线都不处理的分支」——
既不算命中也不算终止，排查代价极高。

531fdd76 把 matching 侧的 45 处收成了 ``contracts.STATUS_*``，但 sync 侧的 16 处
生产者仍是裸字符串，而消费侧（SyncPipeline / SyncOrchestrator）已经改成比常量。
本文件用 AST 扫生产侧，防止再长出第二套字面量：

1. ``StepOutcome(status=...)`` 不允许写字符串字面量；
2. 常量取值本身也被钉住 —— 它会被写进 match_trace 并落库，改值等于改数据格式。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.matching.contracts import (
    STATUS_ERROR,
    STATUS_HIT,
    STATUS_LOW_CONFIDENCE,
    STATUS_MISS,
    STATUS_SKIPPED,
)

ROOT = Path(__file__).resolve().parents[2]
PRODUCER_DIRS = (
    ROOT / "app" / "services" / "matching",
    ROOT / "app" / "services" / "sync_service" / "steps",
)


def _iter_step_outcome_status_literals() -> list[str]:
    """扫描所有 ``StepOutcome(status="…")``，返回违规位置。"""
    hits: list[str] = []
    for directory in PRODUCER_DIRS:
        for path in sorted(directory.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", "")
                )
                if name != "StepOutcome":
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "status":
                        continue
                    value = keyword.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        rel = path.relative_to(ROOT).as_posix()
                        hits.append(f"{rel}:{node.lineno} status={value.value!r}")
    return hits


def test_no_raw_status_literals_in_step_outcome() -> None:
    """生产者不得写裸状态字符串（消费侧已经在比常量）。"""
    hits = _iter_step_outcome_status_literals()
    assert not hits, "StepOutcome.status 请改用 contracts.STATUS_*：\n" + "\n".join(
        hits
    )


@pytest.mark.parametrize(
    ("constant", "value"),
    [
        (STATUS_HIT, "hit"),
        (STATUS_MISS, "miss"),
        (STATUS_ERROR, "error"),
        (STATUS_LOW_CONFIDENCE, "low_confidence"),
        (STATUS_SKIPPED, "skipped"),
    ],
)
def test_status_constant_values_are_pinned(constant: str, value: str) -> None:
    """常量取值落进 match_trace / 数据库，改值等于改数据格式。"""
    assert constant == value


def test_status_constants_are_distinct() -> None:
    values = {
        STATUS_HIT,
        STATUS_MISS,
        STATUS_ERROR,
        STATUS_LOW_CONFIDENCE,
        STATUS_SKIPPED,
    }
    assert len(values) == 5
