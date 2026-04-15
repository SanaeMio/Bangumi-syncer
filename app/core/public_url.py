"""
对外 URL 路径前缀（子路径反向代理）工具。
"""

from __future__ import annotations

import os

from fastapi.responses import RedirectResponse


def normalize_public_base_path(raw: str) -> str:
    """规范化前缀：空、无尾斜杠、以 / 开头、URL 内统一为正斜杠。"""
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.replace("\\", "/")
    while len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    if not s.startswith("/"):
        s = "/" + s
    return s


def get_public_base_path() -> str:
    """
    当前对外路径前缀（不含尾斜杠）。
    优先环境变量 APPLICATION_ROOT 或 BASE_PATH，其次 config.ini [web] base_path。
    """
    env_raw = (
        os.environ.get("APPLICATION_ROOT") or os.environ.get("BASE_PATH") or ""
    ).strip()
    if env_raw:
        return normalize_public_base_path(env_raw)
    try:
        from .config import config_manager

        ini = config_manager.get("web", "base_path", fallback="") or ""
        return normalize_public_base_path(str(ini).strip())
    except Exception:
        return ""


def join_public(path: str) -> str:
    """
    将必须以 / 开头的站内路径（可含 query）拼到公开前缀之后。
    """
    if not path:
        base = get_public_base_path()
        return base if base else "/"
    path = path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    base = get_public_base_path()
    if not base:
        return path
    return base + path


def redirect_public(path: str, status_code: int = 302) -> RedirectResponse:
    return RedirectResponse(url=join_public(path), status_code=status_code)
