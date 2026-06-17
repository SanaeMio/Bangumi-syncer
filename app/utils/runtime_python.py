"""记录直装模式下实际运行应用的 Python 解释器路径。"""

from __future__ import annotations

import sys
from pathlib import Path

RUNTIME_PYTHON_FILE = Path("data/runtime_python.txt")


def persist_runtime_python() -> None:
    """将当前解释器路径写入 data/runtime_python.txt，供 start.bat 复用。"""
    RUNTIME_PYTHON_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_PYTHON_FILE.write_text(sys.executable, encoding="utf-8")
