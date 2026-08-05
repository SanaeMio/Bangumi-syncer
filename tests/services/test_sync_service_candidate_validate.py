"""sync_service：候选确认 subject_id 有效性校验与确认即补发测试

验证：
1. 非数字 subject_id 直接拒绝
2. archive 命中且类型为动画/三次元时放行
3. archive 命中但类型为书籍/音乐时拒绝
4. archive 未命中时降级到 API 校验
5. API 返回条目不存在时拒绝
6. API 异常时降级放行（不阻塞用户）
7. 无可用 Bangumi 配置时降级放行
8. confirm_pending_candidate 校验失败时不写入映射
9. 候选确认即补发：sync_record_id 存在时自动重试
10. 补发成功后回写 sync_records 状态为 retried 并清理 pending_sync_queue
11. 无 sync_record_id 或原记录 success 时不触发补发
"""

from unittest.mock import MagicMock, patch

from app.services.sync_service import SyncService
from app.utils.bangumi_api._archive_shortcut import ShortcutResult
from app.utils.bangumi_constants import SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL


class TestValidateSubjectId:
    """_validate_subject_id 校验逻辑"""

    def setup_method(self):
        self.svc = SyncService()

    def test_rejects_non_numeric(self):
        """非数字 subject_id 直接拒绝"""
        ok, reason = self.svc._validate_subject_id("abc")
        assert ok is False
        assert "纯数字" in reason

    def test_archive_hit_anime_passes(self):
        """archive 命中动画类型放行"""
        with patch(
            "app.utils.bangumi_api._archive_shortcut.archive_shortcut"
        ) as mock_shortcut:
            mock_shortcut.enabled = True
            mock_shortcut.try_get_subject.return_value = ShortcutResult(
                True, {"id": 123, "type": SUBJECT_TYPE_ANIME}, "archive_hit"
            )
            ok, _ = self.svc._validate_subject_id("123")
        assert ok is True

    def test_archive_hit_real_passes(self):
        """archive 命中三次元类型放行"""
        with patch(
            "app.utils.bangumi_api._archive_shortcut.archive_shortcut"
        ) as mock_shortcut:
            mock_shortcut.enabled = True
            mock_shortcut.try_get_subject.return_value = ShortcutResult(
                True, {"id": 456, "type": SUBJECT_TYPE_REAL}, "archive_hit"
            )
            ok, _ = self.svc._validate_subject_id("456")
        assert ok is True

    def test_archive_hit_book_rejected(self):
        """archive 命中但类型为书籍时拒绝"""
        with patch(
            "app.utils.bangumi_api._archive_shortcut.archive_shortcut"
        ) as mock_shortcut:
            mock_shortcut.enabled = True
            mock_shortcut.try_get_subject.return_value = ShortcutResult(
                True, {"id": 789, "type": 1}, "archive_hit"
            )
            ok, reason = self.svc._validate_subject_id("789")
        assert ok is False
        assert "1" in reason

    def test_archive_miss_fallback_to_api(self):
        """archive 未命中时降级到 API 校验"""
        cfg = {"username": "u", "access_token": "t", "private": False}
        dev = {
            "script_proxy": "",
            "ssl_verify": True,
            "bgm_api_proxy": "",
            "bgm_next_proxy": "",
        }
        with (
            patch(
                "app.utils.bangumi_api._archive_shortcut.archive_shortcut"
            ) as mock_shortcut,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=cfg,
            ),
            patch(
                "app.services.sync_service.config_manager.get_dev_http_snapshot",
                return_value=dev,
            ),
            patch("app.services.sync_service.BangumiApi") as MockApi,
        ):
            mock_shortcut.enabled = True
            mock_shortcut.try_get_subject.return_value = ShortcutResult(
                False, None, "archive_miss"
            )
            instance = MockApi.return_value
            instance.get_subject.return_value = {
                "id": 123,
                "type": SUBJECT_TYPE_ANIME,
            }
            ok, _ = self.svc._validate_subject_id("123")
        assert ok is True
        instance.get_subject.assert_called_once_with(123)

    def test_api_returns_empty_rejected(self):
        """API 返回空字典时拒绝"""
        cfg = {"username": "u", "access_token": "t", "private": False}
        dev = {
            "script_proxy": "",
            "ssl_verify": True,
            "bgm_api_proxy": "",
            "bgm_next_proxy": "",
        }
        with (
            patch(
                "app.utils.bangumi_api._archive_shortcut.archive_shortcut"
            ) as mock_shortcut,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=cfg,
            ),
            patch(
                "app.services.sync_service.config_manager.get_dev_http_snapshot",
                return_value=dev,
            ),
            patch("app.services.sync_service.BangumiApi") as MockApi,
        ):
            mock_shortcut.enabled = False
            instance = MockApi.return_value
            instance.get_subject.return_value = {}
            ok, reason = self.svc._validate_subject_id("999")
        assert ok is False
        assert "不存在" in reason

    def test_api_exception_degrades_pass(self):
        """API 异常时降级放行（不阻塞用户）"""
        cfg = {"username": "u", "access_token": "t", "private": False}
        dev = {
            "script_proxy": "",
            "ssl_verify": True,
            "bgm_api_proxy": "",
            "bgm_next_proxy": "",
        }
        with (
            patch(
                "app.utils.bangumi_api._archive_shortcut.archive_shortcut"
            ) as mock_shortcut,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=cfg,
            ),
            patch(
                "app.services.sync_service.config_manager.get_dev_http_snapshot",
                return_value=dev,
            ),
            patch("app.services.sync_service.BangumiApi") as MockApi,
        ):
            mock_shortcut.enabled = False
            instance = MockApi.return_value
            instance.get_subject.side_effect = RuntimeError("network error")
            ok, _ = self.svc._validate_subject_id("123")
        assert ok is True

    def test_no_config_degrades_pass(self):
        """无可用 Bangumi 配置时降级放行"""
        with (
            patch(
                "app.utils.bangumi_api._archive_shortcut.archive_shortcut"
            ) as mock_shortcut,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=None,
            ),
            patch(
                "app.core.accounts.list_bangumi_configs",
                return_value={},
            ),
        ):
            mock_shortcut.enabled = False
            ok, _ = self.svc._validate_subject_id("123")
        assert ok is True


