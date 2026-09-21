"""匹配裁决层（P3）

把「哪个候选胜出」从各 step 内部的隐式约定（first-hit-wins + 改选硬编码顺序）
收敛成一次显式裁决：

    跨源投票 → 加权排序 → margin 门控 → auto_confirm / needs_review / reject

为什么需要单独一层
------------------
改造前，判定散落在 APISearchStep 内部：

- ``bgm_data[0]``（platform 排序 + post_search 改选后的队首）天然就是答案，
  候选列表只是**记录**，不参与判定；
- 置信度只看 top1 的 ``title_diff_ratio``，没有「top1 比 top2 强多少」的概念；
- 阈值散落多处（``match_confidence_threshold`` 0.6、歧义 0.05 硬编码）。

结果是 archive 短路这类「只有一个候选、无从比较」的分支只能盲信 ——
实测 76 条盲信命中里 10 条标错（13.2%）。

裁决层把它拆成两件可独立调整的事：**分数够不够**（min_score）与
**领先得够不够**（min_margin）。

设计取舍
--------
1. **跨源聚合取「加权后最高分」而非求和或平均**：

   - 求和会破坏 0-1 分数语义（多源命中容易 >1，无法与 min_score 比较）；
   - 平均会让低分源稀释高分源 —— archive 给 0.7、api_search 给 0.9 时
     平均只有 0.8，反而在 min_score=0.85 下被否掉，**多投一票反而更差**。
     而不同源的打分标准并不完全一致（``title_diff_ratio`` 对条目形状的
     敏感度不同），平均会把这种系统性差异当成噪声引入。

   因此取 ``max(权重 × 分数)``：让最可信的源主导判定，权重用于表达
   「哪个源说了算」，多源命中这一事实记录在 ``RankedCandidate.sources``
   里供排查。例：archive 权重 0.1 时 ``max(0.1×0.9, 1.0×0.8) = 0.8``，
   api_search 的 0.8 胜出 —— 权重确实改变了裁决结果。

2. **margin 保持三态**：``None`` = 无从比较（候选不足或没有打分信息），
   不能与「并列第一 margin=0」混为一谈 —— 后者说明条目本身有歧义，
   前者只是缺少信息。语义与 ``contracts.compute_margin`` 一致。

3. **默认关闭**：``enabled=False`` 时调用方走原有判定，行为逐点位等价。
   裁决会改变现有点位的判定结果，必须先在黄金用例集上量化再决定是否默认启用。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.services.matching.contracts import (
    SOURCE_API_SEARCH,
    SOURCE_ARCHIVE,
    SOURCE_BANGUMI_DATA,
    SOURCE_CUSTOM_MAPPING,
)
from app.services.sync_service.match_trace import MatchCandidate

# 裁决结论
VERDICT_AUTO = "auto_confirm"  # 自动采用
VERDICT_REVIEW = "needs_review"  # 沉淀待审（分数够但领先不足 / 有歧义）
VERDICT_REJECT = "reject"  # 判定无匹配（分数不达标）

# 配置段与默认值
CONFIG_SECTION = "matching"
DEFAULT_WEIGHTS: dict[str, float] = {
    SOURCE_CUSTOM_MAPPING: 1.0,
    SOURCE_BANGUMI_DATA: 1.0,
    SOURCE_ARCHIVE: 1.0,
    SOURCE_API_SEARCH: 1.0,
}
DEFAULT_MIN_SCORE = 0.85
DEFAULT_MIN_MARGIN = 0.10
DEFAULT_AMBIGUOUS_MARGIN = 0.05


@dataclass(frozen=True)
class MatchPolicy:
    """C6：所有匹配阈值集中于此

    此前 0.x 阈值散落在三十余处（置信度 0.6、歧义 0.05、部分匹配 0.4……），
    改一个要翻遍代码。裁决层相关的阈值全部收在这里，便于整体调整与解释。
    """

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    min_score: float = DEFAULT_MIN_SCORE
    min_margin: float = DEFAULT_MIN_MARGIN
    ambiguous_margin: float = DEFAULT_AMBIGUOUS_MARGIN
    enabled: bool = False

    def weight_of(self, source: str) -> float:
        """取某来源权重；未知来源按 1.0 处理（不因新来源而整体失效）"""
        return self.weights.get(source, 1.0)


@dataclass(frozen=True)
class RankedCandidate:
    """聚合后的候选条目

    同一条目可能被多个来源同时命中（archive 与 api_search 各命中一次），
    裁决前先合并，避免同源重复计数把票投歪。
    """

    subject_id: str
    weighted_score: float  # max(权重 × 分数)，落在 0-1
    sources: tuple[str, ...]
    best: MatchCandidate  # 加权分最高的那一条，供 trace 展示

    @property
    def source_count(self) -> int:
        return len(self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "weighted_score": round(self.weighted_score, 4),
            "sources": list(self.sources),
            "name": self.best.name,
            "name_cn": self.best.name_cn,
        }


@dataclass(frozen=True)
class Decision:
    """裁决结果

    ``margin`` 为 None 表示「无从比较」，调用方不应据此判定条目有歧义。
    """

    verdict: str
    subject_id: str | None = None
    score: float | None = None  # top1 加权分
    margin: float | None = None  # top1 − top2，三态
    reason: str = ""
    is_ambiguous: bool = False  # margin < ambiguous_margin，供编排器发歧义通知
    rankings: tuple[RankedCandidate, ...] = ()

    @property
    def accepted(self) -> bool:
        """是否可直接采用（对应原 status=hit）"""
        return self.verdict == VERDICT_AUTO

    @property
    def needs_review(self) -> bool:
        """是否沉淀待审（对应原 status=low_confidence）"""
        return self.verdict == VERDICT_REVIEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "subject_id": self.subject_id,
            "score": self.score,
            "margin": self.margin,
            "reason": self.reason,
            "is_ambiguous": self.is_ambiguous,
            "rankings": [r.to_dict() for r in self.rankings],
        }


def _safe_score(candidate: MatchCandidate) -> float:
    """取候选分数；非数值（mock / 脏数据）按 0.0 处理"""
    score = getattr(candidate, "score", 0.0)
    return score if isinstance(score, (int, float)) else 0.0


class Arbiter:
    """按 source 权重 + margin 裁决候选列表

    纯函数式：只读候选与 policy，不碰 ctx / 不写 trace / 不发通知，
    方便在黄金用例集上直接跑 A/B。
    """

    def rank(
        self, candidates: Iterable[MatchCandidate] | None, policy: MatchPolicy
    ) -> list[RankedCandidate]:
        """跨源聚合 + 加权排序

        同一 subject_id 的候选合并为一条，加权分取 ``max(权重 × 分数)``。
        """
        if not candidates:
            return []

        grouped: dict[str, list[MatchCandidate]] = {}
        for c in candidates:
            sid = str(getattr(c, "subject_id", "") or "")
            if not sid:
                continue
            grouped.setdefault(sid, []).append(c)

        ranked: list[RankedCandidate] = []
        for sid, group in grouped.items():
            sources: list[str] = []
            best: MatchCandidate | None = None
            best_weighted = -1.0
            for c in group:
                source = getattr(c, "source", "") or ""
                if source and source not in sources:
                    sources.append(source)
                weighted = policy.weight_of(source) * _safe_score(c)
                # 取加权后最高的一条：让最可信的源主导（见模块 docstring 取舍 1）
                if weighted > best_weighted:
                    best_weighted = weighted
                    best = c
            if best is None:
                continue
            ranked.append(
                RankedCandidate(
                    subject_id=sid,
                    weighted_score=round(best_weighted, 4),
                    sources=tuple(sources),
                    best=best,
                )
            )

        ranked.sort(key=lambda r: r.weighted_score, reverse=True)
        return ranked

    def decide(
        self,
        candidates: Iterable[MatchCandidate] | None,
        policy: MatchPolicy | None = None,
        anchor_subject_id: str | None = None,
    ) -> Decision:
        """裁决：跨源投票 → 排序 → margin 门控

        Args:
            candidates: 候选条目
            policy: 裁决策略
            anchor_subject_id: **被采用**的条目（post_search 改选后的队首）。
                传入时走「改选优先」语义：门控分数取 anchor 自己的加权分，
                margin = anchor 分 − 其他候选最高分（可能为负）。
                不传时走「纯排序」语义：门控分数取 top1 加权分。

                为什么必须区分：改选（季度 / 媒体类型 / 关联条目）是领域逻辑，
                裁决不改选。若拿候选池最高分去门控 anchor 的采用，就是
                「用 A 的分数决定要不要采用 B」的错配 —— 实测这会让 min_score
                门控彻底失效：错命中一条都拦不住，反而把正确命中误伤成待审
                （L2 240 条：错命中 9→9，正确采用少 9 条）。

        门控顺序（先判分够不够，再看领先多少）：

        1. 无候选 → reject
        2. 被采用条目加权分 < min_score → reject
        3. margin < ambiguous_margin → needs_review（并列 / 歧义）
        4. margin < min_margin → needs_review（领先不足）
        5. 否则 → auto_confirm

        margin 为 None（没有对比候选、或完全没有打分信息）时不做 margin 门控：
        缺少对比信息不等于条目有歧义，此时只看分数是否达标。
        """
        pol = policy or MatchPolicy()
        ranked = self.rank(candidates, pol)

        if not ranked:
            return Decision(
                verdict=VERDICT_REJECT,
                reason="无候选可裁决",
                rankings=(),
            )

        # 被评估的条目：有 anchor 用 anchor，否则用加权分最高的
        chosen = ranked[0]
        if anchor_subject_id:
            for r in ranked:
                if r.subject_id == str(anchor_subject_id):
                    chosen = r
                    break

        # 对比分：除被采用条目外的最高加权分
        others = [r for r in ranked if r.subject_id != chosen.subject_id]
        best_other = max((r.weighted_score for r in others), default=None)
        margin: float | None = None
        if best_other is not None and any(r.weighted_score for r in ranked):
            margin = round(chosen.weighted_score - best_other, 4)

        if chosen.weighted_score < pol.min_score:
            return Decision(
                verdict=VERDICT_REJECT,
                subject_id=chosen.subject_id,
                score=chosen.weighted_score,
                margin=margin,
                reason=(
                    f"加权分 {chosen.weighted_score:.2f} 低于阈值 {pol.min_score:.2f}"
                ),
                is_ambiguous=(margin is not None and margin < pol.ambiguous_margin),
                rankings=tuple(ranked),
            )

        if margin is not None and margin < pol.ambiguous_margin:
            return Decision(
                verdict=VERDICT_REVIEW,
                subject_id=chosen.subject_id,
                score=chosen.weighted_score,
                margin=margin,
                reason=(
                    f"top1 与 top2 分差 {margin:.2f} 小于歧义阈值 "
                    f"{pol.ambiguous_margin:.2f}，条目本身有歧义"
                ),
                is_ambiguous=True,
                rankings=tuple(ranked),
            )

        if margin is not None and margin < pol.min_margin:
            return Decision(
                verdict=VERDICT_REVIEW,
                subject_id=chosen.subject_id,
                score=chosen.weighted_score,
                margin=margin,
                reason=(
                    f"top1 领先 {margin:.2f} 不足阈值 {pol.min_margin:.2f}，沉淀待审"
                ),
                is_ambiguous=False,
                rankings=tuple(ranked),
            )

        return Decision(
            verdict=VERDICT_AUTO,
            subject_id=chosen.subject_id,
            score=chosen.weighted_score,
            margin=margin,
            reason=(
                f"裁决采用 {chosen.subject_id}"
                + (
                    f"（加权分 {chosen.weighted_score:.2f}，"
                    f"领先 {margin:.2f}，来源 {','.join(chosen.sources)}）"
                    if margin is not None
                    else f"（加权分 {chosen.weighted_score:.2f}，无对比候选）"
                )
            ),
            is_ambiguous=False,
            rankings=tuple(ranked),
        )


def _as_float(value: Any, default: float) -> float:
    """ini 读出来都是字符串，非法值回退默认（配置缺失不应让匹配整体失效）"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def policy_from_config(
    config_manager: Any, section: str = CONFIG_SECTION
) -> MatchPolicy:
    """从配置读取裁决策略

    缺失或非法值一律回退默认：配置项写错不应导致匹配整体失效，
    也不应静默改变判定（enabled 默认 False，即保持原有判定路径）。
    """
    if config_manager is None:
        return MatchPolicy()

    def get(key: str, fallback: Any = None) -> Any:
        try:
            return config_manager.get(section, key, fallback=fallback)
        except Exception:
            return fallback

    enabled = _as_bool(get("arbiter_enabled", False), False)
    weights = dict(DEFAULT_WEIGHTS)
    for source in DEFAULT_WEIGHTS:
        raw = get(f"weight_{source}", None)
        if raw is not None:
            weights[source] = _as_float(raw, DEFAULT_WEIGHTS[source])

    return MatchPolicy(
        weights=weights,
        min_score=_as_float(get("min_score", None), DEFAULT_MIN_SCORE),
        min_margin=_as_float(get("min_margin", None), DEFAULT_MIN_MARGIN),
        ambiguous_margin=_as_float(
            get("ambiguous_margin", None), DEFAULT_AMBIGUOUS_MARGIN
        ),
        enabled=enabled,
    )
