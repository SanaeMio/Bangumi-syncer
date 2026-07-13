@echo off
chcp 65001 >nul
REM 安装 pre-commit hook（设置 git core.hooksPath 指向 .githooks）
REM 适用于 Windows（无需 bash）

setlocal

REM 切换到仓库根目录（脚本所在目录的上一级）
cd /d "%~dp0\.."

REM 设置 core.hooksPath
git config core.hooksPath .githooks
if errorlevel 1 (
    echo ❌ 设置 core.hooksPath 失败，请确认已安装 git
    exit /b 1
)

echo ✅ 已安装 git hooks（core.hooksPath = .githooks）
echo.
echo 提交时将自动执行:
echo   1. ruff check（仅暂存的 .py 文件）
echo   2. ruff format --check
echo   3. pytest tests/（全量单元测试）
echo   4. config.ini 污染自动恢复
echo.
echo 如需跳过（紧急情况）: git commit --no-verify
echo 如需加速测试: set PRECOMMIT_TEST_ARGS=-x tests/services ^&^& git commit

endlocal
