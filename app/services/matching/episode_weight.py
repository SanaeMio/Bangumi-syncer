"""集数一致性加权（可选步骤，默认关闭）

媒体源推来的「季数 + 集数」是可用的匹配证据：若某候选条目（目标季）的章节数
装得下请求的集数，说明它更可能是正确的续作条目。本模块把它做成**排序加权**，
而不是「先按季集选、错了再回退」。

为什么不做「优先路径 + 回退」
------------------------------
回退需要一个「错了」的判据，而真实数据里「媒体源错了」（S50E1000 这类文件名
解析垃圾值、剧场版被塞成 season=1/episode=1）与「Bangumi 章节结构与媒体库不同」
（长篇被拆成 S01E120、章节 sort 从 0 起）无法区分。判错方向就会把好数据回退掉，
且一次同步会跑两遍、给出两个不同结论，排查成本极高。

加权是同一个决策内的一次排序，天然没有这个问题：对得上就排前面，对不上就排后面，
最终仍由既有的置信度 / 裁决层统一判定。

为什么不默认开启
----------------
「季集可信」依赖媒体库刮削质量，而这是个无法在程序里验证的前提。真实库中：
- 长篇连载常被拆成 S01E120，与 Bangumi 章节编号错位；
- 剧场版 / OVA 的季集是媒体库编造的；
- 文件名解析可能产出垃圾集号。
这些场景下「集数对得上」并不能证明条目选对了。故交由用户在配置页决定
（``[matching] episode_count_weight``）。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import logger

# 章节数充足时的加分 / 明显不足时的减分。
#
# 分值取得很小：加权只用于**微调同分候选的顺序**，不参与置信度阈值判定
# （阈值判的是 title_diff_ratio 算出的 score，与本加权相互独立）。若此处给大
# 分值，会让「标题明显不像但集数刚好够」的条目压过「标题高度相似」的条目。
BONUS_EPISODE_FIT = 0.02
PENALTY_EPISODE_SHORT = 0.02


def apply_episode_count_weight(
    candidates: list[dict[str, Any]],
    target_ep: int | None,
    fetch_episode_count: Any,
    *,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """按「目标季章节数是否装得下请求集数」微调候选顺序

    Args:
        candidates: 候选条目列表（需带 ``id`` 字段）
        target_ep: 媒体源推来的集数；None / 0 时不做任何调整
        fetch_episode_count: ``(subject_id) -> int | None``，返回该条目本篇章节数。
            返回 None 表示无法确定（条目无章节数据 / 查询失败），此时**不加不减**。
            做成回调是为了让本函数保持纯函数、易于测试，也避免这里耦合 API 层。
        limit: 只对前 limit 个候选计算（默认全部）。排序后仍返回完整列表。

    Returns:
        ``(调整后的候选列表, 说明列表)``。说明用于 match trace，便于排查
        「为什么这条被排到了后面」。

    取向：**只在有把握时调整**。无法确定章节数的一律不加不减；目标季集数恰好
    等于章节数视为刚好够（加分而非减分）—— Bangumi 常有「正片比标注集数少一集」
    的情况。
    """
    if not candidates or not target_ep or target_ep <= 0:
        return candidates, []
    if not isinstance(candidates, list):
        return candidates, []  # type: ignore[return-value]

    notes: list[str] = []
    adjusted: list[tuple[float, dict[str, Any]]] = []
    probe = candidates if limit is None else candidates[:limit]

    for cand in probe:
        if not isinstance(cand, dict):
            adjusted.append((0.0, cand))
            continue
        subject_id = cand.get("id")
        if subject_id is None:
            adjusted.append((0.0, cand))
            continue
        try:
            count = fetch_episode_count(subject_id)
        except Exception as e:  # noqa: BLE001 — 查询失败不影响匹配
            logger.debug(f"集数加权跳过 {subject_id}（查询失败）: {e!r}"[:120])
            count = None
        if not isinstance(count, int) or count <= 0:
            adjusted.append((0.0, cand))
            continue

        if count >= target_ep:
            adjusted.append((BONUS_EPISODE_FIT, cand))
            notes.append(f"集数加 {subject_id}：章节 {count} ≥ 请求 {target_ep}")
        else:
            adjusted.append((-PENALTY_EPISODE_SHORT, cand))
            notes.append(f"集数减 {subject_id}：章节 {count} < 请求 {target_ep}")

    # 未参与计算（limit 之外）的候选保持原顺序排在后面，不参与本次重排
    tail = candidates[len(probe) :] if limit is not None else []

    # 稳定排序：同分候选保持原有相对顺序（platform 加权已经排过一次）
    reordered = [c for _, c in sorted(adjusted, key=lambda x: x[0], reverse=True)]
    return reordered + list(tail), notes
