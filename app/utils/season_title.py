"""季编号解析纯函数（无外部依赖，可被 bangumi_api 层复用）。

从条目标题中提取明确声明的季度编号，供跨季选集时定位目标季条目。
"""

from __future__ import annotations

import re

from .text_constants import CN_NUM


def extract_explicit_season(title: str) -> int | None:
    """从标题中提取明确声明的季度编号。

    返回值：
    - 明确声明第 N 季时返回 N（>=1）
    - 标题不含季度声明时返回 None（可能是第一季本体，也可能是总集篇等）

    覆盖形式：第 X 季 / 第 X 期（阿拉伯与中文数字）、Xnd/Xrd/Xth season、Season X
    """
    if not title:
        return None
    text = title.strip()

    # "第X期" / "第X季"（阿拉伯数字）
    m = re.search(r"第\s*(\d+)\s*[期季]", text)
    if m:
        return int(m.group(1))
    # "第X期" / "第X季"（中文数字，含"十一"~"十九"）
    m = re.search(r"第\s*([一二三四五六七八九十]+)\s*[期季]", text)
    if m:
        cn = m.group(1)
        if len(cn) == 1:
            return CN_NUM.get(cn)
        if cn.startswith("十"):
            return 10 + CN_NUM.get(cn[1], 0)
        return CN_NUM.get(cn)
    # "Xnd/Xrd/Xth season"
    m = re.search(r"(\d+)(?:st|nd|rd|th)\s+season", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # "Season X"（需带数字，避免误匹配"Season"单词本身）
    m = re.search(r"season\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None
