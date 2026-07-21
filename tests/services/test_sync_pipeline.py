"""同步记录详情流水线重构测试

验证：
- CustomItem.raw_payload 透传到 trace.receive step
- trace.steps 包含 10 个阶段（receive/normalize/custom_mapping/bangumi_data/
  api_search/post_search/cross_season/episode_resolve/sync_action/result）
- 失败场景流水线终止于 result step（status=miss）
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.models.sync import CustomItem
from app.services.sync_service import SyncService


@pytest.fixture
def mock_config():
    with patch("app.services.sync_service.config_manager") as mock_cm:

        def get_side_effect(section, key, fallback=None):
            if section == "sync" and key == "mode":
                return "single"
            elif section == "sync" and key == "blocked_keywords":
                return ""
            elif section == "sync" and key == "movie_mark_subject_completed":
                return True
            elif section == "sync" and key == "movie_playback_start_mark_watching":
                return True
            return fallback

        mock_cm.get.side_effect = get_side_effect
        mock_cm.get_single_mode_media_usernames.return_value = ["testuser"]
        mock_cm.get_user_mappings.return_value = {}
        mock_cm.get_bangumi_configs.return_value = {}
        yield mock_cm


@pytest.fixture
def mock_database():
    with patch("app.services.sync_service.database_manager") as mock_db:
        mock_db.log_sync_record.return_value = None
        yield mock_db


@contextmanager
def _make_mock_bangumi_api():
    """构造 mock BangumiApi：搜索命中 + 集数命中 + mark 返回 1"""
    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = [
            {
                "id": 577198,
                "name": "完美世界 第六季",
                "name_cn": "完美世界 第六季",
                "platform": "WEB",
                "date": "2025-01-01",
                "eps": 52,
            }
        ]
        mock_instance.get_subject.return_value = {
            "id": 577198,
            "name": "完美世界 第六季",
            "name_cn": "完美世界 第六季",
            "type": 2,
            "eps": 52,
            "platform": "WEB",
        }
        mock_instance.get_episodes.return_value = {
            "data": [
                {"id": 1552069, "ep": 1, "sort": 270, "name": "第270话"},
            ],
            "total": 1,
        }
        mock_instance.get_related_subjects.return_value = []
        mock_instance.get_target_season_episode_id.return_value = (577198, 1552069)
        mock_instance.mark_episode_watched.return_value = 1
        mock_instance.ensure_subject_watching.return_value = 1
        mock_instance.get_movie_main_episode_id.return_value = ("577198", 1552069)
        mock_instance.get_subject_collection.return_value = {}
        mock_api.return_value = mock_instance
        yield mock_api


def test_pipeline_receive_step_carries_processed_payload(mock_config, mock_database):
    """receive step 应携带 sync 开始时的输入字段（processed_payload）"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="完美世界",
        ori_title=None,
        season=1,
        episode=270,
        media_type="episode",
        release_date="",
        source="fongmi",
    )

    with _make_mock_bangumi_api():
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

    assert result.status == "success"

    # 从 log_sync_record 调用中提取 match_trace
    call_args = mock_database.log_sync_record.call_args
    assert call_args is not None, "log_sync_record 未被调用"
    trace_data = call_args.kwargs.get("match_trace") or {}
    assert trace_data, "match_trace 为空"

    # receive step 应是第一步
    steps = trace_data.get("steps") or []
    assert len(steps) >= 1, "trace.steps 为空"
    receive_step = steps[0]
    assert receive_step["stage"] == "receive"
    assert receive_step["status"] == "hit"
    assert receive_step["processed_payload"] is not None
    assert receive_step["processed_payload"]["source"] == "fongmi"
    assert receive_step["processed_payload"]["title"] == "完美世界"
    assert receive_step["processed_payload"]["season"] == 1
    assert receive_step["processed_payload"]["episode"] == 270


def test_pipeline_result_step_carries_episode_and_links(mock_config, mock_database):
    """result step 应携带集数 + subject/ep 链接（processed_payload）"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="完美世界",
        ori_title=None,
        season=1,
        episode=270,
        media_type="episode",
        release_date="",
        source="fongmi",
    )

    with _make_mock_bangumi_api():
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

    assert result.status == "success"
    call_args = mock_database.log_sync_record.call_args
    trace_data = call_args.kwargs.get("match_trace") or {}
    steps = trace_data.get("steps") or []
    result_step = steps[-1]
    assert result_step["stage"] == "result"
    assert result_step["status"] == "hit"
    payload = result_step["processed_payload"]
    assert payload is not None
    assert payload["status"] == "success"
    assert payload["episode"] == "S01E270"
    assert payload["subject_id"] == "577198"
    assert payload["episode_id"] == "1552069"
    assert payload["subject_url"] == "https://bgm.tv/subject/577198"
    assert payload["episode_url"] == "https://bgm.tv/ep/1552069"


def test_pipeline_episode_resolve_step_records_change(mock_config, mock_database):
    """episode_resolve step 应记录输入→输出变更过程（processed_payload）"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="完美世界",
        ori_title=None,
        season=1,
        episode=270,
        media_type="episode",
        release_date="",
        source="fongmi",
    )

    with _make_mock_bangumi_api():
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

    assert result.status == "success"
    call_args = mock_database.log_sync_record.call_args
    trace_data = call_args.kwargs.get("match_trace") or {}
    steps = trace_data.get("steps") or []
    ep_step = next((s for s in steps if s["stage"] == "episode_resolve"), None)
    assert ep_step is not None, "流水线缺少 episode_resolve step"
    payload = ep_step["processed_payload"]
    assert payload is not None
    assert payload["input_subject_id"] == "577198"
    assert payload["request_season"] == 1
    assert payload["request_episode"] == 270
    assert payload["output_subject_id"] == "577198"
    assert payload["output_episode_id"] == "1552069"
    assert payload["changed"] is False
    assert payload["subject_url"] == "https://bgm.tv/subject/577198"
    assert payload["episode_url"] == "https://bgm.tv/ep/1552069"


