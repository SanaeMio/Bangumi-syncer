"""将 Release Markdown 转为可安全插入页面的 HTML（bleach 清洗）。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import markdown
from bleach import sanitizer
from bleach.html5lib_shim import Filter

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


# 正文图片允许的主机：发行说明与公告的图片来自 GitHub 附件。
# github.com/user-attachments 会重定向到 S3 资产桶，浏览器的 CSP 校验包含
# 重定向目标，故三段主机都需要放行；GitHub 更换资产桶域名时需同步更新。
_CONTENT_IMG_HOSTS = (
    "github.com",
    "*.githubusercontent.com",
    "github-production-user-asset-6210df.s3.amazonaws.com",
)


def content_img_sources() -> list[str]:
    """CSP img-src 中放行正文图片的 host-source（与本模块的图片准入同一份名单）。"""
    return [f"https://{host}" for host in _CONTENT_IMG_HOSTS]


def _is_allowed_content_image(src: str) -> bool:
    """正文图片是否来自放行名单内的 HTTPS 主机。"""
    try:
        parsed = urlsplit(src)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    for pattern in _CONTENT_IMG_HOSTS:
        if pattern.startswith("*."):
            if host.endswith("." + pattern[2:]):
                return True
        elif host == pattern:
            return True
    return False


def _attr(data: dict, name: str) -> str:
    """按属性名取 html5lib 令牌中的值（键为 (命名空间, 属性名) 元组）。"""
    for key, value in (data or {}).items():
        if (key[1] if isinstance(key, tuple) else key) == name:
            return str(value or "").strip()
    return ""


class _ContentImageFilter(Filter):
    """正文图片准入：放行名单内的主机保留 <img> 并加原生懒加载属性，其余改为链接。

    名单与 CSP 的 img-src 同源，页面不会请求未放行的主机。
    """

    def __iter__(self):
        for token in super().__iter__():
            if token["type"] in ("StartTag", "EmptyTag") and token["name"] == "img":
                data = dict(token.get("data") or {})
                src = _attr(data, "src")
                alt = _attr(data, "alt") or "图片"
                if _is_allowed_content_image(src):
                    data[(None, "loading")] = "lazy"
                    data[(None, "decoding")] = "async"
                    yield dict(token, data=data)
                elif src.startswith(("http://", "https://")):
                    yield {
                        "type": "StartTag",
                        "name": "a",
                        "data": {
                            (None, "href"): src,
                            (None, "target"): "_blank",
                            (None, "rel"): "noopener noreferrer",
                        },
                    }
                    yield {"type": "Characters", "data": alt}
                    yield {"type": "EndTag", "name": "a"}
                continue
            yield token


_CLEANER = sanitizer.Cleaner(
    tags=_ALLOWED_TAGS,
    attributes=_ALLOWED_ATTRIBUTES,
    protocols=["http", "https", "mailto"],
    strip=True,
    filters=[_ContentImageFilter],
)


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
    return _CLEANER.clean(html)