class TestConfirmPendingCandidateValidation:
    """confirm_pending_candidate 在校验失败时不写入映射"""

    def setup_method(self):
        self.svc = SyncService()

    def test_validation_failure_blocks_mapping_write(self):
        """subject_id 校验失败时不写入映射、不更新状态"""
        record = {
            "id": 1,
            "status": "pending",
            "request_title": "测试",
            "request_season": 1,
            "user_name": "u1",
            "source": "plex",
        }
        with (
            patch(
                "app.services.sync_service.database_manager.get_pending_candidate_by_id",
                return_value=record,
            ),
            patch.object(
                self.svc, "_validate_subject_id", return_value=(False, "条目不存在")
            ),
            patch(
                "app.services.sync_service.mapping_service.upsert_single_mapping"
            ) as mock_upsert,
            patch(
                "app.services.sync_service.database_manager.update_pending_candidate_status"
            ) as mock_update,
        ):
            ok, msg = self.svc.confirm_pending_candidate(1, "999")

        assert ok is False
        assert "校验失败" in msg
        mock_upsert.assert_not_called()
        mock_update.assert_not_called()

    def test_validation_pass_writes_mapping(self):
        """subject_id 校验通过时正常写入映射"""
        record = {
            "id": 2,
            "status": "pending",
            "request_title": "测试番",
            "request_season": 1,
            "user_name": "u1",
            "source": "plex",
        }
        with (
            patch(
                "app.services.sync_service.database_manager.get_pending_candidate_by_id",
                return_value=record,
            ),
            patch.object(self.svc, "_validate_subject_id", return_value=(True, "")),
            patch(
                "app.services.sync_service.mapping_service.upsert_single_mapping",
                return_value=True,
            ) as mock_upsert,
            patch(
                "app.services.sync_service.database_manager.update_pending_candidate_status"
            ) as mock_update,
            patch(
                "app.services.sync_service.database_manager.resolve_similar_pending_candidates"
            ),
            patch.object(self.svc, "_auto_replay_after_confirm", return_value=""),
        ):
            ok, msg = self.svc.confirm_pending_candidate(2, "123")

        assert ok is True
        mock_upsert.assert_called_once_with("测试番", "123", 1)
        mock_update.assert_called_once()


