"""匹配记录与测试匹配的集成测试

验证：
1. test_match 不写入 sync_records（避免污染统计）
2. 新媒体类型（ova/oad/real_action）在 sync_custom_item 中的路由
3. MatchTrace 新字段是否正确填充
"""

from unittest.mock import MagicMock, patch

from app.models.sync import CustomItem, SyncResponse
from app.services.sync_service import SyncService
from app.services.sync_service.match_trace import MatchTrace


class TestTestMatchNoDbWrite:
    """test_match 不应写入数据库，避免污染 dashboard 统计与同步记录列表"""

    def _make_service(self):
        return SyncService()

    def test_test_match_does_not_write_record_on_success(self):
        """test_match 命中时不写库"""
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
        mock_db.log_sync_record.assert_not_called()

    def test_test_match_does_not_write_record_on_failure(self):
        """test_match 未命中时也不写库"""
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
        mock_db.log_sync_record.assert_not_called()

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
        with patch.object(svc, "_find_subject_id", return_value=("999", False, "")):
            result = svc.test_match(item)
        trace = result["trace"]
        assert trace["request_episode"] == 2
        assert trace["request_media_type"] == "ova"
        assert trace["request_release_date"] == "2024-03-01"
        assert trace["request_user_name"] == "test_user"


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
        with patch.object(svc, "_check_user_permission", return_value=(True, "")):
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
        with patch.object(svc, "_check_user_permission", return_value=(True, "")):
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
        with patch.object(svc, "_check_user_permission", return_value=(True, "")):
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

    def test_final_episode_id_in_to_dict(self):
        """final_episode_id 出现在 to_dict 输出中"""
        trace = MatchTrace(final_subject_id="123", final_episode_id="456")
        d = trace.to_dict()
        assert d["final_subject_id"] == "123"
        assert d["final_episode_id"] == "456"

    def test_final_episode_id_default_none(self):
        """final_episode_id 默认为 None"""
        trace = MatchTrace()
        assert trace.final_episode_id is None
        assert trace.to_dict()["final_episode_id"] is None


class TestSyncMovieWatchingMatchFields:
    """剧场版分支补传 match_* 字段测试"""

    def _make_service(self):
        return SyncService()

    def test_movie_watching_success_passes_match_fields(self):
        """剧场版标记在看成功时传递 match_* 字段到 log_sync_record"""
        svc = self._make_service()
        item = CustomItem(
            media_type="movie",
            title="测试剧场版",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
            sync_action="mark_watching",
        )
        with patch.object(svc, "_check_user_permission", return_value=(True, "")):
            with patch.object(svc, "_is_title_blocked", return_value=False):
                with patch.object(
                    svc,
                    "_find_matching_subject",
                    return_value=("12345", False, None, MagicMock()),
                ):
                    with patch.object(
                        svc,
                        "_get_bangumi_api_for_user",
                        return_value=MagicMock(
                            ensure_subject_watching=MagicMock(return_value=1)
                        ),
                    ):
                        with patch(
                            "app.services.sync_service.database_manager"
                        ) as mock_db:
                            with patch("app.services.sync_service.send_notify"):
                                with patch(
                                    "app.services.sync_service.config_manager"
                                ) as mock_cfg:
                                    mock_cfg.get.return_value = True
                                    result = svc.sync_custom_item(item)
        assert result.status == "success"
        # 验证 log_sync_record 被调用且传了 match_method
        mock_db.log_sync_record.assert_called_once()
        call_kwargs = mock_db.log_sync_record.call_args.kwargs
        # trace 作为返回值传递，match_method/match_trace 来自 trace
        assert "match_method" in call_kwargs
        assert "match_trace" in call_kwargs


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


