"""sync_service：候选确认 subject_id 有效性校验测试

验证：
1. 非数字 subject_id 直接拒绝
2. archive 命中且类型为动画/三次元时放行
3. archive 命中但类型为书籍/音乐时拒绝
4. archive 未命中时降级到 API 校验
5. API 返回条目不存在时拒绝
6. API 异常时降级放行（不阻塞用户）
7. 无可用 Bangumi 配置时降级放行
8. confirm_pending_candidate 校验失败时不写入映射
"""

from unittest.mock import patch

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
                "app.services.sync_service.config_manager.get_active_bangumi_config",
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
                "app.services.sync_service.config_manager.get_active_bangumi_config",
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
                "app.services.sync_service.config_manager.get_active_bangumi_config",
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
                "app.services.sync_service.config_manager.get_active_bangumi_config",
                return_value=None,
            ),
            patch(
                "app.services.sync_service.config_manager.get_bangumi_configs",
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
        ):
            ok, msg = self.svc.confirm_pending_candidate(2, "123")

        assert ok is True
        mock_upsert.assert_called_once_with("测试番", "123", 1)
        mock_update.assert_called_once()
