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
# 紧贴标题末尾的季号：``XXX2`` / ``XXXⅡ``
#
# Bangumi 有条目把续作写成「标题+数字」而**不写**「第N季」字样，实测
# ``笨蛋，测验，召唤兽2``（11145，第二季）。这类命名的特征是数字直接**紧贴**
# 在标题本体后面：
# - 不能允许空格分隔 —— ``機動戦士ガンダム0083``（作品编号）、
#   ``コードギアス 亡国のアキト 第3章「…」``（章号）之类会被误判成季号，
#   实测放宽后 L1 黄金集 28 条错配（把短标题条目挤出榜首）；
# - 数字前必须已有标题本体（``86`` 这种整名是数字的标题不算）。
_SEASON_TRAILING_RE = re.compile(r"[^\d\s]\s*(\d{1,2})(?![\d])$")
# 罗马数字季号（``偶像大师 II`` / ``灰与幻想的格林姆迦尔Ⅲ``）
#
# 前置字符不能是罗马数字或拉丁字母（避免 ``AIV`` 之类），但允许空格、标点，
# 也允许直接紧贴汉字（``格林姆迦尔Ⅲ``）。用负向后顾而非「吞一个字符」：
# 后者在 ``偶像大师 II``（数字前有空格）时会吃掉空格，导致 $ 落空。
_SEASON_ROMAN_RE = re.compile(
    r"(?<![\u2160-\u216BA-Za-z])([\u2160-\u216B]|II|III|IV|V|VI)$"
)
_ROMAN_VALUES = {
    "Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5, "Ⅵ": 6, "Ⅶ": 7,
    "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10, "Ⅺ": 11, "Ⅻ": 12,
    "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
}  # fmt: skip


def _trailing_season(text: str) -> int | None:
    """标题**结尾紧贴**的季号（``笨蛋，测验，召唤兽2`` → 2；``86`` → None）

    只认尾部数字/罗马数字，且必须紧贴标题本体（不隔空格），避免把作品编号
    （``ガンダム0083``）与章号（``第3章``）当成季号。
    """
    stripped = text.rstrip()
    m = _SEASON_ROMAN_RE.search(stripped)
    if m:
        return _ROMAN_VALUES.get(m.group(1))
    m = _SEASON_TRAILING_RE.search(stripped)
    if m:
        n = int(m.group(1))
        # 只接受「像季号」的值（2-12）；0083 这类多位编号已由上界排除
        if 2 <= n <= 12:
            return n
    return None


def extract_explicit_season(title: str) -> int | None:
    """从标题中提取明确声明的季度编号。

    返回值：
    - 明确声明第 N 季时返回 N（>=1）
    - 标题不含季度声明时返回 None（可能是第一季本体，也可能是总集篇等）

    覆盖形式：第 X 季 / 第 X 期（阿拉伯与中文数字）、Xnd/Xrd/Xth season、Season X，
    以及**标题结尾紧贴的季号**（``笨蛋，测验，召唤兽2`` → 2、``XXX II`` → 2）。

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
    # 标题结尾的季号（无「第N季」字样）
    return _trailing_season(text)


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

    trailing = _trailing_season(text)
    if trailing is not None:
        found.add(trailing)

    return found
