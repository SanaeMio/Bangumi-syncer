"""业务身份键（business key）构造。

把「用户 + 标题 + 季数」三元组映射为稳定字符串键，
用于 agent_runs 去重 / 重入队 / 结果复用。

键格式：match|{user_name}|{normalize(title).strip().lower()}|{season}
"""

from __future__ import annotations

from ..sync_service.title_normalize import normalize_title_text


def build_match_business_key(
    user_name: str,
    title: str,
    season: int | str | None,
) -> str:
    """构造 match 任务的业务键。

    - title 经 normalize_title_text 归一化后做 ``.strip().lower()``；
      归一化结果为空时退回 ``str(title).strip().lower()``。
    - season 缺失（None / 空字符串）按 1 处理；season=0（SP/特别篇）独立成键。
    - user_name 保持原样（写入与查询使用同一实现即可保证一致性）。
    """
    normalized = normalize_title_text(title)
    if not normalized:
        normalized = str(title).strip().lower()
    else:
        normalized = normalized.strip().lower()

    season_val = _coerce_season(season)
    return f"match|{user_name}|{normalized}|{season_val}"


def _coerce_season(season: int | str | None) -> int:
    """把 season 归一化为整数。

    - None / 空字符串 / 非法值 → 1（缺失语义）
    - season=0（SP/特别篇）→ 0（独立成键，不与 S1 合并）
    - 负数 → 1（非法）
    """
    if season is None:
        return 1
    try:
        v = int(season)
    except (TypeError, ValueError):
        return 1
    return v if v >= 0 else 1
