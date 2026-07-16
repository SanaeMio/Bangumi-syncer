"""匹配记录与测试匹配的集成测试

验证：
1. test_match 写入匹配记录（source=test-match）
2. 新媒体类型（ova/oad/real_action）在 sync_custom_item 中的路由
3. MatchTrace 新字段是否正确填充
"""

from unittest.mock import MagicMock, patch

from app.models.sync import CustomItem, SyncResponse
from app.services.sync_service import SyncService
from app.services.sync_service.match_trace import MatchTrace


class TestTestMatchWritesRecord:
    """test_match 应写入匹配记录"""

    def _make_service(self):
        svc = SyncService()
        return svc

    def test_test_match_writes_success_record(self):
        """test_match 命中时写入 success 记录"""
        svc = self._make_service()
        item = CustomItem(
            media_type="episode",
            title="测试番剧",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="test_user",
        )
        with patch("app.services.sync_service.database_manager") as mock_db:
            with patch.object(
                svc, "_find_subject_id", return_value=("12345", False, "")
            ):
                result = svc.test_match(item)
        assert result["subject_id"] == "12345"
        # 验证 log_sync_record 被调用
        mock_db.log_sync_record.assert_called_once()
        call_kwargs = mock_db.log_sync_record.call_args
        assert call_kwargs.kwargs["source"] == "test-match"
        assert call_kwargs.kwargs["status"] == "success"
        assert call_kwargs.kwargs["subject_id"] == "12345"
        assert (
            call_kwargs.kwargs["match_method"] == "custom_mapping"
            or call_kwargs.kwargs["match_method"] == "failed"
        )

    def test_test_match_writes_error_record_on_failure(self):
        """test_match 未命中时写入 error 记录"""
        svc = self._make_service()
        item = CustomItem(
            media_type="episode",
            title="不存在的番剧",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="test_user",
        )
        with patch("app.services.sync_service.database_manager") as mock_db:
            with patch.object(
                svc, "_find_subject_id", return_value=(None, False, "未找到")
            ):
                result = svc.test_match(item)
        assert result["subject_id"] is None
        mock_db.log_sync_record.assert_called_once()
        call_kwargs = mock_db.log_sync_record.call_args
        assert call_kwargs.kwargs["source"] == "test-match"
        assert call_kwargs.kwargs["status"] == "error"

    def test_test_match_trace_has_new_fields(self):
        """test_match 的 trace 包含新增字段"""
        svc = self._make_service()
        item = CustomItem(
            media_type="ova",
            title="测试OVA",
            season=1,
            episode=2,
            release_date="2024-03-01",
            user_name="test_user",
        )
        with patch("app.services.sync_service.database_manager"):
            with patch.object(svc, "_find_subject_id", return_value=("999", False, "")):
                result = svc.test_match(item)
        trace = result["trace"]
        assert trace["request_episode"] == 2
        assert trace["request_media_type"] == "ova"
        assert trace["request_release_date"] == "2024-03-01"
        assert trace["request_user_name"] == "test_user"

    def test_test_match_db_error_does_not_crash(self):
        """log_sync_record 失败不影响 test_match 返回"""
        svc = self._make_service()
        item = CustomItem(
            media_type="episode",
            title="测试",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        with patch("app.services.sync_service.database_manager") as mock_db:
            mock_db.log_sync_record.side_effect = Exception("DB error")
            with patch.object(svc, "_find_subject_id", return_value=("1", False, "")):
                result = svc.test_match(item)
        # 仍然正常返回
        assert result["subject_id"] == "1"


class TestMediaTypeRouting:
    """新媒体类型（ova/oad/real_action）的路由测试"""

    def _make_service(self):
        return SyncService()

    def test_ova_accepted_by_normalize(self):
        """ova 类型通过校验"""
        svc = self._make_service()
        item = CustomItem(
            media_type="ova",
            title="测试OVA",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        with patch.object(svc, "_check_user_permission", return_value=True):
            with patch.object(svc, "_is_title_blocked", return_value=False):
                error = svc._normalize_custom_item_params(item)
        assert error is None

    def test_oad_accepted_by_normalize(self):
        """oad 类型通过校验"""
        svc = self._make_service()
        item = CustomItem(
            media_type="oad",
            title="测试OAD",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        with patch.object(svc, "_check_user_permission", return_value=True):
            with patch.object(svc, "_is_title_blocked", return_value=False):
                error = svc._normalize_custom_item_params(item)
        assert error is None

    def test_real_action_accepted_by_normalize(self):
        """real_action 类型通过校验"""
        svc = self._make_service()
        item = CustomItem(
            media_type="real_action",
            title="测试日剧",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        with patch.object(svc, "_check_user_permission", return_value=True):
            with patch.object(svc, "_is_title_blocked", return_value=False):
                error = svc._normalize_custom_item_params(item)
        assert error is None

    def test_unknown_type_rejected(self):
        """未知类型被拒绝"""
        svc = self._make_service()
        item = CustomItem(
            media_type="unknown_type",
            title="测试",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        error = svc._normalize_custom_item_params(item)
        assert error is not None
        assert "不支持" in error.message

    def test_real_action_mark_watching_routes_to_movie_watching(self):
        """real_action + mark_watching 路由到 sync_movie_watching"""
        svc = self._make_service()
        item = CustomItem(
            media_type="real_action",
            title="真人版电影",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
            sync_action="mark_watching",
        )
        with patch.object(
            svc,
            "sync_movie_watching",
            return_value=SyncResponse(status="success", message="ok"),
        ) as mock:
            result = svc.sync_custom_item(item)
        mock.assert_called_once()
        assert result.status == "success"

    def test_ova_mark_watching_not_routed_to_movie(self):
        """ova + mark_watching 不走 movie 路径（返回 ignored）"""
        svc = self._make_service()
        item = CustomItem(
            media_type="ova",
            title="测试OVA",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
            sync_action="mark_watching",
        )
        result = svc.sync_custom_item(item)
        assert result.status == "ignored"


class TestMatchTraceNewFields:
    """MatchTrace 新字段测试"""

    def test_match_trace_to_dict_includes_new_fields(self):
        """to_dict 包含新增字段"""
        trace = MatchTrace(
            request_title="标题",
            request_ori_title="ori",
            request_season=2,
            request_episode=5,
            request_media_type="ova",
            request_release_date="2024-01-01",
            request_user_name="user1",
            request_platform_hint="plex",
        )
        d = trace.to_dict()
        assert d["request_episode"] == 5
        assert d["request_media_type"] == "ova"
        assert d["request_release_date"] == "2024-01-01"
        assert d["request_user_name"] == "user1"

    def test_match_trace_default_values(self):
        """默认值正确"""
        trace = MatchTrace()
        d = trace.to_dict()
        assert d["request_episode"] == 0
        assert d["request_media_type"] == ""
        assert d["request_release_date"] == ""
        assert d["request_user_name"] == ""


class TestLogSyncRecordFacadeForwardsMatchFields:
    """log_sync_record facade 正确转发 match_* 字段"""

    def test_facade_forwards_all_match_fields(self):
        """facade 转发 match_method/match_score/match_platform/match_trace"""
        from app.core.database import DatabaseManager

        dbm = DatabaseManager.__new__(DatabaseManager)
        dbm._sync = MagicMock()
        dbm._sync.log_sync_record.return_value = 42

        trace_dict = {"steps": [], "final_subject_id": "123"}
        result = dbm.log_sync_record(
            user_name="u",
            title="t",
            ori_title=None,
            season=1,
            episode=1,
            subject_id="123",
            status="success",
            match_method="api_search",
            match_score=0.9,
            match_platform="TV",
            match_trace=trace_dict,
        )

        assert result == 42
        dbm._sync.log_sync_record.assert_called_once_with(
            user_name="u",
            title="t",
            ori_title=None,
            season=1,
            episode=1,
            subject_id="123",
            episode_id=None,
            status="success",
            message="",
            source="custom",
            media_type="episode",
            bgm_title="",
            match_method="api_search",
            match_score=0.9,
            match_platform="TV",
            match_trace=trace_dict,
        )
