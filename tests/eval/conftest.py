"""tests/eval 专用夹具：保证 CI 下可 ``import eval.lib``。

``eval/`` 是无 ``__init__.py`` 的命名空间包（非可安装 package），其可导入性
依赖仓库根进入 ``sys.path``。``pyproject.toml`` 已配置 ``pythonpath = "."``，
此处再兜底注入仓库根，避免从非根目录调用 pytest（如 IDE 单文件运行）时导入失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tests/eval/conftest.py -> parents[2] == 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def eval_dir() -> Path:
    """仓库内 ``eval/`` 目录。"""
    return EVAL_DIR


@pytest.fixture(scope="session")
def fixtures_dir(eval_dir: Path) -> Path:
    """cassette 目录（``eval/fixtures``）。"""
    return eval_dir / "fixtures"


@pytest.fixture(scope="session")
def golden_path(eval_dir: Path) -> Path:
    """golden 用例文件（``eval/golden/public_v1.jsonl``）。"""
    return eval_dir / "golden" / "public_v1.jsonl"
