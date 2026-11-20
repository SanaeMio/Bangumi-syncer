"""跨模块共享的标题类正则（单一 compiled 单源）。

## 新增正则该放哪（决策顺序）

1. **两个以上模块、同一目的** → 放这里。跨域差异靠「片段」表达（见下），
   不要让调用方各自复制一份字面量。
2. **只有一个模块用** → 留在该模块，但提成命名常量（``_XXX_RE``）。
   搬运本身不产生价值，反而让读代码的人多跳一次文件。
3. **输入域私有** → 一律留在原模块：日志解析（``log_grouping``）、FTS 键归一化
   （``_fts_query`` 的 NFKC 链）、archive 深度归一（``_title_normalize``）。
   它们的正则与"标题"共享词汇，但不共享**判据**。
4. **语义有意的跨域差异** → 禁止合并，用片段拼装表达：
   - 括号集合三种（文件名 / archive 深度归一 / 噪声清理），各自集合见注释；
   - ``SxxExx`` 两侧锚定与位数不同（fongmi 解析用户文件名、log_grouping 解析
     自家日志）；
   - fongmi 季号"仅 N>1"（方案 B 策略，见 ``fongmi/client.py`` 头注）。
5. **季/部后缀剥离** → 一律去 :mod:`app.utils.season_suffix`。四套输入域的
   模式集合与剥离流程在那里并排维护，本模块不再持有季后缀模式。

## 为什么是「片段」而不是「全部 compiled」

同一个词元在不同域需要不同外壳：年份在 FTS 里是裸的、在标题清洗里要连括号
一起剥、在文件名里要带边界。共享 compiled 对象做不到，共享**片段**可以 ——
各域用 ``re.compile(rf"...{TOKEN}...")`` 自编译，词元只有一份。
"""

from __future__ import annotations

import re

# 4 位年份片段（19xx/20xx）。调用方按需加外壳，勿各自重写 ``(?:19|20)\d{2}``：
# - _fts_query / _archive_shortcut: 裸年份（本文件的 YEAR_RE）
# - _title_normalize._DECOR_YEAR_RE: 连全半角括号一起剥
# - fongmi._MOVIE_YEAR_RE: 要求前后是文件名分隔符（避免 12020 这类串）
YEAR_TOKEN = r"(?:19|20)\d{2}"

# 4 位年份（裸）：年份消歧与按年过滤用
YEAR_RE = re.compile(YEAR_TOKEN)

# 连续空白。**只共享模式，不共享替换值** —— 标题归一并为一个空格，archive
# 深度归一直接删空（``_title_normalize``），两者语义不同。
WHITESPACE_RE = re.compile(r"\s+")

# 季度标记剥离不在这里：四套输入域（bangumi-data / 调用方脏标题 / TMDB 季标题 /
# 文件名）的模式集合有意不同，已集中登记在 ``app.utils.season_suffix``，调用方
# 按 scope 取用，勿再各写一份模式。


def extract_year(text: str) -> int | None:
    """从字符串抽取 4 位年份（19xx/20xx），无则返回 None。"""
    if not text:
        return None
    m = YEAR_RE.search(text)
    return int(m.group(0)) if m else None
