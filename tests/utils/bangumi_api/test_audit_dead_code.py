"""D 死代码验证测试

用 ast 静态分析 + 行为测试验证 5 个死代码问题：
- D-1: mainline_match 变量（api_search_main.py）只有初始化无赋值
- D-2: ``None, None if target_ep else None`` 死逻辑（episodes.py）
- D-3: try_search_old 已弃用方法无生产调用
- D-4: _title_to_ids / _bigram_index 空属性无生产引用
- D-5: search.py 的 FALLBACK_SEARCH_LIMIT 无引用

所有测试为普通测试（断言当前死代码状态，通过），确认死代码存在。
"""

from __future__ import annotations

import ast
from pathlib import Path

# app/ 根目录（tests/utils/bangumi_api/ → 上溯 3 级到项目根 → app/）
_APP_ROOT = Path(__file__).resolve().parents[3] / "app"


def _collect_python_files(root: Path) -> list[Path]:
    """收集 root 下所有 .py 文件"""
    return sorted(root.rglob("*.py"))


def _parse_ast(filepath: Path) -> ast.AST:
    """解析 Python 文件为 AST"""
    return ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))


class TestVerifyD01MainlineMatchDeadCode:
    """D-1: mainline_match 变量（随 P0-2 修复）

    修复前：mainline_match 在 api_search_main.py 中初始化为 None 后从未被赋值，
    return mainline_match or other_match 等价于 return other_match。
    修复后：mainline_match 变量已删除，直接 return other_match。
    """

    def test_verify_mainline_match_removed(self) -> None:
        """D-1: mainline_match 变量已从 api_search_main.py 中删除"""
        filepath = _APP_ROOT / "services" / "matching" / "steps" / "api_search_main.py"
        tree = _parse_ast(filepath)
        assignments: list[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "mainline_match":
                        assignments.append(node)
            elif isinstance(node, ast.AugAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "mainline_match"
                ):
                    assignments.append(node)
        # 修复后 mainline_match 变量应完全删除，无任何赋值
        assert len(assignments) == 0, (
            f"expected 0 assignments (removed), got {len(assignments)}"
        )

    def test_verify_mainline_match_not_in_return(self) -> None:
        """D-1: return 语句中不再包含 mainline_match"""
        filepath = _APP_ROOT / "services" / "matching" / "steps" / "api_search_main.py"
        content = filepath.read_text(encoding="utf-8")
        # 修复后 return 语句应为 return other_match
        assert "return mainline_match or other_match" not in content
        assert "return other_match" in content


class TestVerifyD02DeadTernaryLogic:
    """D-2: ``None, None if target_ep else None`` 死逻辑（随 P0-3 验证）

    episodes.py 中 line 252、542 的 ``return None, None if target_ep else None``
    是死逻辑：条件表达式两分支都返回 None，无论 target_ep 真假都返回 (None, None)。
    """

    def test_verify_dead_ternary_in_episodes(self) -> None:
        """D-2: 死逻辑表达式在 episodes.py 中出现 2 次（line 252, 542）"""
        filepath = _APP_ROOT / "utils" / "bangumi_api" / "episodes.py"
        content = filepath.read_text(encoding="utf-8")
        count = content.count("None, None if target_ep else None")
        assert count == 2, f"expected 2 occurrences (lines 252, 542), got {count}"


class TestVerifyD03TrySearchOldDeprecated:
    """D-3: try_search_old 已弃用方法无生产调用

    _archive_shortcut.py line 568 定义了 try_search_old，标注"已弃用"，
    内部直接委托 try_search。app/ 下无任何生产代码调用该方法。
    """

    def test_verify_try_search_old_no_production_callers(self) -> None:
        """D-3: app/ 下无 try_search_old 的属性引用（无生产调用）"""
        references: list[Path] = []
        for f in _collect_python_files(_APP_ROOT):
            tree = _parse_ast(f)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "try_search_old":
                    references.append(f.relative_to(_APP_ROOT))
        # 无生产代码引用 try_search_old
        # （只有定义本身，定义是 FunctionDef 不是 Attribute）
        assert len(references) == 0, (
            f"try_search_old referenced in production: {references}"
        )

    def test_verify_try_search_old_defined_as_deprecated(self) -> None:
        """D-3: try_search_old 在 _archive_shortcut.py 中有定义（标注已弃用）"""
        from app.utils.bangumi_api._archive_shortcut import ArchiveShortcut

        # 方法存在（已弃用，保留签名）
        assert hasattr(ArchiveShortcut, "try_search_old")
        # 内部直接委托 try_search（不执行实际逻辑）
        assert callable(ArchiveShortcut.try_search_old)


class TestVerifyD04EmptyProperties:
    """D-4: _title_to_ids / _bigram_index 空属性无生产引用

    _title_index.py line 52-60 定义了 _title_to_ids / _bigram_index 两个
    空属性（FTS5 方案下无内存索引）。app/ 下无生产代码访问这两个属性。
    """

    def test_verify_title_to_ids_returns_empty(self) -> None:
        """D-4: archive_title_index._title_to_ids 返回空 dict"""
        from app.utils.bangumi_archive._title_index import archive_title_index

        assert archive_title_index._title_to_ids == {}

    def test_verify_bigram_index_returns_empty(self) -> None:
        """D-4: archive_title_index._bigram_index 返回空 dict"""
        from app.utils.bangumi_archive._title_index import archive_title_index

        assert archive_title_index._bigram_index == {}

    def test_verify_no_production_access(self) -> None:
        """D-4: app/ 下无生产代码访问 _title_to_ids / _bigram_index 属性"""
        references: list[tuple[Path, str]] = []
        for f in _collect_python_files(_APP_ROOT):
            tree = _parse_ast(f)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in (
                    "_title_to_ids",
                    "_bigram_index",
                ):
                    references.append((f.relative_to(_APP_ROOT), node.attr))
        # 无生产代码访问
        # （只有定义在 _title_index.py，定义是 FunctionDef 不是 Attribute）
        assert len(references) == 0, (
            f"_title_to_ids/_bigram_index accessed in production: {references}"
        )


class TestVerifyD05FallbackSearchLimitUnreferenced:
    """D-5: search.py 的 FALLBACK_SEARCH_LIMIT 无引用

    search.py line 20 定义了 FALLBACK_SEARCH_LIMIT = 15，注释说"实际使用
    已迁移至 api_search step"。app/ 下无任何模块通过 import 引用
    search.py 的 FALLBACK_SEARCH_LIMIT；api_search.py 有独立的同名常量被引用。
    """

    def test_verify_no_import_of_fallback_search_limit(self) -> None:
        """D-5: app/ 下无 ImportFrom 导入 FALLBACK_SEARCH_LIMIT"""
        importers: list[tuple[Path, str | None]] = []
        for f in _collect_python_files(_APP_ROOT):
            tree = _parse_ast(f)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "FALLBACK_SEARCH_LIMIT":
                            importers.append((f.relative_to(_APP_ROOT), node.module))
        # 无任何模块通过 import 导入 FALLBACK_SEARCH_LIMIT
        # （search.py 和 api_search.py 各自定义，不从对方导入）
        assert len(importers) == 0, f"FALLBACK_SEARCH_LIMIT imported by: {importers}"

    def test_verify_search_py_defines_fallback_limit(self) -> None:
        """D-5: search.py 中定义了 FALLBACK_SEARCH_LIMIT（但无外部引用）"""
        from app.utils.bangumi_api.search import FALLBACK_SEARCH_LIMIT

        assert FALLBACK_SEARCH_LIMIT == 15

    def test_verify_api_search_has_own_fallback_limit(self) -> None:
        """D-5: api_search.py 有独立的 FALLBACK_SEARCH_LIMIT 定义并被引用"""
        from app.services.matching.steps.api_search import FALLBACK_SEARCH_LIMIT

        assert FALLBACK_SEARCH_LIMIT == 15
