"""季集一致性加权（可选步骤，默认关闭）

媒体源推来的「季数 + 集数」是可用的匹配证据：若某候选条目（目标季）的章节数
装得下请求的集数，说明它更可能是正确的续作条目；候选标题**明确声明**了与请求
一致的季号（``第2期`` / ``第二季`` / 结尾贴号 ``XXX2``），同样是识别续作的
有力证据。本模块把这两项做成**排序加权**，而不是「先按季集选、错了再回退」。

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

季号加权为什么只看「明确声明」
------------------------------
候选标题**未声明**季号时不做任何调整 —— 这一点与
``MatchingMixin._filter_candidates_by_season`` 的「无季号按第一季处理」不同：
那里是在同一部作品的前传/续作闭包内筛选（无季号者基本就是第一季本体），
这里的候选来自开放文本搜索，无季号可能只是「标题恰好不带后缀的独立条目」，
据此扣分会误伤。只有标题白纸黑字写了季号时才加减分。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import logger
from app.utils.season_title import extract_season_candidates

# 章节数充足时的加分 / 明显不足时的减分。
#
# 分值取得很小：加权只用于**微调同分候选的顺序**，不参与置信度阈值判定
# （阈值判的是 title_diff_ratio 算出的 score，与本加权相互独立）。若此处给大
# 分值，会让「标题明显不像但集数刚好够」的条目压过「标题高度相似」的条目。
BONUS_EPISODE_FIT = 0.02
PENALTY_EPISODE_SHORT = 0.02

# 季号声明一致时的加分 / 明确声明其他季号时的减分（取值理由同上）。
BONUS_SEASON_MATCH = 0.02
PENALTY_SEASON_MISMATCH = 0.02

# 媒体季号的合理范围。超出视为解析垃圾（S50 这类），不做加权。
_MIN_SEASON = 1
_MAX_SEASON = 99


def _episode_deltas(
    probe: list[Any], target_ep: int, fetch_episode_count: Any
) -> tuple[list[float], list[str]]:
    """计算探针范围内每个候选的集数调整分（与候选一一对应）"""
    deltas: list[float] = []
    notes: list[str] = []
    for cand in probe:
        if not isinstance(cand, dict):
            deltas.append(0.0)
            continue
        subject_id = cand.get("id")
        if subject_id is None:
            deltas.append(0.0)
            continue
        try:
            count = fetch_episode_count(subject_id)
        except Exception as e:  # noqa: BLE001 — 查询失败不影响匹配
            logger.debug(f"集数加权跳过 {subject_id}（查询失败）: {e!r}"[:120])
            count = None
        if not isinstance(count, int) or count <= 0:
            deltas.append(0.0)
            continue

        if count >= target_ep:
            deltas.append(BONUS_EPISODE_FIT)
            notes.append(f"集数加 {subject_id}：章节 {count} ≥ 请求 {target_ep}")
        else:
            deltas.append(-PENALTY_EPISODE_SHORT)
            notes.append(f"集数减 {subject_id}：章节 {count} < 请求 {target_ep}")
    return deltas, notes


def _season_deltas(
    probe: list[Any], target_season: int, title_fields: tuple[str, ...]
) -> tuple[list[float], list[str]]:
    """计算探针范围内每个候选的季号调整分（与候选一一对应）"""
    deltas: list[float] = []
    notes: list[str] = []
    for cand in probe:
        seasons: set[int] = set()
        if isinstance(cand, dict):
            for field in title_fields:
                text = cand.get(field)
                if isinstance(text, str) and text:
                    seasons |= extract_season_candidates(text)
        if not seasons:
            # 未声明季号：无法区分「第一季本体」与「不带后缀的独立条目」，不动
            deltas.append(0.0)
        elif target_season in seasons:
            deltas.append(BONUS_SEASON_MATCH)
            notes.append(
                f"季号加 {cand.get('id')}：标题声明 {sorted(seasons)} 含目标 {target_season}"
            )
        else:
            deltas.append(-PENALTY_SEASON_MISMATCH)
            notes.append(
                f"季号减 {cand.get('id')}：标题声明 {sorted(seasons)} 不含目标 {target_season}"
            )
    return deltas, notes


def _reorder(
    candidates: list[Any],
    probe: list[Any],
    deltas: list[float],
    notes: list[str],
) -> tuple[list[Any], list[str]]:
    """按调整分对探针范围做一次稳定排序，limit 外的候选原序接在后面"""
    tail = candidates[len(probe) :]

    # 稳定排序：同分候选保持原有相对顺序（platform 加权已经排过一次）
    reordered = [
        c
        for _, c in sorted(
            zip(deltas, probe, strict=True), key=lambda x: x[0], reverse=True
        )
    ]
    return reordered + list(tail), notes


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

    probe = candidates if limit is None else candidates[:limit]
    deltas, notes = _episode_deltas(probe, int(target_ep), fetch_episode_count)
    return _reorder(candidates, probe, deltas, notes)


def apply_season_number_weight(
    candidates: list[dict[str, Any]],
    target_season: int | None,
    *,
    title_fields: tuple[str, ...] = ("name", "name_cn"),
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """按「候选标题明确声明的季号是否与请求一致」微调候选顺序

    只在候选标题**白纸黑字**声明了季号时加减分（``第2期`` / ``第二季`` /
    ``Season 2`` / 结尾贴号 ``XXX2`` / 罗马数字 ``II``）：

    - 声明集合包含目标季号 → 加分；
    - 声明集合不含目标季号 → 减分；
    - 完全未声明 → 不加不减（理由见模块 docstring「季号加权为什么只看明确声明」）。

    Args:
        candidates: 候选条目列表
        target_season: 媒体源推来的季号；无效（None / 超出合理范围）时不做任何调整
        title_fields: 参与季号提取的候选字段名（默认 ``name`` 与 ``name_cn``）
        limit: 只对前 limit 个候选计算（默认全部）。排序后仍返回完整列表。

    Returns:
        ``(调整后的候选列表, 说明列表)``，说明用于 match trace。
    """
    if not candidates or not isinstance(target_season, int):
        return candidates, []
    if not (_MIN_SEASON <= target_season <= _MAX_SEASON):
        return candidates, []
    if not isinstance(candidates, list):
        return candidates, []  # type: ignore[return-value]

    probe = candidates if limit is None else candidates[:limit]
    deltas, notes = _season_deltas(probe, target_season, title_fields)
    return _reorder(candidates, probe, deltas, notes)


def apply_season_episode_weight(
    candidates: list[dict[str, Any]],
    *,
    target_season: int | None = None,
    target_ep: int | None = None,
    fetch_episode_count: Any = None,
    title_fields: tuple[str, ...] = ("name", "name_cn"),
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """把媒体源推来的「季号 + 集数」**一同**纳入候选排序

    与先后调用两个独立加权不同，本函数把两项调整分**求和**后做**一次**稳定
    排序：集数对得上但季号声明不符的候选（+0.02-0.02=0）不会被季号单项排序
    拉到集数季号都对不上的候选之后，两个证据的强弱在同一次排序里可比。

    Args:
        candidates: 候选条目列表
        target_season: 媒体源推来的季号（None / 无效时季号项不参与）
        target_ep: 媒体源推来的集数（None / 0 时集数项不参与）
        fetch_episode_count: ``(subject_id) -> int | None`` 章节数回调，见
            :func:`apply_episode_count_weight`
        title_fields: 季号提取的候选字段名，见 :func:`apply_season_number_weight`
        limit: 只对前 limit 个候选计算（默认全部）

    Returns:
        ``(调整后的候选列表, 集数说明列表, 季号说明列表)``。
        两项证据都无效时原样返回，说明列表为空。
    """
    if not candidates or not isinstance(candidates, list):
        return candidates, [], []  # type: ignore[return-value]

    has_ep = bool(target_ep) and target_ep > 0
    has_season = isinstance(target_season, int) and (
        _MIN_SEASON <= target_season <= _MAX_SEASON
    )
    if not has_ep and not has_season:
        return candidates, [], []

    probe = candidates if limit is None else candidates[:limit]

    if has_ep:
        ep_deltas, ep_notes = _episode_deltas(
            probe, int(target_ep), fetch_episode_count
        )
    else:
        ep_deltas, ep_notes = [0.0] * len(probe), []

    if has_season:
        season_deltas, season_notes = _season_deltas(probe, target_season, title_fields)
    else:
        season_deltas, season_notes = [0.0] * len(probe), []

    total = [e + s for e, s in zip(ep_deltas, season_deltas, strict=True)]
    reordered, _ = _reorder(candidates, probe, total, [])
    return reordered, ep_notes, season_notes
