"""将 Release Markdown 转为可安全插入页面的 HTML（bleach 清洗）。"""

from __future__ import annotations

import re

import bleach
import markdown
from bleach import sanitizer

_EXTRA_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "pre",
        "code",
        "hr",
        "blockquote",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "div",
        "span",
        "del",
        "ins",
        "img",
        "sup",
        "sub",
        "input",
    }
)
_ALLOWED_TAGS = sanitizer.ALLOWED_TAGS | _EXTRA_TAGS


def _strip_leading_changes_heading(raw: str) -> str:
    """去掉文首常见的「## Changes」标题行（含其后的空行）。"""
    s = raw.lstrip("\ufeff")
    return re.sub(
        r"\A\s*#{1,2}\s*Changes\s*\n+",
        "",
        s,
        count=1,
        flags=re.IGNORECASE,
    )


_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    **{k: list(v) for k, v in sanitizer.ALLOWED_ATTRIBUTES.items()},
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height"],
    "th": ["align", "colspan", "rowspan"],
    "td": ["align", "colspan", "rowspan"],
    "code": ["class"],
    "span": ["class"],
    "div": ["class"],
    "ul": ["class"],
    "li": ["class"],
    "input": ["type", "checked", "disabled", "class"],
}


def markdown_to_safe_html(text: str | None) -> str:
    """Markdown → HTML → bleach 白名单过滤。"""
    raw = _strip_leading_changes_heading((text or "").strip())
    if not raw:
        return ""
    html = markdown.markdown(
        raw,
        extensions=[
            "pymdownx.tasklist",
            "fenced_code",
            "tables",
            "nl2br",
            "sane_lists",
        ],
        extension_configs={
            "pymdownx.tasklist": {
                "clickable_checkbox": False,
                "custom_checkbox": False,
            }
        },
        output_format="html",
    )
    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )
