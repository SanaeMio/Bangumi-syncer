"""新增通知事件的注册与触发测试

覆盖：
- 注册表完整性：新事件类型可被列出且元数据正确
- notifier_helpers 辅助函数：notify_scheduler_failure / notify_batch_sync_summary
  / notify_queue_size_warning / notify_source_event 都应正确调用 notification_service.notify
- _maybe_notify_match_ambiguous 在 top1/top2 分数接近时触发，差距足够大时不触发
"""

from unittest.mock import MagicMock, patch

from app.core.notification_registry import (
    _TYPES,
    get_type_meta,
    ui_visible_types,
)
from app.services.base.notifier_helpers import (
    notify_batch_sync_summary,
    notify_queue_size_warning,
    notify_scheduler_failure,
    notify_source_event,
)

# ─────────────────────────────────────────────────────────────────────────
# 注册表完整性
# ─────────────────────────────────────────────────────────────────────────

NEW_TYPES = {
    "source_fetch_failed",
    "source_fetch_empty",
    "scheduler_job_failed",
    "bangumi_token_expired",
    "batch_sync_summary",
    "match_ambiguous",
    "queue_size_warning",
    "app_upgrade_available",
    "archive_build_failed",
    "archive_disk_warning",
    "auth_login_failed",
}


def test_all_new_types_registered():
    """所有新增事件类型都已注册到 _TYPES"""
    for t in NEW_TYPES:
        assert t in _TYPES, f"事件 {t} 未注册"
        meta = get_type_meta(t)
        assert meta is not None
        assert meta.display_name
        assert meta.icon


def test_new_types_listed_in_user_choices():
    """新增事件应出现在用户可选择列表里（除非被显式隐藏）"""
    listed_ids = {t.id for t in ui_visible_types()}
    for t in NEW_TYPES:
        assert t in listed_ids, f"事件 {t} 未出现在用户选择列表"


def test_item_level_flag_correct():
    """match_ambiguous 应为 item_level，其余系统级事件应为非 item_level"""
    assert get_type_meta("match_ambiguous").is_item_level is True
    for t in NEW_TYPES - {"match_ambiguous"}:
        assert get_type_meta(t).is_item_level is False, f"{t} 应为系统级"


# ─────────────────────────────────────────────────────────────────────────
# notifier_helpers 辅助函数
# ─────────────────────────────────────────────────────────────────────────


def _patched_notify():
    """patch notification_service.notify 并返回 mock"""
    return patch("app.services.base.notifier_helpers.notification_service.notify")


def test_notify_scheduler_failure_calls_notify():
    with _patched_notify() as mock_notify:
        notify_scheduler_failure("trakt", "boom", user_id="u1")
    mock_notify.assert_called_once()
    args, kwargs = mock_notify.call_args
    assert args[0] == "scheduler_job_failed"
    assert kwargs["driver"] == "trakt"
    assert kwargs["error_message"] == "boom"
    assert kwargs["user_id"] == "u1"
    assert kwargs["is_timeout"] is False


def test_notify_scheduler_failure_timeout_flag():
    with _patched_notify() as mock_notify:
        notify_scheduler_failure("feiniu", "超时", timeout=True)
    _, kwargs = mock_notify.call_args
    assert kwargs["is_timeout"] is True


def test_notify_scheduler_failure_swallows_exceptions():
    with _patched_notify() as mock_notify:
        mock_notify.side_effect = RuntimeError("boom")
        # 不应抛出
        notify_scheduler_failure("x", "y")


def test_notify_batch_sync_summary_calls_notify():
    with _patched_notify() as mock_notify:
        notify_batch_sync_summary("feiniu", total=10, succeeded=8, failed=1, skipped=1)
    args, kwargs = mock_notify.call_args
    assert args[0] == "batch_sync_summary"
    assert kwargs["total"] == 10
    assert kwargs["succeeded"] == 8
    assert kwargs["failed"] == 1
    assert kwargs["skipped"] == 1


def test_notify_batch_sync_summary_default_skipped_zero():
    with _patched_notify() as mock_notify:
        notify_batch_sync_summary("trakt", total=5, succeeded=5, failed=0)
    _, kwargs = mock_notify.call_args
    assert kwargs["skipped"] == 0


