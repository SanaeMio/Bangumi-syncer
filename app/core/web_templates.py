"""
全局 Jinja2 配置：子路径过滤器 p、公开前缀注入等。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .app_version import get_version
from .public_url import get_public_base_path, join_public

_templates: Jinja2Templates | None = None


def _templates_dir() -> str:
    return str(Path(__file__).resolve().parent.parent.parent / "templates")


def get_templates() -> Jinja2Templates:
    global _templates
    if _templates is None:
        _templates = Jinja2Templates(directory=_templates_dir())
        env = _templates.env
        env.filters["p"] = join_public
        _ver = get_version()

        def _static_v(path: str) -> str:
            base = join_public(path)
            sep = "&" if "?" in base else "?"
            return f"{base}{sep}v={_ver}"

        env.filters["static_v"] = _static_v
        env.globals["get_public_base_path"] = get_public_base_path

        def public_base_path_json() -> Markup:
            return Markup(json.dumps(get_public_base_path()))

        env.globals["public_base_path_json"] = public_base_path_json

        # 注入 archive_enabled 函数供 base.html 侧边栏条件显示（避免在每个页面 context 中显式传递）
        def archive_enabled() -> bool:
            try:
                from ..utils.bangumi_archive import bangumi_archive

                return bool(bangumi_archive.enabled)
            except Exception:
                return False

        env.globals["archive_enabled"] = archive_enabled

        # 注入 replay_enabled 函数供 base.html 侧边栏条件显示
        # 规则与 is_replay_enabled 一致：[bangumi-replay] enabled 非 false（与 archive 解耦）
        def replay_enabled() -> bool:
            try:
                from ..utils.bangumi_api.collection import is_replay_enabled

                return bool(is_replay_enabled())
            except Exception:
                return False

        env.globals["replay_enabled"] = replay_enabled
    return _templates
