"""日志分级守卫（agent 生态）：``except`` 块内禁止 ``logger.debug``。

判据（与 developer 文档一致）：

- ``warning``：异常被捕获并降级 / 防御兜底触发 / 数据异常被跳过 —— 默认
  ``log_level = INFO`` 下必须可见；
- ``except`` 块内的**分支说明**（异常重抛前）至少 ``info``，不得 ``debug``。

否则错误路径在默认级别下完全没有日志，排障只能靠复现或读代码。

范围（agent 生态）：``app/services/agent/**``、``app/services/llm/**``、
``app/services/llm_match_scheduler.py``；其余历史模块不在本守卫范围。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

_TARGET_DIRS = (
    Path("app/services/agent"),
    Path("app/services/llm"),
)
_TARGET_FILES = (
    Path("app/services/llm_match_scheduler.py"),
    # 通知 helper 的 except 降级日志同样纳入分级守卫
    Path("app/services/base/notifier_helpers.py"),
)


def _iter_files() -> Iterator[Path]:
    for base in _TARGET_DIRS:
        yield from sorted(base.rglob("*.py"))
    yield from _TARGET_FILES


def _debug_calls_inside_except(path: Path) -> list[int]:
    """返回该文件中「位于 ``except`` 块内」的 ``logger.debug`` 行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "debug"
                and getattr(sub.func.value, "id", "") == "logger"
            ):
                hits.append(sub.lineno)
    return hits


def test_no_debug_logging_inside_except_blocks():
    """``except`` 块内不得使用 ``logger.debug``（错误路径必须默认可见）。"""
    offenders: list[str] = []
    for path in _iter_files():
        for lineno in _debug_calls_inside_except(path):
            offenders.append(f"{path}:{lineno}")
    assert offenders == [], (
        "except 块内禁止 logger.debug：被吞异常/降级用 warning，"
        "重抛前的分支说明至少 info（默认 log_level=INFO 下可见）：\n"
        + "\n".join(offenders)
    )
