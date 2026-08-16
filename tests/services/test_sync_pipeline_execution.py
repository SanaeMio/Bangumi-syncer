"""SyncPipeline（执行阶段第二段管线）测试

验证：
- 顺序执行 episode_resolve → cross_season → sync_action → result，每步
  trace 记录进出数据与耗时，final_* 由 ResultStep 统一结算
- 终态语义：error / miss 短路（terminal），queued 无 result step
- step 抛异常时记录 error step 后重抛（编排器统一异常处理）
- 集成：queued / 认证失败在编排器层的分支收尾
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.sync import CustomItem
from app.services.sync_service import SyncService
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.match_trace import MatchTrace
from app.services.sync_service.pipeline import SyncPipeline
from app.services.sync_service.retry import MARK_QUEUED
from app.services.sync_service.steps import (
    CrossSeasonStep,
    EpisodeResolveStep,
    ResultStep,
    SyncActionStep,
)
from app.utils.bangumi_api.collection import _PendingSyncQueued


def _make_ctx(**overrides):
    """构造执行阶段上下文（默认走命中路径）"""
    bgm = MagicMock()
    bgm.find_episode_across_seasons.return_value = None
    bgm.get_subject.return_value = {
        "id": 100,
        "name": "测试番剧",
        "name_cn": "测试番剧",
    }
    service = MagicMock()
    service._resolve_season_episode.return_value = ("100", "200")
    service._retry_mark_episode.return_value = 1
    service._format_mark_status_message.return_value = "已标记为看过"

    item = CustomItem(
        user_name="u",
        title="测试番剧",
        season=1,
        episode=1,
        media_type="tv",
        release_date="",
    )
    defaults = dict(
        item=item,
        bgm=bgm,
        trace=MatchTrace(),
        service=service,
        actual_source="fongmi",
        subject_id="100",
        is_season_matched_id=False,
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)


class TestSyncPipelineExecution:
    def test_success_flow_records_all_steps_and_final(self):
        """成功流：四步顺序执行，trace 记录进出数据，final_* 由 ResultStep 结算"""
        ctx = _make_ctx()
        pipeline = SyncPipeline(
            [EpisodeResolveStep(), CrossSeasonStep(), SyncActionStep(), ResultStep()]
        )

        terminal = pipeline.run(ctx)

        assert terminal is None
        stages = [s.stage for s in ctx.trace.steps]
        assert stages == ["episode_resolve", "cross_season", "sync_action", "result"]

        ep_step, cross_step, action_step, result_step = ctx.trace.steps
        assert ep_step.status == "hit"
        assert ep_step.outputs["subject_id"] == "100"
        assert ep_step.outputs["episode_id"] == "200"
        assert cross_step.status == "skipped"
        assert cross_step.reason == "集数解析已命中，无需跨季回退"
        assert action_step.status == "hit"
        assert action_step.outputs["mark_status"] == 1
        assert result_step.status == "hit"
        assert result_step.outputs["status"] == "success"
        assert result_step.outputs["bgm_title"] == "测试番剧"
        assert result_step.outputs["message"] == "已标记为看过"

        # 每步自动记录耗时
        for s in ctx.trace.steps:
            assert s.elapsed_ms >= 0

        # 结果链：产出经管线统一 merge 进 current_outputs / step_outputs
        assert ctx.current_outputs["subject_id"] == "100"
        assert ctx.current_outputs["episode_id"] == "200"
        assert ctx.current_outputs["mark_status"] == 1
        assert ctx.current_outputs["message"] == "已标记为看过"
        assert ctx.step_outputs["episode_resolve"]["episode_id"] == "200"
        assert ctx.step_outputs["sync_action"]["mark_status"] == 1
        assert "cross_season" not in ctx.step_outputs  # skipped 不产出

        # final_* 统一结算
        assert ctx.trace.final_episode_id == "200"
        assert ctx.trace.final_action == "1"
        assert ctx.trace.final_status == "success"
        assert ctx.trace.final_message == "已标记为看过"
        assert ctx.trace.final_subject_id is None  # 匹配阶段结算，执行阶段不自动覆写

    def test_episode_resolve_miss_triggers_cross_season_miss_terminal(self):
        """集数解析 miss → 跨季回退 miss → 以 (cross_season, miss) 终态短路"""
        ctx = _make_ctx()
        ctx.service._resolve_season_episode.return_value = ("100", None)
        ctx.bgm.find_episode_across_seasons.return_value = None

        terminal = SyncPipeline(
            [EpisodeResolveStep(), CrossSeasonStep(), SyncActionStep(), ResultStep()]
        ).run(ctx)

        assert terminal is not None
        stage, outcome = terminal
        assert stage == "cross_season"
        assert outcome.status == "miss"
        assert outcome.is_terminal is True
        # 空值产物不覆盖上游（current_outputs 无 episode_id 键）
        assert ctx.current_outputs.get("episode_id") is None
        # sync_action / result 未执行
        stages = [s.stage for s in ctx.trace.steps]
        assert stages == ["episode_resolve", "cross_season"]

    def test_cross_season_hit_changes_subject_and_settles_final(self):
        """跨季命中：改选 subject+ep，ResultStep 覆写 final_*（archive 语义）"""
        ctx = _make_ctx()
        ctx.service._resolve_season_episode.return_value = ("100", None)
        ctx.bgm.find_episode_across_seasons.return_value = ("999", "9981")
        ctx.bgm.last_cross_season_path = "chain"

        terminal = SyncPipeline(
            [EpisodeResolveStep(), CrossSeasonStep(), SyncActionStep(), ResultStep()]
        ).run(ctx)

        assert terminal is None
        # 跨季改选经结果链覆盖上游产物
        assert ctx.current_outputs["subject_id"] == "999"
        assert ctx.current_outputs["episode_id"] == "9981"
        assert ctx.current_outputs["mark_status"] == 1
        assert ctx.trace.final_subject_id == "999"
        assert ctx.trace.final_episode_id == "9981"
        assert ctx.trace.final_match_method == "archive"
        assert ctx.trace.final_match_method_detail == "cross_season_chain"
        cross_step = ctx.trace.steps[1]
        assert cross_step.status == "hit"
        assert cross_step.outputs["match_path"] == "chain"
        assert ctx.step_outputs["cross_season"]["match_path"] == "chain"

    def test_sync_action_queued_is_terminal_without_result_step(self):
        """API 不可达入队：sync_action 以 hit 终态短路，无 result step"""
        ctx = _make_ctx()
        ctx.service._retry_mark_episode.return_value = MARK_QUEUED

        terminal = SyncPipeline(
            [EpisodeResolveStep(), CrossSeasonStep(), SyncActionStep(), ResultStep()]
        ).run(ctx)

        assert terminal is not None
        stage, outcome = terminal
        assert stage == "sync_action"
        assert outcome.status == "hit"
        assert outcome.is_terminal is True
        assert "已入待同步队列" in outcome.reason
        assert ctx.current_outputs.get("mark_status") == MARK_QUEUED
        assert [s.stage for s in ctx.trace.steps] == [
            "episode_resolve",
            "cross_season",
            "sync_action",
        ]

    def test_episode_resolve_auth_failure_terminal_error(self):
        """集数解析认证失败：以 (episode_resolve, error) 终态短路"""
        ctx = _make_ctx()
        ctx.service._resolve_season_episode.side_effect = ValueError(
            "认证失败: access_token 无效"
        )

        terminal = SyncPipeline([EpisodeResolveStep()]).run(ctx)

        assert terminal is not None
        stage, outcome = terminal
        assert stage == "episode_resolve"
        assert outcome.status == "error"
        assert outcome.error_detail["type"] == "auth_failed"
        assert outcome.is_terminal is True

    def test_sync_action_auth_failure_terminal_error(self):
        """标记认证失败：以 (sync_action, error) 终态短路"""
        ctx = _make_ctx()
        ctx.service._retry_mark_episode.side_effect = ValueError(
            "认证失败: access_token 无效"
        )

        terminal = SyncPipeline([SyncActionStep()]).run(ctx)

        assert terminal is not None
        stage, outcome = terminal
        assert stage == "sync_action"
        assert outcome.status == "error"
        assert outcome.error_detail["type"] == "auth_failed"

    def test_step_exception_records_error_then_reraises(self):
        """step 抛非认证异常：记录 error step 后重抛（编排器统一异常处理）"""
        ctx = _make_ctx()
        ctx.service._resolve_season_episode.side_effect = RuntimeError("boom")

        pipeline = SyncPipeline([EpisodeResolveStep(), ResultStep()])
        with pytest.raises(RuntimeError, match="boom"):
            pipeline.run(ctx)

        step = ctx.trace.steps[-1]
        assert step.stage == "episode_resolve"
        assert step.status == "error"
        assert step.error_detail == {"type": "RuntimeError", "message": "boom"}


class TestOrchestratorExecutionBranch:
    """编排器终态分支收尾（集成：SyncService 全流程）"""

    @pytest.fixture
    def mock_config(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_cm,
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[{"section_name": "bangumi"}],
            ),
            patch(
                "app.core.accounts.get_single_mode_media_usernames",
                return_value=["testuser"],
            ),
        ):

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "single"
                return fallback

            mock_cm.get.side_effect = get_side_effect
            yield mock_cm

    @pytest.fixture
    def mock_database(self):
        with patch("app.services.sync_service.database_manager") as mock_db:
            mock_db.log_sync_record.return_value = None
            yield mock_db

    def _run_sync(self, item, mock_bgm_setup):
        """跑完整同步，返回 (result, trace_data, mock_database)"""
        from tests.services.test_sync_pipeline import _make_mock_bangumi_api

        service = SyncService()
        with _make_mock_bangumi_api() as mock_api:
            mock_bgm_setup(mock_api)
            with patch.object(
                service,
                "_get_bangumi_config_for_user",
                return_value={
                    "username": "testuser",
                    "access_token": "tok",
                    "private": True,
                },
            ):
                result = service.sync_custom_item(item, "custom")
        return result

    def test_queued_branch_no_result_step_and_queued_final(
        self, mock_config, mock_database
    ):
        """API 不可达入队：queued 终态、无 result step、final_action=queued"""
        from tests.services.test_sync_pipeline import _make_mock_bangumi_api

        item = CustomItem(
            user_name="testuser",
            title="完美世界",
            season=1,
            episode=1,
            media_type="episode",
            release_date="",
            source="fongmi",
        )
        service = SyncService()

        def setup(mock_api):
            mock_api.return_value.mark_episode_watched.side_effect = _PendingSyncQueued(
                subject_id=577198, ep_id=1552069, reason="api_unreachable"
            )

        with (
            _make_mock_bangumi_api() as mock_api,
            patch(
                "app.services.sync_service.retry.is_replay_enabled", return_value=True
            ),
            patch.object(service, "_get_bangumi_config_for_user") as mock_cfg,
        ):
            mock_cfg.return_value = {
                "username": "testuser",
                "access_token": "tok",
                "private": True,
            }
            setup(mock_api)
            result = service.sync_custom_item(item, "custom")

        assert result.status == "queued"
        trace_data = mock_database.log_sync_record.call_args.kwargs["match_trace"]
        stages = [s["stage"] for s in trace_data["steps"]]
        assert "sync_action" in stages
        assert "result" not in stages  # queued 无 result step
        assert trace_data["final_status"] == "queued"
        assert trace_data["final_action"] == "queued"
        assert trace_data["final_message"] == "API 不可达，已入待同步队列"
        # sync_action step 记录入队原因
        action_step = next(
            s for s in trace_data["steps"] if s["stage"] == "sync_action"
        )
        assert action_step["status"] == "hit"
        assert "已入待同步队列" in action_step["reason"]

    def test_episode_resolve_auth_failure_goes_to_exception_handler(
        self, mock_config, mock_database
    ):
        """集数解析认证失败：走统一异常处理（error 记录 + mark_failed 通知）"""
        from tests.services.test_sync_pipeline import _make_mock_bangumi_api

        item = CustomItem(
            user_name="testuser",
            title="完美世界",
            season=1,
            episode=1,
            media_type="episode",
            release_date="",
            source="fongmi",
        )
        service = SyncService()

        with _make_mock_bangumi_api() as mock_api:
            mock_api.return_value.get_target_season_episode_id.side_effect = ValueError(
                "认证失败: access_token 无效"
            )
            with patch.object(
                service,
                "_get_bangumi_config_for_user",
                return_value={
                    "username": "testuser",
                    "access_token": "tok",
                    "private": True,
                },
            ):
                result = service.sync_custom_item(item, "custom")

        assert result.status == "error"
        # 统一异常处理写 error 记录（match_trace 含 episode_resolve error step）
        call_args = mock_database.log_sync_record.call_args
        assert call_args.kwargs["status"] == "error"
        trace_data = call_args.kwargs["match_trace"]
        ep_step = next(
            s for s in trace_data["steps"] if s["stage"] == "episode_resolve"
        )
        assert ep_step["status"] == "error"
        assert "认证失败" in ep_step["reason"]

    def test_sync_action_auth_failure_returns_error_without_record(
        self, mock_config, mock_database
    ):
        """标记认证失败：直接返回 error 响应，不写 error 记录（现状行为）"""
        from tests.services.test_sync_pipeline import _make_mock_bangumi_api

        item = CustomItem(
            user_name="testuser",
            title="完美世界",
            season=1,
            episode=1,
            media_type="episode",
            release_date="",
            source="fongmi",
        )
        service = SyncService()

        with _make_mock_bangumi_api() as mock_api:
            mock_api.return_value.mark_episode_watched.side_effect = ValueError(
                "认证失败: access_token 无效"
            )
            with patch.object(
                service,
                "_get_bangumi_config_for_user",
                return_value={
                    "username": "testuser",
                    "access_token": "tok",
                    "private": True,
                },
            ):
                result = service.sync_custom_item(item, "custom")

        assert result.status == "error"
        assert "认证失败" in result.message
        # 现状：sync_action 认证失败不写 error 记录
        mock_database.log_sync_record.assert_not_called()
