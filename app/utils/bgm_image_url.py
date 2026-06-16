"""Bangumi 封面图 URL 工具（lain.bgm.tv 反代改写）。"""

from __future__ import annotations

import hashlib
from typing import Any

LAIN_BGM_TV_PREFIX = "https://lain.bgm.tv"

_POSTER_SIZE_ORDER = ("large", "medium", "common", "small", "grid")


def build_poster_cache_namespace(
    bgm_api_proxy: str = "", bgm_image_proxy: str = ""
) -> str:
    """根据 Bangumi API / 图片反代配置生成封面 localStorage 命名空间。"""
    raw = f"{(bgm_api_proxy or '').strip()}\n{(bgm_image_proxy or '').strip()}"
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:12]


def extract_poster_url(subject: dict[str, Any]) -> str | None:
    """从 get_subject 响应中选取最佳可用封面 URL。"""
    images = subject.get("images")
    if not isinstance(images, dict):
        return None
    for key in _POSTER_SIZE_ORDER:
        url = images.get(key)
        if url:
            return str(url)
    return None


def rewrite_bgm_image_url(url: str, image_proxy: str) -> str:
    """将 lain.bgm.tv 前缀替换为图片反代根地址；留空则原样返回。"""
    if not url or not (image_proxy or "").strip():
        return url
    proxy_base = image_proxy.strip().rstrip("/")
    if url.startswith(LAIN_BGM_TV_PREFIX):
        return proxy_base + url[len(LAIN_BGM_TV_PREFIX) :]
    return url
