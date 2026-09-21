"""#4 bangumi_data 日期硬校验单测

覆盖：请求无日期 / 命中条目无 begin / 日期差在阈值内 / 低置信区间 / 超阈值驳回，
以及驳回后必须继续执行后续步骤（不设 ctx.subject_id、不终止）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.steps.bangumi_data import (
    DATE_GUARD_LOW_CONFIDENCE_DAYS,
    DATE_GUARD_REJECT_DAYS,
    BangumiDataStep,
)
from app.services.sync_service.match_trace import MatchTrace


def _build_ctx(release_date: str | None = "2024-01-15", title: str = "测试番剧"):
    return MatchContext(
        item=CustomItem(
            media_type="episode",
            title=title,
            ori_title=None,
            season=1,
            episode=1,
            release_date=release_date or "",
            user_name="u",
            source="test",
        ),
        bgm=None,
        trace=MatchTrace(),
        service=MagicMock(),
    )


def _run(
    ctx: MatchContext,
    hit_id: str = "66",
    matched_title: str = "测试番剧",
    hit_begin: str | None = "2024-01-01",
    date_matched: bool = False,
):
    """构造一个 bangumi-data 命中并跑一次 step

    hit_begin=None 模拟候选里没有 date 字段（条目无 begin / 精确索引路径）。
    """
    mock_bgm_data = MagicMock()

    def _fake_find(*args, **kwargs):
        out = kwargs.get("candidates_out")
        if out is not None:
            cand = {
                "id": hit_id,
                "name": matched_title,
                "name_cn": matched_title,
                "score": 0.618,
                "source": "bangumi_data_partial",
            }
            if hit_begin is not None:
                cand["date"] = hit_begin
            out[:] = [cand]
        return (hit_id, matched_title, date_matched)

    mock_bgm_data.find_bangumi_id.side_effect = _fake_find
    ctx.service._get_bangumi_data.return_value = mock_bgm_data

    with patch("app.services.sync_service.config_manager") as mock_cfg:
        mock_cfg.get.side_effect = lambda s, k, fallback=None: (
            True if (s, k) == ("bangumi_data", "enabled") else fallback
        )
        return BangumiDataStep().execute(ctx)


class TestDateGuardReject:
    def test_huge_date_gap_is_rejected(self):
        """差 55 年（巨型金丝雀 1947 → 命中 2002）→ 不采信，继续后续步骤"""
        ctx = _build_ctx(release_date="1947-12-06")
        outcome = _run(ctx, hit_begin="2002-03-22T16:00:00.000Z")

        assert outcome.status == "miss"
        assert outcome.is_terminal is False
        assert ctx.subject_id is None
        assert ctx.match_stage != "bangumi_data"
        assert outcome.outputs["date_guard"] == "reject"
        assert outcome.outputs["rejected_subject_id"] == "66"
        assert outcome.outputs["date_diff_days"] > DATE_GUARD_REJECT_DAYS
        assert "不予采信" in outcome.reason

    def test_eight_year_gap_is_rejected(self):
        """差 8 年（人生单车 2022 → 命中 2014）→ 不采信"""
        ctx = _build_ctx(release_date="2022-06-02")
        outcome = _run(ctx, hit_begin="2014-07-06")

        assert outcome.status == "miss"
        assert ctx.subject_id is None
        assert outcome.outputs["date_guard"] == "reject"

    def test_reject_still_returns_candidates(self):
        """驳回时候选仍需回传，供候选队列展示"""
        ctx = _build_ctx(release_date="1947-12-06")
        outcome = _run(ctx, hit_begin="2002-03-22")

        assert len(outcome.candidates) == 1
        assert outcome.candidates[0].subject_id == "66"


class TestDateGuardPassThrough:
    def test_no_request_date_skips_guard(self):
        """请求无日期 → 不校验（保留原行为）"""
        ctx = _build_ctx(release_date=None)
        outcome = _run(ctx, hit_begin="2002-03-22")

        assert outcome.status == "hit"
        assert outcome.is_terminal is True
        assert ctx.subject_id == "66"
        assert outcome.outputs["date_guard"] == "ok"

    def test_no_begin_date_skips_guard(self):
        """命中条目无 begin → 不校验（避免把"日期缺失"误判为"日期差极大"）"""
        ctx = _build_ctx(release_date="2024-01-15")
        outcome = _run(ctx, hit_begin=None)

        assert outcome.status == "hit"
        assert outcome.is_terminal is True
        assert outcome.outputs["date_guard"] == "ok"

    def test_small_gap_hits(self):
        """日期差在 90 天内 → 正常命中"""
        ctx = _build_ctx(release_date="2024-01-15")
        outcome = _run(ctx, hit_begin="2024-01-01")

        assert outcome.status == "hit"
        assert outcome.is_terminal is True
        assert ctx.subject_id == "66"
        assert outcome.outputs["date_diff_days"] == 14

    def test_mid_gap_hits_with_low_confidence_marker(self):
        """90 天 < 差 <= 365 天 → 仍命中，但标记 low_confidence（仅观测不拦）"""
        ctx = _build_ctx(release_date="2024-01-15")
        outcome = _run(ctx, hit_begin="2023-06-01")  # 约 228 天

        assert outcome.status == "hit"
        assert outcome.is_terminal is True
        assert ctx.subject_id == "66"
        diff = outcome.outputs["date_diff_days"]
        assert DATE_GUARD_LOW_CONFIDENCE_DAYS < diff <= DATE_GUARD_REJECT_DAYS
        assert outcome.outputs["date_guard"] == "low_confidence"
        assert "低置信" in outcome.reason

    def test_boundary_365_days_still_hits(self):
        """恰好 365 天 → 边界内，命中（> 才驳回）"""
        ctx = _build_ctx(release_date="2024-01-15")
        outcome = _run(ctx, hit_begin="2023-01-15")

        assert outcome.status == "hit"
        assert outcome.outputs["date_diff_days"] == 365


class TestDateDiffHelper:
    def test_unparsable_returns_none(self):
        from app.utils.bangumi_data.matching import date_diff_days

        assert date_diff_days("", "2024-01-01") is None
        assert date_diff_days("2024-01-01", "not-a-date") is None
        assert date_diff_days("2024-01-01", "2024-01-11") == 10
