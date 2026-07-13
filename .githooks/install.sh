#!/usr/bin/env bash
# 安装 pre-commit hook（设置 git core.hooksPath 指向 .githooks）
# 适用于 Linux / macOS / Git for Windows（bash）

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$(cd "$(dirname "$0")/.." && pwd)")"
cd "$REPO_ROOT"

HOOKS_DIR="$REPO_ROOT/.githooks"

if [ ! -d "$HOOKS_DIR" ]; then
    echo "❌ 未找到 .githooks 目录: $HOOKS_DIR"
    exit 1
fi

# 设置 core.hooksPath
git config core.hooksPath .githooks

# 确保钩子可执行（Unix / Git for Windows）
chmod +x "$HOOKS_DIR"/* 2>/dev/null || true

echo "✅ 已安装 git hooks（core.hooksPath = .githooks）"
echo ""
echo "钩子列表:"
ls -1 "$HOOKS_DIR" | sed 's/^/  - /'
echo ""
echo "提交时将自动执行:"
echo "  1. ruff check（仅暂存的 .py 文件）"
echo "  2. ruff format --check"
echo "  3. pytest tests/（全量单元测试）"
echo "  4. config.ini 污染自动恢复"
echo ""
echo "如需跳过（紧急情况）: git commit --no-verify"
echo "如需加速测试: PRECOMMIT_TEST_ARGS=\"-x tests/services\" git commit"
