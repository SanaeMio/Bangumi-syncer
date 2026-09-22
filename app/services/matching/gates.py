"""执行门（Gate）集中登记表

把「某个 step 要不要执行」的条件从各 step 的 ``execute`` 开头集中到这里，
使**全部执行门可枚举、可单独测试、可在 trace 里区分原因**。

改造前：7 处裸 ``if ... return StepOutcome(status="skipped", ...)`` 散落在
4 个文件的 ``execute`` 内部，无法枚举「系统一共有哪些门」，也无法单测判定。

改造后：每个门是一个具名 ``Gate`` 常量，登记在此文件。step 通过类属性
``gates`` 声明自己受哪些门约束，管线在 ``execute`` 前统一评估。

为什么这样是安全的
------------------
``status="skipped"`` 在两条管线里**都不终止**，且全仓**没有任何逻辑分支
消费它**（只有 hit/miss/error/low_confidence 被分支，见
``tests/services/test_gate_contract.py``）。因此改变「门在哪里判定」不影响
匹配控制流，只影响 trace 里记录的跳过原因。

门的两种形态
------------
1. **声明式门**（step 类属性 ``gates``）：判定只依赖 ctx / prev，无副作用。
   由管线统一评估，命中即产出标准 skipped outcome（含 ``gate`` 标识）。
2. **内联门**：判定依赖 ``execute`` 中途产生的状态（如 ``ctx.bgm`` 的赋值），
   无法提到执行前。仍在 ``execute`` 内判定，但复用本文件的 ``Gate`` 常量，
   使门名同样出现在 trace 里。
"""

from __future__ import annotations

from typing import Any

from app.services.matching.steps.base import Gate

# ============================================================================
# 配置开关类（进程级稳定，与请求无关）
# ============================================================================


def _archive_disabled(ctx: Any, prev: Any = None) -> bool:
    """archive 未启用时跳过归档短路，交由 API 托底。

    需要 ``ctx.bgm`` —— 由 ArchiveShortcutStep 在 execute 开头获取并写回
    （该赋值本身有副作用，故这是**内联门**，不走声明式评估）。
    """
    bgm = getattr(ctx, "bgm", None)
    if bgm is None:
        return False
    archive = getattr(bgm, "_archive", None)
    return archive is None or not getattr(archive, "enabled", False)


ARCHIVE_DISABLED = Gate(
    name="archive_disabled",
    describe="archive 未启用，走 API 托底",
    check=_archive_disabled,
)


def _bangumi_data_disabled(ctx: Any, prev: Any = None) -> bool:
    """bangumi-data 本地数据源被禁用"""
    from app.services.sync_service import config_manager

    return not config_manager.get("bangumi_data", "enabled", fallback=True)


BANGUMI_DATA_DISABLED = Gate(
    name="bangumi_data_disabled",
    describe="bangumi-data 已禁用",
    check=_bangumi_data_disabled,
)


# ============================================================================
# 环境 / 输入前置条件类
# ============================================================================


def _bgm_unavailable(ctx: Any, prev: Any = None) -> bool:
    """无法取得 BangumiApi 实例（未配置账号等），归档短路无从执行。

    内联门：获取 bgm 的调用同时会把结果写回 ``ctx.bgm`` 供后续 step 复用。
    """
    return getattr(ctx, "bgm", None) is None


BGM_UNAVAILABLE = Gate(
    name="bgm_unavailable",
    describe="bgm 不可用，跳过 archive 短路",
    check=_bgm_unavailable,
)


def _no_valid_date(ctx: Any, prev: Any = None) -> bool:
    """无有效首播日期（长度 < 10），日期精确搜索无从执行"""
    item = getattr(ctx, "item", None)
    release_date = getattr(item, "release_date", "") if item else ""
    return not release_date or len(release_date) < 10


NO_VALID_DATE = Gate(
    name="no_valid_date",
    describe="无有效日期，跳过精确搜索",
    check=_no_valid_date,
)


# ============================================================================
# 上游已满足类（早退优化：前序步骤已给出结果，本步无需再跑）
# ============================================================================


def _exact_search_hit(ctx: Any, prev: Any = None) -> bool:
    """精确搜索首条相似度已达 PRIMARY 阈值，无需变体兜底"""
    from app.services.matching.steps.api_search import API_SIMILARITY_PRIMARY

    bgm_data = getattr(ctx, "bgm_data", None)
    if not bgm_data:
        return False
    bgm = getattr(ctx, "bgm", None)
    item = getattr(ctx, "item", None)
    if bgm is None or item is None:
        return False
    ratio = bgm.title_diff_ratio(
        title=item.title,
        ori_title=item.ori_title,
        bgm_data=bgm_data[0],
    )
    return ratio >= API_SIMILARITY_PRIMARY


EXACT_SEARCH_HIT = Gate(
    name="exact_search_hit",
    describe="精确搜索已命中，跳过兜底",
    check=_exact_search_hit,
)


def _episode_already_resolved(ctx: Any, prev: Any = None) -> bool:
    """上游已解析出 episode_id（本季直接命中），无需跨季回退"""
    return bool(prev and prev.get("episode_id"))


EPISODE_ALREADY_RESOLVED = Gate(
    name="episode_already_resolved",
    describe="集数解析已命中，无需跨季回退",
    check=_episode_already_resolved,
)


# ============================================================================
# 登记表：全部执行门的清单（供文档 / 测试 / 排查枚举）
# ============================================================================

ALL_GATES: tuple[Gate, ...] = (
    ARCHIVE_DISABLED,
    BANGUMI_DATA_DISABLED,
    BGM_UNAVAILABLE,
    NO_VALID_DATE,
    EXACT_SEARCH_HIT,
    EPISODE_ALREADY_RESOLVED,
)

#: 门名 → Gate，便于按 trace 里的 gate 字段反查
GATES_BY_NAME: dict[str, Gate] = {g.name: g for g in ALL_GATES}
