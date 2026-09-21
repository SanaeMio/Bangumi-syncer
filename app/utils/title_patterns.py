"""跨模块共享的标题类正则（单一 compiled 单源）。

职责边界：
- 只收「两个以上模块同目的使用」的 compiled 正则与纯函数
- 各输入域私有的正则不收：日志解析（log_grouping）、FTS 键归一化（_fts_query
  的 NFKC 链）、括号种类变体（_DECOR_BRACKET_RE 等）
- ⚠ 语义有意的跨域差异【勿合并】：fongmi 季号"仅 N>1"（方案 B 策略，见
  fongmi/client.py 头注）、SxxExx 两侧不同的锚定/位数（fongmi 解析用户文件名、
  log_grouping 解析自家日志）、括号剥离三种各自的括号集合
"""

from __future__ import annotations

import re

# 4 位年份（19xx/20xx）：
# - _fts_query: 年份消歧（从 date 字符串抽取 / 从查询标题抽取并剥离）
# - _archive_shortcut: 按放送年份过滤候选
YEAR_RE = re.compile(r"(?:19|20)\d{2}")

# 季度标记剥离（尾部锚定，按优先级排序）：
# bangumi_data/matching.find_bangumi_id 在 season>1 时用这组模式
# 把「XX 第2季 / XX Season 2 / XX 2期12話 / XX II」剥回第一季核心标题。
# 模式集合与 _title_normalize._SEASON_EPISODE_PATTERNS（调用方脏标题清洗）
# 有意不同：本组面向 bangumi-data 侧的标题（第N期[話话集] 后缀等），勿合并。
SEASON_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*[第]?\s*\d+\s*期?[話话集]?$"),
    re.compile(r"\s*Season\s*\d+$", re.IGNORECASE),
    re.compile(r"\s*S\d+$", re.IGNORECASE),
    re.compile(r"\s*\d+$"),
    re.compile(r"\s*II+$"),
    re.compile(r"\s*[第]?\s*\d+\s*[期季]$"),
)


def extract_year(text: str) -> int | None:
    """从字符串抽取 4 位年份（19xx/20xx），无则返回 None。"""
    if not text:
        return None
    m = YEAR_RE.search(text)
    return int(m.group(0)) if m else None