class TestAutoReplayAfterConfirm:
    """候选确认即补发（_auto_replay_after_confirm）"""

    def setup_method(self):
        self.svc = SyncService()

    def test_no_sync_record_id_skips_replay(self):
        """无 sync_record_id 时不触发补发"""
        msg = self.svc._auto_replay_after_confirm(None, {}, "title")
        assert msg == ""

    def test_record_not_found_skips_replay(self):
        """sync_record_id 对应记录不存在时跳过"""
        with patch(
            "app.services.sync_service.database_manager.get_sync_record_by_id",
            return_value=None,
        ):
            msg = self.svc._auto_replay_after_confirm(99, {}, "title")
        assert msg == ""

    def test_record_success_skips_replay(self):
        """原记录已是 success 时不重复补发"""
        with patch(
            "app.services.sync_service.database_manager.get_sync_record_by_id",
            return_value={"id": 1, "status": "success"},
        ):
            msg = self.svc._auto_replay_after_confirm(1, {}, "title")
        assert msg == ""

    def test_replay_success_writes_retried_and_cleans_pending(self):
        """补发成功后状态改写为 retried 并清理 pending_sync_queue"""
        record = {
            "id": 10,
            "status": "error",
            "source": "plex",
            "title": "测试",
            "match_trace": {},
        }
        result = MagicMock()
        result.status = "success"
        result.message = "ok"
        with (
            patch(
                "app.services.sync_service.database_manager.get_sync_record_by_id",
                return_value=record,
            ),
            patch.object(self.svc, "_build_retry_item_from_record") as mock_build,
            patch.object(
                self.svc, "sync_custom_item", return_value=result
            ) as mock_sync,
            patch(
                "app.services.sync_service.database_manager.update_sync_record_status"
            ) as mock_update,
            patch.object(self.svc, "_cleanup_pending_for_replay") as mock_cleanup,
        ):
            msg = self.svc._auto_replay_after_confirm(10, {}, "测试")

        assert "补发成功" in msg
        mock_build.assert_called_once()
        mock_sync.assert_called_once()
        mock_update.assert_called_once_with(
            record_id=10,
            status="retried",
            message=mock_update.call_args.kwargs["message"],
        )
        mock_cleanup.assert_called_once_with(10, "error")

    def test_replay_queued_success_cleans_pending_sync_queue(self):
        """原状态为 queued、补发成功时清理 pending_sync_queue"""
        record = {"id": 20, "status": "queued", "source": "plex", "match_trace": {}}
        result = MagicMock()
        result.status = "success"
        result.message = "ok"
        with (
            patch(
                "app.services.sync_service.database_manager.get_sync_record_by_id",
                return_value=record,
            ),
            patch.object(self.svc, "_build_retry_item_from_record"),
            patch.object(self.svc, "sync_custom_item", return_value=result),
            patch(
                "app.services.sync_service.database_manager.update_sync_record_status"
            ),
            patch(
                "app.services.sync_service.database_manager.mark_pending_sync_synced_by_sync_record_id",
                return_value=2,
            ) as mock_mark,
        ):
            msg = self.svc._auto_replay_after_confirm(20, {}, "测试")

        assert "补发成功" in msg
        mock_mark.assert_called_once_with(20)

    def test_replay_error_keeps_original_status(self):
        """补发失败（error）时保持原状态不变"""
        record = {"id": 30, "status": "error", "source": "plex", "match_trace": {}}
        result = MagicMock()
        result.status = "error"
        result.message = "API 不可达"
        with (
            patch(
                "app.services.sync_service.database_manager.get_sync_record_by_id",
                return_value=record,
            ),
            patch.object(self.svc, "_build_retry_item_from_record"),
            patch.object(self.svc, "sync_custom_item", return_value=result),
            patch(
                "app.services.sync_service.database_manager.update_sync_record_status"
            ) as mock_update,
            patch.object(self.svc, "_cleanup_pending_for_replay") as mock_cleanup,
        ):
            msg = self.svc._auto_replay_after_confirm(30, {}, "测试")

        assert "补发失败" in msg
        assert "API 不可达" in msg
        mock_update.assert_not_called()
        mock_cleanup.assert_not_called()

    def test_replay_exception_returns_error_message(self):
        """补发过程抛异常时返回错误描述，不抛出"""
        record = {"id": 40, "status": "error", "source": "plex", "match_trace": {}}
        with (
            patch(
                "app.services.sync_service.database_manager.get_sync_record_by_id",
                return_value=record,
            ),
            patch.object(self.svc, "_build_retry_item_from_record"),
            patch.object(
                self.svc,
                "sync_custom_item",
                side_effect=RuntimeError("boom"),
            ),
        ):
            msg = self.svc._auto_replay_after_confirm(40, {}, "测试")

        assert "补发异常" in msg
        assert "boom" in msg

    def test_cleanup_pending_skips_non_queued(self):
        """_cleanup_pending_for_replay 对非 queued 状态跳过"""
        with patch(
            "app.services.sync_service.database_manager.mark_pending_sync_synced_by_sync_record_id"
        ) as mock_mark:
            self.svc._cleanup_pending_for_replay(1, "error")
        mock_mark.assert_not_called()