def test_notify_queue_size_warning_calls_notify():
    with _patched_notify() as mock_notify:
        notify_queue_size_warning(pending_count=150, threshold=100)
    args, kwargs = mock_notify.call_args
    assert args[0] == "queue_size_warning"
    assert kwargs["pending_count"] == 150
    assert kwargs["threshold"] == 100


def test_notify_source_event_failed():
    with _patched_notify() as mock_notify:
        notify_source_event("trakt", "failed", user_id="u1", error_message="timeout")
    args, _ = mock_notify.call_args
    assert args[0] == "source_fetch_failed"


def test_notify_source_event_empty():
    with _patched_notify() as mock_notify:
        notify_source_event("feiniu", "empty", message="无记录")
    args, _ = mock_notify.call_args
    assert args[0] == "source_fetch_empty"


def test_notify_source_event_swallows_exceptions():
    with _patched_notify() as mock_notify:
        mock_notify.side_effect = RuntimeError("boom")
        notify_source_event("x", "failed")


# ─────────────────────────────────────────────────────────────────────────
# match_ambiguous 触发逻辑
# ─────────────────────────────────────────────────────────────────────────


def _make_trace_with_candidates(scores: list[float]):
    """构造含指定分数候选的 MatchTrace"""
    from app.services.sync_service.match_trace import MatchCandidate, MatchTrace

    trace = MatchTrace(
        request_title="test",
        request_ori_title="",
        request_season=1,
        request_episode=1,
    )
    step = trace.start_step("api_search")
    for i, score in enumerate(scores):
        step.candidates.append(
            MatchCandidate(
                subject_id=str(1000 + i),
                name=f"item{i}",
                name_cn=f"条目{i}",
                score=score,
            )
        )
    trace._finish_current_step()
    trace.final_subject_id = "1000"
    trace.finish()
    return trace


def test_match_ambiguous_triggered_when_scores_close():
    """top1/top2 分数差 < 0.05 时触发通知"""
    from app.models.sync import CustomItem
    from app.services.sync_service import SyncService

    trace = _make_trace_with_candidates([0.95, 0.93])  # 差 0.02
    item = MagicMock(spec=CustomItem)
    item.source = "trakt"
    svc = SyncService.__new__(SyncService)

    with patch(
        "app.services.notification_service.notification_service.notify"
    ) as mock_notify:
        svc._maybe_notify_match_ambiguous(trace, item, "trakt")

    mock_notify.assert_called_once()
    args, kwargs = mock_notify.call_args
    assert args[0] == "match_ambiguous"
    assert kwargs["top1_score"] == 0.95
    assert kwargs["top2_score"] == 0.93
    assert kwargs["score_diff"] == 0.02


def test_match_ambiguous_not_triggered_when_scores_far():
    """top1/top2 分数差 >= 0.05 时不触发"""
    from app.models.sync import CustomItem
    from app.services.sync_service import SyncService

    trace = _make_trace_with_candidates([0.95, 0.80])  # 差 0.15
    item = MagicMock(spec=CustomItem)
    item.source = "trakt"
    svc = SyncService.__new__(SyncService)

    with patch(
        "app.services.notification_service.notification_service.notify"
    ) as mock_notify:
        svc._maybe_notify_match_ambiguous(trace, item, "trakt")

    mock_notify.assert_not_called()


def test_match_ambiguous_not_triggered_with_single_candidate():
    """仅一个候选时不触发"""
    from app.models.sync import CustomItem
    from app.services.sync_service import SyncService

    trace = _make_trace_with_candidates([0.95])
    item = MagicMock(spec=CustomItem)
    item.source = "trakt"
    svc = SyncService.__new__(SyncService)

    with patch(
        "app.services.notification_service.notification_service.notify"
    ) as mock_notify:
        svc._maybe_notify_match_ambiguous(trace, item, "trakt")

    mock_notify.assert_not_called()


def test_match_ambiguous_swallows_exceptions():
    """trace 异常时不应抛出"""
    from app.models.sync import CustomItem
    from app.services.sync_service import SyncService

    svc = SyncService.__new__(SyncService)  # 跳过 __init__
    item = MagicMock(spec=CustomItem)
    item.source = "trakt"

    # 传 None 作为 trace，应被 try/except 吞掉
    svc._maybe_notify_match_ambiguous(None, item, "trakt")  # type: ignore[arg-type]
