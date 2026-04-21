"""
运行时版本与应用元信息。

版本号优先级：环境变量 APP_VERSION > 项目根目录 release_manifest.json > 开发占位。
release_manifest.json 由 CI（Docker / Release zip）写入，字段示例：{"version": "3.8.0", "git_sha": "..."}

约定：
- **无 v**：`get_version()`、manifest、semver 比较、Docker/OpenAPI 语义版本字符串（便于与 tag 去 v 后一致）。
- **带 v**：`get_display_version()`、zip 文件名、页面展示用文案；若上游已写 ``vX`` 则不再重复加 ``v``。
- **默认占位**：未安装 manifest 时用 ``0.0.0.dev``，一眼区分「非 Release 构建」。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# 静态展示用（原 version.py 中非 semver 部分）
VERSION_NAME = "Bangumi-Syncer"
VERSION_DESCRIPTION = "自动同步Bangumi观看记录"
_DEV_PLACEHOLDER = "0.0.0.dev"
_MANIFEST_NAME = "release_manifest.json"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _read_manifest_version() -> str | None:
    path = _project_root() / _MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    v = data.get("version")
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def get_version() -> str:
    env = os.environ.get("APP_VERSION", "").strip()
    if env:
        return env
    mv = _read_manifest_version()
    if mv:
        return mv
    return _DEV_PLACEHOLDER


def get_display_version(version: str | None = None) -> str:
    """
    人读展示用版本号：无前导 ``v``/``V`` 时补一个，避免 ``v`` 重复。
    ``version`` 默认取 ``get_version()``。
    """
    s = (version if version is not None else get_version()).strip()
    if not s:
        return "v0.0.0.dev"
    if re.match(r"^[vV]\d", s):
        return s
    return f"v{s}"


def get_version_name() -> str:
    return VERSION_NAME


def get_full_name() -> str:
    return f"{VERSION_NAME} {get_display_version()}"


def get_version_info() -> dict[str, Any]:
    ver = get_version()
    disp = get_display_version(ver)
    return {
        "version": ver,
        "display_version": disp,
        "name": VERSION_NAME,
        "description": VERSION_DESCRIPTION,
        "full_name": f"{VERSION_NAME} {disp}",
    }
