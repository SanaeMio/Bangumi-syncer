"""llm 层只放通用设施：不得**依赖**领域模块（import 级边界守卫）。

对应评审意见 ``app/services/llm/output_parser.py:1``「应该迁移到 matching 模块，
理由：实际非通用能力，专为 matching 使用」。

领域能力一旦上浮进 ``llm`` 这类「通用」层，就会被其它场景误用（耦合伪装成复用），
且删除/改签名的影响面被放大。本守卫以 **import 依赖**表述边界（而非源码文本）：
注释 / docstring 提及领域概念是合法的交叉引用，不构成依赖。
"""

from __future__ import annotations

import ast
from pathlib import Path

_LLM_DIR = Path("app/services/llm")

#: llm 层不得 import 的领域模块前缀（依赖方向：领域 → llm，禁止反向）
_FORBIDDEN_MODULE_PREFIXES = (
    "app.services.matching",
    "app.services.scenarios",
    "app.services.agent",
    "app.services.sync_service",
    "app.utils.bangumi_api",
    "app.models.sync",
)


def _iter_imported_modules(tree: ast.AST):
    """产出 AST 中的所有 (行号, 模块名) 对。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield node.lineno, node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name


def test_llm_layer_has_no_domain_module_imports():
    """``app/services/llm/**`` 不得 import 领域模块（matching / agent / bangumi_api…）。"""
    offenders: list[str] = []
    for path in sorted(_LLM_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, module in _iter_imported_modules(tree):
            if module and module.startswith(_FORBIDDEN_MODULE_PREFIXES):
                offenders.append(f"{path}:{lineno}: {module}")
    assert offenders == [], (
        "llm 层不得依赖领域模块（领域能力应归位场景侧）：\n" + "\n".join(offenders)
    )
