"""季编号解析纯函数（无外部依赖，可被 bangumi_api 层复用）。

从条目标题中提取明确声明的季度编号，供跨季选集时定位目标季条目。
"""

from __future__ import annotations

import re

from .text_constants import CN_NUM

# 季号模式（模块级预编译，避免每次调用重编译）
_SEASON_NUM_RE = re.compile(r"第\s*(\d+)\s*[期季]")
_SEASON_CN_RE = re.compile(r"第\s*([一二三四五六七八九十]+)\s*[期季]")
_SEASON_ORDINAL_RE = re.compile(r"(\d+)(?:st|nd|rd|th)\s+season", re.IGNORECASE)
_SEASON_WORD_RE = re.compile(r"season\s*(\d+)", re.IGNORECASE)


def extract_explicit_season(title: str) -> int | None:
    """从标题中提取明确声明的季度编号。

    返回值：
    - 明确声明第 N 季时返回 N（>=1）
    - 标题不含季度声明时返回 None（可能是第一季本体，也可能是总集篇等）

    覆盖形式：第 X 季 / 第 X 期（阿拉伯与中文数字）、Xnd/Xrd/Xth season、Season X

    注意：标题里若出现**多个互相矛盾**的季号（如既有 "Season 1" 又有
    "第二季"，真实媒体库存在这种脏数据），本函数按模式优先级返回**首个**
    匹配。需要判断「季号是否可信」的场景请用 :func:`extract_season_candidates`。
    """
    if not title:
        return None
    text = title.strip()

    # "第X期" / "第X季"（阿拉伯数字）
    m = _SEASON_NUM_RE.search(text)
    if m:
        return int(m.group(1))
    # "第X期" / "第X季"（中文数字，含"十一"~"十九"）
    m = _SEASON_CN_RE.search(text)
    if m:
        cn = m.group(1)
        if len(cn) == 1:
            return CN_NUM.get(cn)
        if cn.startswith("十"):
            return 10 + CN_NUM.get(cn[1], 0)
        return CN_NUM.get(cn)
    # "Xnd/Xrd/Xth season"
    m = _SEASON_ORDINAL_RE.search(text)
    if m:
        return int(m.group(1))
    # "Season X"（需带数字，避免误匹配"Season"单词本身）
    m = _SEASON_WORD_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def extract_season_candidates(title: str) -> set[int]:
    """提取标题中出现的**全部**季号（去重）。

    与 :func:`extract_explicit_season` 的区别：后者只返回首个匹配，本函数把
    所有模式的所有匹配都收集起来，供调用方判断「季号是否唯一可信」。

    为什么需要：真实媒体库标题可能同时含矛盾季号，例如
    ``"Marvel's Guardians of the Galaxy Season 1 第二季"``（媒体库把英文原名
    与中文季名拼在一起）。此时首个匹配给出 2，但该条目其实是第一季。
    仅凭单一返回值做「季号不等则扣分」的判断会误伤，故需要看到全貌。

    Returns:
        标题中出现的季号集合；无季号时返回空集合。
    """
    if not title:
        return set()
    text = title.strip()
    found: set[int] = set()

    for m in _SEASON_NUM_RE.finditer(text):
        found.add(int(m.group(1)))

    for m in _SEASON_CN_RE.finditer(text):
        cn = m.group(1)
        if len(cn) == 1:
            v = CN_NUM.get(cn)
        elif cn.startswith("十"):
            v = 10 + CN_NUM.get(cn[1], 0)
        else:
            v = CN_NUM.get(cn)
        if v is not None:
            found.add(v)

    for m in _SEASON_ORDINAL_RE.finditer(text):
        found.add(int(m.group(1)))

    for m in _SEASON_WORD_RE.finditer(text):
        found.add(int(m.group(1)))

    return found
