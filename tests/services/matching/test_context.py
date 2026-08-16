"""MatchContext / MatchResult 数据结构测试"""

from __future__ import annotations

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.result import MatchResult
from app.services.sync_service.match_trace import MatchTrace


def _build_item() -> CustomItem:
    return CustomItem(
        media_type="episode",
        title="测试番剧",
        ori_title=None,
        season=1,
        episode=1,
        release_date="",
        user_name="test_user",
        source="test",
    )


def test_context_defaults():
    """新建 ctx 应有默认空值，仅 item/bgm/trace 必填"""
    ctx = MatchContext(
        item=_build_item(),
        bgm=object(),
        trace=MatchTrace(),
    )
    assert ctx.subject_id is None
    assert ctx.bgm_se_id is None
    assert ctx.bgm_ep_id is None
    assert ctx.normalized_title == ""
    assert ctx.match_stage == ""
    assert ctx.match_method_detail == ""
    assert ctx.matched_variant_method == ""
    assert ctx.bgm_data is None
    assert ctx.is_ambiguous is False
    assert ctx.failure_detail == ""


def test_context_field_assignment():
    """step 执行后写入 ctx 字段"""
    ctx = MatchContext(
        item=_build_item(),
        bgm=object(),
        trace=MatchTrace(),
    )
    ctx.normalized_title = "测试番剧"
    ctx.subject_id = "12345"
    ctx.match_stage = "archive"
    ctx.match_method_detail = "exact"
    assert ctx.subject_id == "12345"
    assert ctx.match_stage == "archive"
    assert ctx.match_method_detail == "exact"


def test_result_defaults():
    """MatchResult 默认值"""
    trace = MatchTrace()
    result = MatchResult(
        subject_id=None,
        bgm_se_id=None,
        bgm_ep_id=None,
        bgm_title="",
        is_season_matched_id=False,
        trace=trace,
    )
    assert result.subject_id is None
    assert result.failure_detail == ""
    assert result.is_ambiguous is False
    assert result.trace is trace


def test_result_from_ctx_fields():
    """MatchResult 映射 ctx 字段"""
    trace = MatchTrace()
    result = MatchResult(
        subject_id="12345",
        bgm_se_id="12345",
        bgm_ep_id="67890",
        bgm_title="测试番剧",
        is_season_matched_id=False,
        trace=trace,
        failure_detail="",
        is_ambiguous=True,
    )
    assert result.bgm_se_id == "12345"
    assert result.bgm_ep_id == "67890"
    assert result.bgm_title == "测试番剧"
    assert result.is_ambiguous is True
