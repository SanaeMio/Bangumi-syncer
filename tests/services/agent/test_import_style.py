"""导入风格守卫：agent 相关导入禁止 ``as`` 别名（保证名字可一跳追溯）。

背景：ty 的 go-to-definition / go-to-declaration 会**跳过 ``as`` 绑定点**、直奔原定义
（astral-sh/ty#1592，maintainer 提交且未修；实测 ty 0.0.84：references / rename 对别名
处理正常）。多级别名下「这个名字从哪来」无法一跳看到，故约定：

- **agent 相关导入禁止任何 ``as`` 别名**（符号或模块）；需要避让时改**本地名字**
  （参数 / 变量），patch 目标用字符串路径（``patch("a.b.c.fn", ...)``）；
- 需要"留真身"（patch 后仍调原函数）时用**本地赋值**（``real = module.func``），
  而不是导入别名；
- 不相关模块的别名（如 ``import json as _json``、FastAPI router 别名）不受本守卫约束。

受控范围（agent 生态）：``app.services.agent*``、``app.services.scenarios``、
``app.services.matching.llm_assist``、``app.services.llm_match_scheduler``。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

_SCAN_DIRS = ("app", "eval", "tests")
_AGENT_PREFIX = "app.services.agent"
#: 精确匹配（覆盖 ``from app.services import scenarios`` 这类形态）
_AGENT_EXACT = {
    "app.services.scenarios",
    "app.services.matching.llm_assist",
    "app.services.llm_match_scheduler",
}


def _iter_py_files() -> Iterator[Path]:
    for base in _SCAN_DIRS:
        for path in sorted(Path(base).rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            yield path


def _is_agent_related(module: str | None, name: str) -> bool:
    """``from <module> import <name>`` / ``import <name>`` 是否属于 agent 生态。"""
    if not module:
        return False
    candidates = {module, f"{module}.{name}"}
    for target in candidates:
        if target == _AGENT_PREFIX or target.startswith(_AGENT_PREFIX + "."):
            return True
        if target in _AGENT_EXACT:
            return True
    return False


def test_no_agent_import_aliases():
    """agent 相关导入不得使用 ``as`` 别名（符号或模块）。"""
    offenders: list[str] = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname and _is_agent_related(node.module, alias.name):
                        offenders.append(
                            f"{path}:{node.lineno}: from {node.module} "
                            f"import {alias.name} as {alias.asname}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname and _is_agent_related(alias.name, ""):
                        offenders.append(
                            f"{path}:{node.lineno}: import {alias.name} as {alias.asname}"
                        )
    assert offenders == [], (
        "agent 相关导入禁止 as 别名（需要避让时改本地名字、patch 用字符串路径、"
        "留真身用本地赋值）：\n" + "\n".join(offenders)
    )