class TestSeasonOneCandidateSelection:
    """season=1 时 API 搜索候选筛选测试

    场景：搜索"凡人修仙传" season=1，API 按热度返回第一条是"凡人修仙传 第五季"，
    应在候选列表里改选标题不含季度后缀的第一季本体。
    """

    def _make_service(self):
        return SyncService()

    def _make_candidates(self):
        """模拟 bgm_search 返回的候选列表（已按热度排序）"""
        return [
            {
                "id": 607915,
                "name": "凡人修仙传 第五季",
                "name_cn": "凡人修仙传 第五季",
                "platform": "WEB",
                "date": "2026-06-13",
            },
            {
                "id": 401234,
                "name": "凡人修仙传 第四季",
                "name_cn": "凡人修仙传 第四季",
                "platform": "WEB",
                "date": "2024-01-01",
            },
            {
                "id": 328088,
                "name": "凡人修仙传",
                "name_cn": "凡人修仙传",
                "platform": "WEB",
                "date": "2020-07-15",
            },
        ]

    def test_season1_picks_first_season_body_when_top_is_sequel(self):
        """首条为第五季时，改选无季度后缀的第一季本体"""
        svc = self._make_service()
        item = CustomItem(
            media_type="episode",
            title="凡人修仙传",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        with patch("app.services.sync_service.mapping_service") as mock_mapping:
            mock_mapping.find_mapping.return_value = (None, "", "")
            with patch("app.services.sync_service.config_manager") as mock_cfg:
                mock_cfg.get.side_effect = lambda s, k, fallback=None: {
                    ("bangumi_data", "enabled"): False,
                    ("sync", "enable_real_action"): False,
                }.get((s, k), fallback)
                with patch.object(
                    svc,
                    "_get_bangumi_api_for_user",
                    return_value=MagicMock(
                        bgm_search=MagicMock(return_value=self._make_candidates())
                    ),
                ):
                    with patch.object(
                        svc,
                        "_sort_candidates_by_platform",
                        side_effect=lambda data, **kw: data,
                    ):
                        subject_id, is_matched, err = svc._find_subject_id(item)
        assert err == ""
        assert subject_id == 328088  # 第一季本体
        assert is_matched is True

    def test_season1_keeps_top_when_no_seasonless_candidate(self):
        """候选列表里无无季度后缀条目时，保持首条"""
        svc = self._make_service()
        item = CustomItem(
            media_type="episode",
            title="某番剧",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        candidates = [
            {
                "id": 111,
                "name": "某番剧 第二季",
                "name_cn": "某番剧 第二季",
                "platform": "TV",
                "date": "",
            },
            {
                "id": 222,
                "name": "某番剧 第三季",
                "name_cn": "某番剧 第三季",
                "platform": "TV",
                "date": "",
            },
        ]
        with patch("app.services.sync_service.mapping_service") as mock_mapping:
            mock_mapping.find_mapping.return_value = (None, "", "")
            with patch("app.services.sync_service.config_manager") as mock_cfg:
                mock_cfg.get.side_effect = lambda s, k, fallback=None: {
                    ("bangumi_data", "enabled"): False,
                    ("sync", "enable_real_action"): False,
                }.get((s, k), fallback)
                with patch.object(
                    svc,
                    "_get_bangumi_api_for_user",
                    return_value=MagicMock(
                        bgm_search=MagicMock(return_value=candidates)
                    ),
                ):
                    with patch.object(
                        svc,
                        "_sort_candidates_by_platform",
                        side_effect=lambda data, **kw: data,
                    ):
                        subject_id, is_matched, err = svc._find_subject_id(item)
        assert err == ""
        assert subject_id == 111  # 保持首条
        assert is_matched is False

    def test_season1_keeps_top_when_top_is_seasonless(self):
        """首条本身无季度后缀时，直接采用（无需改选）"""
        svc = self._make_service()
        item = CustomItem(
            media_type="episode",
            title="鬼滅の刃",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        candidates = [
            {
                "id": 333,
                "name": "鬼滅の刃",
                "name_cn": "鬼灭之刃",
                "platform": "TV",
                "date": "2019-04-06",
            },
        ]
        with patch("app.services.sync_service.mapping_service") as mock_mapping:
            mock_mapping.find_mapping.return_value = (None, "", "")
            with patch("app.services.sync_service.config_manager") as mock_cfg:
                mock_cfg.get.side_effect = lambda s, k, fallback=None: {
                    ("bangumi_data", "enabled"): False,
                    ("sync", "enable_real_action"): False,
                }.get((s, k), fallback)
                with patch.object(
                    svc,
                    "_get_bangumi_api_for_user",
                    return_value=MagicMock(
                        bgm_search=MagicMock(return_value=candidates)
                    ),
                ):
                    with patch.object(
                        svc,
                        "_sort_candidates_by_platform",
                        side_effect=lambda data, **kw: data,
                    ):
                        subject_id, is_matched, err = svc._find_subject_id(item)
        assert err == ""
        assert subject_id == 333
        # 首条无季度后缀，不触发改选逻辑，is_matched 保持 False
        assert is_matched is False
