"""匹配置信度分级工具。

用于把 0~1 的相似度分数映射为等级（high/medium/low），供 Web 待审界面
着色徽章展示；并与同步配置 `sync.match_confidence_threshold`（自动采用
vs 沉淀待审的阈值）解耦——本模块只负责"展示分级"，阈值判断在 sync_service。

注意：这里的分级阈值是**绝对相似度**语义，不依赖用户配置；用户可配置的
`match_confidence_threshold` 决定"是否自动同步"，本分级仅决定"徽章颜色"。
"""

from __future__ import annotations

# 置信度等级阈值（绝对相似度，0~1）。
HIGH_CONFIDENCE = 0.8
MEDIUM_CONFIDENCE = 0.5

# 等级 -> 中文标签（Web 待审界面使用）。
CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}


def classify_confidence(score: float | None) -> str:
    """将 0~1 的相似度分数映射为置信度等级 high/medium/low。

    - >= 0.8 -> "high"
    - >= 0.5 -> "medium"
    - 其余（含 None / 非法值）-> "low"
    """
    if score is None:
        return "low"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "low"
    if s >= HIGH_CONFIDENCE:
        return "high"
    if s >= MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


def confidence_label(level: str) -> str:
    """返回置信度等级的中文标签；未知等级原样返回。"""
    return CONFIDENCE_LABELS.get(level, level)


def confidence_badge_class(level: str) -> str:
    """返回 Bootstrap 徽章配色 class（待审界面着色用）。"""
    return {
        "high": "bg-success",
        "medium": "bg-warning text-dark",
        "low": "bg-danger",
    }.get(level, "bg-secondary")
