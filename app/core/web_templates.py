"""
全局 Jinja2 配置：子路径过滤器 p、公开前缀注入等。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

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
        env.globals["get_public_base_path"] = get_public_base_path

        def public_base_path_json() -> Markup:
            return Markup(json.dumps(get_public_base_path()))

        env.globals["public_base_path_json"] = public_base_path_json
    return _templates
