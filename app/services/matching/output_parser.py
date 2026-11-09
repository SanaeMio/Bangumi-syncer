"""匹配场景的结构化输出解析器（**匹配领域能力，非 llm 层通用设施**）。

LLM 返回自由文本时，从中提取 JSON、校验 subject_id / reason，并以
``(LLMSuggestion | None, 错误原因)`` 形式降级返回。任何畸形 / 类型错误都
以 ``(None, 原因)`` 降级，**绝不抛异常**，供调用方直接写入 ``last_error``。

归属说明（原位于 ``app/services/llm/output_parser.py``，2026-10-11 迁出）：
解析产物 ``subject_id`` 是匹配领域概念，且全仓唯一消费者是
``matching/llm_assist.py``（``exhausted`` 兜底解析文本建议）。llm 层只保留
与领域无关的通用设施（client / models / providers / protocols），避免领域
能力上浮成「通用」后被其它场景误用。

设计为纯函数（无状态）。
"""

import json
import re
from dataclasses import dataclass

__all__ = ["LLMSuggestion", "parse_suggestion"]

# subject_id 约束：非空纯数字（与 tools schema pattern ^\d+$ 对齐）
_SUBJECT_ID_RE = re.compile(r"\d+")


@dataclass
class LLMSuggestion:
    """LLM 结构化建议（与 pending_candidates.llm_subject_id / llm_reason 对应）。"""

    subject_id: str
    reason: str


def parse_suggestion(text: "str | None") -> "tuple[LLMSuggestion | None, str]":
    """从 LLM 文本解析建议。

    返回 ``(LLMSuggestion, "")``（成功）或 ``(None, 错误原因)``（降级）。
    错误原因取值：``空文本`` / ``JSON 提取失败`` / ``JSON 解析失败`` /
    ``subject_id 非法`` / ``reason 超长`` / ``reason 非法``。
    """
    if text is None:
        return None, "空文本"
    stripped = text.strip()
    if not stripped:
        return None, "空文本"

    obj, err = _extract_json_object(stripped)
    if err is not None:
        return None, err
    assert obj is not None  # err is None 时 _extract_json_object 保证 obj 非空

    # 校验 subject_id（缺失 / 非纯数字 / 类型错误）
    raw_id = obj.get("subject_id")
    subject_id = _coerce_subject_id(raw_id)
    if subject_id is None:
        return None, "subject_id 非法"

    # 校验 reason（缺失 -> 空串；超长 -> 拒绝；类型错误 -> 拒绝）
    raw_reason = obj.get("reason")
    if raw_reason is None:
        reason = ""
    elif not isinstance(raw_reason, str) or isinstance(raw_reason, bool):
        return None, "reason 非法"
    elif len(raw_reason) > 200:
        return None, "reason 超长"
    else:
        reason = raw_reason

    return LLMSuggestion(subject_id=subject_id, reason=reason), ""


def _coerce_subject_id(raw: object) -> "str | None":
    """校验并归一化 subject_id。

    纯数字字符串直接通过；纯数字 int（LLM 常以数字形式返回 id）容错 coerce
    为字符串。其余（空串 / 非数字串 / 负数 / 浮点 / bool / 其他）返回 None。
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        return raw if _SUBJECT_ID_RE.fullmatch(raw) else None
    if isinstance(raw, int) and raw >= 0:
        return str(raw)
    return None


def _extract_json_object(text: str) -> "tuple[dict | None, str | None]":
    """从文本提取首个 JSON 对象。

    返回 ``(dict, None)``（成功）、``(None, "JSON 提取失败")``（找不到对象）
    或 ``(None, "JSON 解析失败")``（找到但语法错误 / 非对象）。
    """
    # 尝试 1：整体解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, None
        return None, "JSON 解析失败"
    except json.JSONDecodeError:
        pass

    # 尝试 2：截取首个平衡 {...} 块（容忍前后缀噪声与嵌套）
    start = text.find("{")
    if start == -1:
        return None, "JSON 提取失败"
    end = _find_matching_brace(text, start)
    # 有匹配括号则取平衡块；截断（无匹配右括号）则取首个 { 起的剩余文本，
    # 两种情况下解析失败均归为"JSON 解析失败"（畸形/截断）。
    candidate = text[start : end + 1] if end != -1 else text[start:]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return None, "JSON 解析失败"
    if not isinstance(obj, dict):
        return None, "JSON 解析失败"
    return obj, None


def _find_matching_brace(text: str, start: int) -> int:
    """从 ``start``（'{' 位置）起扫描，返回匹配的右括号索引；未匹配返回 -1。

    正确处理字符串字面量内的 ``{}`` 与转义引号。
    """
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1