def test_pipeline_full_stages_on_success(mock_config, mock_database):
    """成功场景：流水线应包含 receive/normalize/custom_mapping/bangumi_data/
    api_search/episode_resolve/sync_action/result 等阶段"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="完美世界",
        ori_title=None,
        season=1,
        episode=270,
        media_type="episode",
        release_date="",
        source="fongmi",
    )

    with _make_mock_bangumi_api():
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

    assert result.status == "success"
    call_args = mock_database.log_sync_record.call_args
    trace_data = call_args.kwargs.get("match_trace") or {}
    stages = [s["stage"] for s in trace_data.get("steps") or []]

    # 必备阶段
    assert "receive" in stages
    assert "normalize" in stages
    assert "custom_mapping" in stages
    assert "api_search" in stages
    assert "episode_resolve" in stages
    assert "sync_action" in stages
    assert "result" in stages

    # result step 应为最后一步且 status=hit
    last_step = trace_data["steps"][-1]
    assert last_step["stage"] == "result"
    assert last_step["status"] == "hit"

    # final_status/final_message 应被填充
    assert trace_data.get("final_status") == "success"
    assert trace_data.get("final_message")


def test_pipeline_terminates_with_result_miss_on_episode_not_found(
    mock_config, mock_database
):
    """集数未找到场景：流水线应以 result step（status=miss）终止"""
    service = SyncService()

    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = [{"id": 123, "name": "Test"}]
        mock_instance.get_target_season_episode_id.return_value = (None, None)
        mock_instance.find_episode_across_seasons.return_value = None
        mock_api.return_value = mock_instance

        item = CustomItem(
            user_name="testuser",
            title="Test Anime",
            ori_title="",
            season=1,
            episode=999,
            media_type="episode",
            release_date="2024-01-01",
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
    call_args = mock_database.log_sync_record.call_args
    trace_data = call_args.kwargs.get("match_trace") or {}
    stages = [s["stage"] for s in trace_data.get("steps") or []]

    # 必须有 result step
    assert "result" in stages, f"流水线未以 result 终止: {stages}"
    result_step = trace_data["steps"][-1]
    assert result_step["stage"] == "result"
    assert result_step["status"] == "miss"
    assert trace_data.get("final_status") == "error"


def test_pipeline_terminates_with_result_miss_on_subject_not_found(
    mock_config, mock_database
):
    """番剧未找到场景：流水线也应以 result step（status=miss）终止"""
    service = SyncService()

    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = None
        mock_instance.get_target_season_episode_id.return_value = (None, None)
        mock_api.return_value = mock_instance

        item = CustomItem(
            user_name="testuser",
            title="不存在的番剧",
            ori_title="",
            season=1,
            episode=1,
            media_type="episode",
            release_date="2024-01-01",
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
    call_args = mock_database.log_sync_record.call_args
    trace_data = call_args.kwargs.get("match_trace") or {}
    stages = [s["stage"] for s in trace_data.get("steps") or []]

    assert "result" in stages, f"流水线未以 result 终止: {stages}"
    result_step = trace_data["steps"][-1]
    assert result_step["stage"] == "result"
    assert result_step["status"] == "miss"


def test_match_step_to_dict_serializes_new_fields():
    """MatchStep.to_dict 应序列化 raw_payload 和 processed_payload 字段"""
    from app.services.sync_service.match_trace import MatchStep

    step = MatchStep(
        stage="receive",
        status="hit",
        reason="test",
        raw_payload={"source": "fongmi"},
        processed_payload={"title": "T"},
    )
    d = step.to_dict()
    assert d["raw_payload"] == {"source": "fongmi"}
    assert d["processed_payload"] == {"title": "T"}


def test_match_trace_to_dict_serializes_final_status():
    """MatchTrace.to_dict 应序列化 final_status/final_message/final_action"""
    from app.services.sync_service.match_trace import MatchTrace

    trace = MatchTrace()
    trace.final_status = "success"
    trace.final_message = "已标记为看过"
    trace.final_action = "1"
    d = trace.to_dict()
    assert d["final_status"] == "success"
    assert d["final_message"] == "已标记为看过"
    assert d["final_action"] == "1"
