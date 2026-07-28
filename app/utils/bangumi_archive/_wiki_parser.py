"""Bangumi wiki infobox 解析器

将 Archive 存储的原始 wiki 串（如 `{{Infobox|key=value|...}}`）
解析为与 BangumiApi 返回结构兼容的 `list[dict]` 格式。

设计原则：
- 容错优先：格式异常时返回已解析部分，不抛异常
- 仅处理 Infobox 模板，其他模板/纯文本原样返回空列表
- 列表值识别：bullet list (`* item`)、`{{list|...}}`/`{{ll|...}}`、`<br>` 分隔

参考：
- Bangumi Archive README：infobox 为「条目原始 wiki 字符串」
- BangumiApi `/v0/subjects/{id}` 返回 infobox 为 list[{"key":..., "value":...}]
- 现有匹配逻辑（search.py title_diff_ratio）按 key="别名" 提取别名候选
"""

from __future__ import annotations

import re
from typing import Any

# Infobox 模板起始（不区分大小写，支持 Infobox/infobox）
_INFOBOX_RE = re.compile(r"^\s*\{\{\s*[Ii]nfobox\b", re.DOTALL)

# 列表项前缀（* item 或 ** item），按行匹配
_BULLET_LINE_RE = re.compile(r"^\s*\*+\s*(.+?)\s*$", re.MULTILINE)

# {{list|...}} / {{ll|...}} 模板参数（参数分隔符 | 不嵌套其他模板）
_LIST_TEMPLATE_RE = re.compile(
    r"^\s*\{\{\s*(?:list|ll)\s*((?:\|[^{}]*?)+)\}\}\s*$",
    re.IGNORECASE,
)

# <br> / <br/> / <br /> 分隔
_BR_SPLIT_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)

# wiki 链接 [[target|显示文本]] 或 [[target]]
# 捕获整段内部内容（含 |），由调用方 split 取显示文本
_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


def parse_infobox(text: str) -> list[dict[str, Any]]:
    """解析 wiki infobox 模板字符串为 API 兼容格式

    输入示例：
        "{{Infobox|alias=Test|中文名=测试|别名=* a1\\n* a2}}"

    输出示例：
        [
            {"key": "alias", "value": "Test"},
            {"key": "中文名", "value": "测试"},
            {"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]},
        ]

    解析失败或非 infobox 模板时返回空列表。
    """
    if not isinstance(text, str) or not text:
        return []

    # 仅处理 Infobox 模板（其他模板/纯文本返回空）
    if not _INFOBOX_RE.match(text):
        return []

    # 提取最外层 {{...}} 内容（处理嵌套大括号）
    content = _extract_template_content(text)
    if content is None:
        return []

    # content 形如 "Infobox|key1=v1|key2=v2"，去掉模板名部分
    pipe_idx = content.find("|")
    if pipe_idx == -1:
        return []  # 无参数

    params_str = content[pipe_idx + 1 :]

    # 按顶层 | 分割参数（不进入嵌套 {{...}}）
    pairs = _split_top_level_params(params_str)

    result: list[dict[str, Any]] = []
    for pair in pairs:
        eq_idx = pair.find("=")
        if eq_idx == -1:
            # 无 = 的参数（如类型修饰符 anime），跳过
            continue
        key = pair[:eq_idx].strip()
        value = pair[eq_idx + 1 :].strip()
        if not key:
            continue
        parsed_value = _parse_value(value)
        result.append({"key": key, "value": parsed_value})

    return result


def _extract_template_content(text: str) -> str | None:
    """提取最外层 {{...}} 的内部内容

    处理嵌套：`{{outer|{{inner|...}}|key=v}}` 的内部内容是
    `outer|{{inner|...}}|key=v`

    找不到匹配的 `}}` 时返回 None。
    """
    start = text.find("{{")
    if start == -1:
        return None

    depth = 0
    i = start
    n = len(text)
    while i < n - 1:
        two = text[i : i + 2]
        if two == "{{":
            depth += 1
            i += 2
        elif two == "}}":
            depth -= 1
            if depth == 0:
                return text[start + 2 : i]
            i += 2
        else:
            i += 1
    return None  # 没有匹配的 }}


