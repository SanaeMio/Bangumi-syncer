"""_sediment_pending_candidate 触发通知测试

验证：
- 沉淀成功后调用 send_notify('pending_candidate', ...)
- 候选为空时不通知
- 沉淀失败时不通知
- 通知失败不影响主流程
"""

from unittest.mock import patch

from app.models.sync import CustomItem
from app.services.sync_service import SyncService
from app.services.sync_service.match_trace import MatchCandidate, MatchTrace


def _make_item() -> CustomItem:
    return CustomItem(
        media_type="episode",
        title="测试番剧",
        season=2,
        episode=5,
        release_date="",
        user_name="tester",
        sync_action="mark_watching",
    )


def _make_trace_with_candidates() -> MatchTrace:
    trace = MatchTrace(
        request_title="测试番剧",
        request_ori_title="",
        request_season=2,
        request_episode=5,
    )
    step = trace.start_step("api_search")
    step.status = "miss"
    step.candidates = [
        MatchCandidate(
            subject_id="386809",
            name="我推的孩子",
            name_cn="我推的孩子",
            score=0.85,
        ),
        MatchCandidate(
            subject_id="123456",
            name="其他候选",
            name_cn="其他候选",
            score=0.6,
        ),
    ]
    trace.finish()
    return trace


def _make_trace_empty() -> MatchTrace:
    trace = MatchTrace(
        request_title="测试番剧",
        request_ori_title="",
        request_season=2,
        request_episode=5,
    )
    step = trace.start_step("api_search")
    step.status = "miss"
    step.candidates = []
    trace.finish()
    return trace


class TestSedimentPendingCandidateNotification:
    """_sediment_pending_candidate 通知触发测试"""

    def _make_service(self) -> SyncService:
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            return SyncService()

    def test_sediment_success_triggers_notification(self):
        """沉淀成功后触发 pending_candidate 通知"""
        svc = self._make_service()
        item = _make_item()
        trace = _make_trace_with_candidates()

        with patch(
            "app.services.sync_service.database_manager.log_pending_candidate",
            return_value=1,
        ) as mock_log:
            with patch("app.services.sync_service.send_notify") as mock_notify:
                svc._sediment_pending_candidate(item, "plex", trace)

        # 验证沉淀写入
        mock_log.assert_called_once()
        # 验证通知被触发
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert call_args.args[0] == "pending_candidate"
        assert call_args.args[1] is item
        assert call_args.args[2] == "plex"
        kwargs = call_args.kwargs
        assert kwargs["candidates_count"] == 2
        assert kwargs["top_candidate_id"] == "386809"
        assert kwargs["top_candidate_name"] == "我推的孩子"

    def test_sediment_empty_candidates_no_notification(self):
        """无候选时不沉淀也不通知"""
        svc = self._make_service()
        item = _make_item()
        trace = _make_trace_empty()

        with patch(
            "app.services.sync_service.database_manager.log_pending_candidate"
        ) as mock_log:
            with patch("app.services.sync_service.send_notify") as mock_notify:
                svc._sediment_pending_candidate(item, "plex", trace)

        mock_log.assert_not_called()
        mock_notify.assert_not_called()

    def test_sediment_db_failure_no_notification(self):
        """沉淀写入失败时不通知"""
        svc = self._make_service()
        item = _make_item()
        trace = _make_trace_with_candidates()

        with patch(
            "app.services.sync_service.database_manager.log_pending_candidate",
            side_effect=Exception("db error"),
        ) as mock_log:
            with patch("app.services.sync_service.send_notify") as mock_notify:
                # 不应抛出异常（不影响主流程）
                svc._sediment_pending_candidate(item, "plex", trace)

        mock_log.assert_called_once()
        mock_notify.assert_not_called()

    def test_sediment_notify_failure_does_not_crash(self):
        """通知发送失败不影响主流程"""
        svc = self._make_service()
        item = _make_item()
        trace = _make_trace_with_candidates()

        with patch(
            "app.services.sync_service.database_manager.log_pending_candidate",
            return_value=1,
        ):
            with patch(
                "app.services.sync_service.send_notify",
                side_effect=Exception("notify error"),
            ):
                # 不应抛出异常
                svc._sediment_pending_candidate(item, "plex", trace)

    def test_sediment_top_candidate_falls_back_to_name(self):
        """首选候选 name_cn 为空时回退到 name"""
        svc = self._make_service()
        item = _make_item()
        trace = MatchTrace(
            request_title="测试番剧",
            request_ori_title="",
            request_season=2,
            request_episode=5,
        )
        step = trace.start_step("api_search")
        step.status = "miss"
        step.candidates = [
            MatchCandidate(
                subject_id="111",
                name="english name",
                name_cn="",
                score=0.7,
            ),
        ]
        trace.finish()

        with patch(
            "app.services.sync_service.database_manager.log_pending_candidate",
            return_value=1,
        ):
            with patch("app.services.sync_service.send_notify") as mock_notify:
                svc._sediment_pending_candidate(item, "plex", trace)

        kwargs = mock_notify.call_args.kwargs
        assert kwargs["top_candidate_name"] == "english name"
        assert kwargs["top_candidate_id"] == "111"
        assert kwargs["candidates_count"] == 1
