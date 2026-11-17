"""infobox 别名抽取的单一实现。

同一份语义此前有三份拷贝，且键集合不一致：

- ``sync_service._extract_infobox_aliases``：只认「别名」
- ``bangumi_api.search`` 内联循环：只认「别名」
- ``bangumi_archive._fts_query._extract_alias_text``：认 6 个键（别名/alias/
  aliases/中文名/英文名/罗马音/假名）

三处的**数据形状**也不同：Bangumi API 给的是 ``[{"key": ..., "value": ...}]``，
archive 给的是需要 ``parse_infobox`` 解析的原始字符串。这里统一处理两种输入，
键集合由调用方传入——打分层沿用只认「别名」的历史行为，archive 索引用
``ALIAS_KEYS`` 扩大召回，两者的差异因此是显式参数而不是隐藏的拷贝。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .bangumi_archive._wiki_parser import parse_infobox

# archive 索引视为别名的 key（小写比较）。含中文名/英文名/罗马音/假名，
# 因为索引的目标是召回，而打分层只需要官方「别名」字段。
ALIAS_KEYS: frozenset[str] = frozenset(
    {"别名", "alias", "aliases", "中文名", "英文名", "罗马音", "假名"}
)

# 匹配打分默认只认「别名」，与迁移前的行为保持一致。
DEFAULT_ALIAS_KEYS: frozenset[str] = frozenset({"别名"})


def iter_infobox_aliases(
    infobox: Any, keys: frozenset[str] | set[str] = DEFAULT_ALIAS_KEYS
) -> Iterator[str]:
    """按顺序产出 infobox 中命中 ``keys`` 的别名原值。

    ``infobox`` 可以是 API 的 list 结构，也可以是 archive 的原始字符串；
    值为 list 时同时兼容 ``{"v": ...}`` 与裸字符串两种历史格式。
    非字符串值直接跳过（旧实现会带出 int 等类型，下游一律按标题处理）。
    """
    items = parse_infobox(infobox) if isinstance(infobox, str) else infobox
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip().lower()
        if key not in keys:
            continue
        value = item.get("value")
        if isinstance(value, list):
            for entry in value:
                text = entry.get("v") if isinstance(entry, dict) else entry
                if isinstance(text, str) and text.strip():
                    yield text
        elif isinstance(value, str) and value.strip():
            yield value


def extract_infobox_aliases(
    infobox: Any, keys: frozenset[str] | set[str] = DEFAULT_ALIAS_KEYS
) -> list[str]:
    """``iter_infobox_aliases`` 的列表形式。"""
    return list(iter_infobox_aliases(infobox, keys))