def _split_top_level_params(s: str) -> list[str]:
    """按顶层 `|` 分割参数，不进入嵌套 `{{...}}` 或 `[[...]]`

    输入: `"key1=v1|key2={{list|a|b}}|key3=[[link|display]]"`
    输出: `["key1=v1", "key2={{list|a|b}}", "key3=[[link|display]]"]`
    """
    parts: list[str] = []
    brace_depth = 0  # {{...}}
    bracket_depth = 0  # [[...]]
    current: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        two = s[i : i + 2]
        if two == "{{":
            brace_depth += 1
            current.append(two)
            i += 2
        elif two == "}}":
            brace_depth -= 1
            current.append(two)
            i += 2
        elif two == "[[":
            bracket_depth += 1
            current.append(two)
            i += 2
        elif two == "]]":
            bracket_depth -= 1
            current.append(two)
            i += 2
        elif s[i] == "|" and brace_depth == 0 and bracket_depth == 0:
            parts.append("".join(current))
            current = []
            i += 1
        else:
            current.append(s[i])
            i += 1
    if current:
        parts.append("".join(current))
    return parts


def _parse_value(value: str) -> str | list[dict[str, str]]:
    """解析 value，识别列表并转为 API 兼容格式

    识别规则（按优先级）：
    1. bullet list (`* a\\n* b`) → `[{"v": "a"}, {"v": "b"}]`
    2. `{{list|a|b}}` / `{{ll|a|b}}` 模板 → `[{"v": "a"}, {"v": "b"}]`
    3. `<br>` 分隔（至少 2 个非空项）→ `[{"v": "a"}, {"v": "b"}]`
    4. 其他 → 清理 wiki 标记后的字符串

    单个 bullet 项也视为列表（保持与 API 列表字段一致的返回结构）。
    """
    if not value:
        return ""

    # 1. bullet list: * item1\n* item2
    bullets = _BULLET_LINE_RE.findall(value)
    if bullets:
        cleaned = [_clean_wiki_markup(b) for b in bullets]
        cleaned = [c for c in cleaned if c]
        if cleaned:
            return [{"v": c} for c in cleaned]

    # 2. {{list|...}} / {{ll|...}} 模板
    m = _LIST_TEMPLATE_RE.match(value)
    if m:
        # m.group(1) 形如 "|a|b|c"，split 后去掉首部空串
        items = m.group(1).split("|")[1:]
        cleaned = [_clean_wiki_markup(x.strip()) for x in items if x.strip()]
        if cleaned:
            return [{"v": x} for x in cleaned]

    # 3. <br> 分隔（至少 2 个非空项才视为列表）
    if _BR_SPLIT_RE.search(value):
        parts = _BR_SPLIT_RE.split(value)
        cleaned = [_clean_wiki_markup(p.strip()) for p in parts if p.strip()]
        if len(cleaned) >= 2:
            return [{"v": x} for x in cleaned]
        # 单项时 fallthrough 到普通字符串，但需先去除 <br> 标记

    # 4. 普通字符串（清理 wiki 标记 + 残留 <br> 标签）
    cleaned = _clean_wiki_markup(value)
    if "<br" in cleaned.lower():
        cleaned = _BR_SPLIT_RE.sub("", cleaned).strip()
    return cleaned


def _clean_wiki_markup(text: str) -> str:
    """清理 wiki 标记，提取纯文本

    - `[[link|显示文本]]` → `显示文本`（或 link，取 `|` 后部分）
    - `[[link]]` → `link`
    - `'''bold'''` → `bold`
    - `''italic''` → `italic`
    - 去除首尾空白
    """
    if not text:
        return ""
    # [[link|显示]] → 显示（取 | 后部分，无 | 则取整个 link）
    # group(1) 捕获整段内部内容（如 "target|display"），split 取最后一段
    text = _WIKI_LINK_RE.sub(lambda m: m.group(1).split("|")[-1].strip(), text)
    # 去除 bold/italic 标记
    text = text.replace("'''", "").replace("''", "")
    return text.strip()
