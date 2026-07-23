"""
标题归一化与候选排序 Mixin：匹配前的标题预处理 + top-N platform 加权排序。

纯函数逻辑，不依赖任何单例。
"""

from __future__ import annotations

import re
from typing import Any

from ...core.logging import logger

# 各放送形态在「非剧场版」场景下的权重（数值越大越优先）
_PLATFORM_WEIGHT_TV_MODE = {
    "TV": 100,
    "WEB": 90,
    "OVA": 80,
    "OAD": 80,
    "剧场版": 70,
    "电影": 70,
    "日剧": 60,
    "欧美剧": 60,
}

# 各放送形态在「剧场版」场景下的权重（剧场版/电影优先）
_PLATFORM_WEIGHT_MOVIE_MODE = {
    "剧场版": 100,
    "电影": 100,
    "OVA": 80,
    "OAD": 80,
    "TV": 70,
    "WEB": 70,
    "日剧": 60,
    "欧美剧": 60,
}

# 默认权重（未识别的 platform）
_DEFAULT_PLATFORM_WEIGHT = 50

# 释放组/分辨率/编码标记等噪声片段
_NOISE_PATTERNS = [
    # 方括号包裹的发布组/分辨率/编码标记：[ANi] [1080p] [HEVC] [x264] [10bit] [BD]
    re.compile(r"\[[^\[\]]*\]"),
    # 圆括号包裹的分辨率/编码标记：(1080p) (HEVC) (10bit)
    re.compile(r"\((?:1080|2160|720|480)[pP]?\)"),
    re.compile(r"\((?:HEVC|x264|x265|AVC|AAC|FLAC|10bit|BD|BDRip|WEBRip)\)"),
    # 裸露的分辨率/编码关键词
    re.compile(r"\b(?:1080p|2160p|720p|480p)\b", re.IGNORECASE),
    re.compile(r"\b(?:HEVC|x264|x265|AVC|AAC|FLAC|10bit)\b", re.IGNORECASE),
    re.compile(r"\b(?:BDRip|WEBRip|WEB-DL|BluRay|Blu-Ray)\b", re.IGNORECASE),
    # 文件扩展名残留
    re.compile(r"\.(?:mp4|mkv|avi|mov|flv|ts|m4v)$", re.IGNORECASE),
    # 帧率标记
    re.compile(r"\b(?:60fps|120fps|24fps|30fps)\b", re.IGNORECASE),
]

# 中文标点 → 半角/标准形式的映射
_PUNCTUATION_MAP = str.maketrans(
    {
        "：": ":",
        "；": ";",
        "，": ",",
        "。": ".",
        "？": "?",
        "！": "!",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "《": "<",
        "》": ">",
        "「": "'",
        "」": "'",
        "『": "'",
        "』": "'",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "～": "~",
        "・": "·",
        "･": "·",
        "—": "-",
        "–": "-",
        "―": "-",
    }
)

# 连续空白
_MULTI_SPACE = re.compile(r"\s+")


class TitleNormalizeMixin:
    """标题归一化与候选排序（纯逻辑，无外部依赖）。"""

    @staticmethod
    def normalize_title(title: str) -> str:
        """对标题进行归一化处理，去除噪声片段，标准化标点与空白。

        处理步骤：
        1. 中文标点 → 半角/标准形式
        2. 去除发布组/分辨率/编码等噪声片段
        3. 折叠连续空白为单个空格
        4. 去除首尾空白与首尾标点
        """
        if not title:
            return ""

        # 1. 中文标点归一化
        cleaned = title.translate(_PUNCTUATION_MAP)

        # 2. 去除噪声片段
        for pattern in _NOISE_PATTERNS:
            cleaned = pattern.sub(" ", cleaned)

        # 3. 折叠连续空白
        cleaned = _MULTI_SPACE.sub(" ", cleaned)

        # 4. 去除首尾空白与首尾常见标点
        cleaned = cleaned.strip().strip(" -_·.:;,").strip()

        if cleaned != title:
            logger.debug(f"标题归一化：{title!r} → {cleaned!r}")
        return cleaned

    @staticmethod
    def _sort_candidates_by_platform(
        candidates: list[dict[str, Any]],
        is_movie: bool = False,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """按 platform 加权排序候选列表，取 top-N。

        - is_movie=False：TV > WEB > OVA/OAD > 剧场版/电影 > 日剧 > 其他
        - is_movie=True：剧场版/电影 > OVA/OAD > TV/WEB > 日剧 > 其他
        - 同权重的候选项保持原始顺序（稳定排序）
        - 非列表输入原样返回（防御异常调用方）
        """
        if not isinstance(candidates, list) or not candidates:
            return candidates  # type: ignore[return-value]

        weight_table = (
            _PLATFORM_WEIGHT_MOVIE_MODE if is_movie else _PLATFORM_WEIGHT_TV_MODE
        )

        def weight(cand: dict[str, Any]) -> int:
            if not isinstance(cand, dict):
                return _DEFAULT_PLATFORM_WEIGHT
            platform = (cand.get("platform") or "").strip()
            return weight_table.get(platform, _DEFAULT_PLATFORM_WEIGHT)

        sorted_candidates = sorted(candidates, key=weight, reverse=True)
        return sorted_candidates[:limit] if limit > 0 else sorted_candidates
