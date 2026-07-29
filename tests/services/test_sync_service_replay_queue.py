"""sync_service：API 不可达时的降级入队测试

验证：
1. mark_episode_watched 抛 _PendingSyncQueued 时，retry 层捕获并返回 MARK_QUEUED(-1)
2. 配置 enabled=false 时，_PendingSyncQueued 不被吞掉，正常向上抛
3. collection.mark_episode_watched 在 _api_unreachable=True 时直接抛 _PendingSyncQueued
4. BangumiApi.is_api_unreachable 的 TTL 过期逻辑
"""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.sync_service import SyncService
from app.services.sync_service.retry import MARK_QUEUED
from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api.collection import _PendingSyncQueued


class TestRetryMarkEpisodeQueueing:
    """_retry_mark_episode 在 API 不可达时入队"""

    def test_returns_mark_queued_when_pending_sync_raised(self):
        """enabled=true + 抛 _PendingSyncQueued → 返回 MARK_QUEUED"""
        svc = SyncService()
        bgm = MagicMock()
        bgm.username = "bangumi_account_a"  # Bangumi 账号名，不应被入队使用
        bgm.mark_episode_watched.side_effect = _PendingSyncQueued(
            subject_id=123, ep_id=456, reason="api_unreachable"
        )

        with (
            patch(
                "app.services.sync_service.retry.is_replay_enabled", return_value=True
            ),
            patch.object(svc, "_enqueue_pending_sync") as mock_enqueue,
        ):
            status = svc._retry_mark_episode(
                bgm,
                "123",
                "456",
                queue_payload={
                    "title": "测试",
                    "season": 1,
                    "episode": 1,
                    "user_name": "plex_user_b",  # 媒体库用户名
                },
            )

        assert status == MARK_QUEUED
        mock_enqueue.assert_called_once()
        # 验证传入 enqueue 的参数
        call_kwargs = mock_enqueue.call_args
        assert call_kwargs.kwargs["subject_id"] == 123
        assert call_kwargs.kwargs["ep_id"] == 456
        assert call_kwargs.kwargs["reason"] == "api_unreachable"
        # payload 必须原样透传（user_name 由 _enqueue_pending_sync 内部从 payload 读取，
        # 不再依赖 bgm_api.username；具体校验见 TestEnqueuePendingSyncUserName）
        assert call_kwargs.kwargs["payload"]["user_name"] == "plex_user_b"

    def test_raises_when_replay_disabled(self):
        """enabled=false + 抛 _PendingSyncQueued → 正常向上抛"""
        svc = SyncService()
        bgm = MagicMock()
        bgm.mark_episode_watched.side_effect = _PendingSyncQueued(
            subject_id=123, ep_id=456
        )

        with patch(
            "app.services.sync_service.retry.is_replay_enabled", return_value=False
        ):
            with pytest.raises(_PendingSyncQueued):
                svc._retry_mark_episode(
                    bgm, "123", "456", queue_payload={"title": "测试"}
                )

    def test_raises_when_no_payload(self):
        """未传 queue_payload 时即使 enabled=true 也向上抛"""
        svc = SyncService()
        bgm = MagicMock()
        bgm.mark_episode_watched.side_effect = _PendingSyncQueued(
            subject_id=123, ep_id=456
        )

        with patch(
            "app.services.sync_service.retry.is_replay_enabled", return_value=True
        ):
            with pytest.raises(_PendingSyncQueued):
                svc._retry_mark_episode(bgm, "123", "456")


class TestMarkEpisodeWatchedDegradation:
    """collection.mark_episode_watched 的降级行为"""

    def test_raises_pending_sync_queued_when_api_unreachable(self):
        """_api_unreachable=True 时直接抛 _PendingSyncQueued，不发请求"""
        api = BangumiApi.__new__(BangumiApi)
        # 跳过 __init__，手动设置必要属性
        api._api_unreachable = True
        api._api_unreachable_until = time.time() + 300

        with patch.object(api, "is_api_unreachable", return_value=True):
            with pytest.raises(_PendingSyncQueued) as exc_info:
                api.mark_episode_watched(subject_id=123, ep_id=456)

        assert exc_info.value.subject_id == 123
        assert exc_info.value.ep_id == 456
        assert exc_info.value.reason == "api_unreachable"

    def test_proceeds_when_api_reachable(self):
        """_api_unreachable=False 时正常调用 _do_mark_episode_watched"""
        api = BangumiApi.__new__(BangumiApi)
        api._api_unreachable = False

        with (
            patch.object(api, "is_api_unreachable", return_value=False),
            patch.object(api, "_do_mark_episode_watched", return_value=1) as mock_do,
        ):
            result = api.mark_episode_watched(subject_id=123, ep_id=456)

        assert result == 1
        mock_do.assert_called_once_with(123, 456)


class TestApiUnreachableTtl:
    """BangumiApi 不可达标记的 TTL 逻辑"""

    def test_returns_false_when_never_marked(self):
        api = BangumiApi.__new__(BangumiApi)
        api._api_unreachable = False
        api._api_unreachable_until = 0.0
        assert api.is_api_unreachable() is False

    def test_returns_true_within_ttl(self):
        api = BangumiApi.__new__(BangumiApi)
        api._api_unreachable = True
        api._api_unreachable_until = time.time() + 300
        assert api.is_api_unreachable() is True

    def test_returns_false_after_ttl_expiry(self):
        api = BangumiApi.__new__(BangumiApi)
        api._api_unreachable = True
        api._api_unreachable_until = time.time() - 1  # 已过期
        # 调用一次后应清除标记
        assert api.is_api_unreachable() is False
        assert api._api_unreachable is False

    def test_mark_unreachable_sets_ttl(self):
        api = BangumiApi.__new__(BangumiApi)
        api._api_unreachable = False
        api._api_unreachable_until = 0.0

        with patch("app.utils.bangumi_api.config_manager.get", return_value=300):
            api.mark_api_unreachable()

        assert api._api_unreachable is True
        assert api._api_unreachable_until > time.time()

    def test_mark_reachable_clears_flag(self):
        api = BangumiApi.__new__(BangumiApi)
        api._api_unreachable = True
        api._api_unreachable_until = time.time() + 300

        api.mark_api_reachable()

        assert api._api_unreachable is False
        assert api._api_unreachable_until == 0.0


class TestPendingSyncQueuedException:
    """_PendingSyncQueued 异常类的属性与字符串表示"""

    def test_attributes_preserved(self):
        cause = httpx.ConnectError("connection refused")
        exc = _PendingSyncQueued(
            subject_id=123, ep_id=456, reason="connect_error", cause=cause
        )
        assert exc.subject_id == 123
        assert exc.ep_id == 456
        assert exc.reason == "connect_error"
        assert exc.cause is cause

    def test_default_reason(self):
        exc = _PendingSyncQueued(subject_id=1, ep_id=2)
        assert exc.reason == "api_unreachable"
        assert exc.cause is None

    def test_str_contains_subject_and_ep(self):
        exc = _PendingSyncQueued(subject_id=42, ep_id=99, reason="http_503")
        s = str(exc)
        assert "42" in s
        assert "99" in s
        assert "http_503" in s


class TestEnqueuePendingSyncUserName:
    """_enqueue_pending_sync 必须用 payload 里的媒体库用户名，而不是 bgm_api.username"""

    def test_uses_payload_user_name_not_bangumi_username(self):
        from app.services.sync_service import SyncService

        bgm = MagicMock()
        bgm.username = "bangumi_account_a"  # Bangumi 账号名

        payload = {
            "title": "测试番剧",
            "season": 2,
            "episode": 5,
            "user_name": "plex_user_b",  # 媒体库用户名
            "source": "plex",
            "media_type": "episode",
        }

        with (
            patch("app.core.database.database_manager") as mock_db,
            patch(
                "app.services.bangumi_replay_scheduler.bangumi_replay_scheduler"
            ) as mock_sched,
        ):
            SyncService._enqueue_pending_sync(
                bgm_api=bgm,
                subject_id=123,
                ep_id=456,
                reason="api_unreachable",
                last_error="timeout",
                payload=payload,
            )

        mock_db.enqueue_pending_sync.assert_called_once()
        call_kwargs = mock_db.enqueue_pending_sync.call_args.kwargs
        assert call_kwargs["user_name"] == "plex_user_b"
        assert call_kwargs["title"] == "测试番剧"
        assert call_kwargs["season"] == 2
        assert call_kwargs["episode"] == 5
        assert call_kwargs["source"] == "plex"
        assert call_kwargs["subject_id"] == "123"
        assert call_kwargs["episode_id"] == "456"
        # 入队后应立即触发补发调度器
        mock_sched.trigger_immediate_run.assert_called_once()

    def test_falls_back_to_empty_string_when_payload_missing_user_name(self):
        from app.services.sync_service import SyncService

        bgm = MagicMock()
        bgm.username = "bangumi_account_a"

        payload = {"title": "测试", "season": 1, "episode": 1}

        with (
            patch("app.core.database.database_manager") as mock_db,
            patch("app.services.bangumi_replay_scheduler.bangumi_replay_scheduler"),
        ):
            SyncService._enqueue_pending_sync(
                bgm_api=bgm,
                subject_id=1,
                ep_id=2,
                reason="api_unreachable",
                last_error="",
                payload=payload,
            )

        call_kwargs = mock_db.enqueue_pending_sync.call_args.kwargs
        # 缺失时回退为空串，而不是 bgm_api.username
        assert call_kwargs["user_name"] == ""

    def test_trigger_failure_does_not_raise(self):
        """trigger_immediate_run 抛异常时不应影响入队流程"""
        from app.services.sync_service import SyncService

        bgm = MagicMock()
        bgm.username = "bangumi_account_a"
        payload = {"title": "x", "season": 1, "episode": 1, "user_name": "u"}

        with (
            patch("app.core.database.database_manager") as mock_db,
            patch(
                "app.services.bangumi_replay_scheduler.bangumi_replay_scheduler"
            ) as mock_sched,
        ):
            mock_sched.trigger_immediate_run.side_effect = RuntimeError("boom")
            # 不应抛出
            SyncService._enqueue_pending_sync(
                bgm_api=bgm,
                subject_id=1,
                ep_id=None,
                reason="api_unreachable",
                last_error="",
                payload=payload,
            )

        mock_db.enqueue_pending_sync.assert_called_once()


class TestReplayPendingItemSyncRecordId:
    """replay_pending_item 返回 sync_record_id 字段"""

    def test_returns_sync_record_id_from_record(self):
        """补发成功时返回值带 sync_record_id（来自 pending_sync_queue 行）"""
        from app.services.sync_service import SyncService

        svc = SyncService()
        record = {
            "id": 1,
            "user_name": "user1",
            "subject_id": "123",
            "episode_id": "456",
            "payload_json": '{"title": "测试", "season": 1, "episode": 1, '
            '"release_date": "2024-01-01", "user_name": "user1", '
            '"source": "plex", "media_type": "episode"}',
            "source": "plex",
            "sync_record_id": 88,
        }

        with (
            patch.object(svc, "_get_bangumi_api_for_user") as mock_get_bgm,
            patch.object(svc, "_retry_mark_episode", return_value=1),
        ):
            mock_bgm = MagicMock()
            mock_bgm.mark_api_reachable = MagicMock()
            mock_get_bgm.return_value = mock_bgm

            result = svc.replay_pending_item(record)

        assert result["success"] is True
        assert result["sync_record_id"] == 88

    def test_returns_none_sync_record_id_when_missing(self):
        """旧数据无 sync_record_id 字段时返回 None"""
        from app.services.sync_service import SyncService

        svc = SyncService()
        record = {
            "id": 1,
            "user_name": "user1",
            "subject_id": "123",
            "episode_id": "456",
            "payload_json": '{"title": "测试", "season": 1, "episode": 1, '
            '"release_date": "2024-01-01", "user_name": "user1", '
            '"source": "plex", "media_type": "episode"}',
            "source": "plex",
            "sync_record_id": None,  # 旧数据
        }

        with (
            patch.object(svc, "_get_bangumi_api_for_user") as mock_get_bgm,
            patch.object(svc, "_retry_mark_episode", return_value=1),
        ):
            mock_bgm = MagicMock()
            mock_bgm.mark_api_reachable = MagicMock()
            mock_get_bgm.return_value = mock_bgm

            result = svc.replay_pending_item(record)

        assert result["success"] is True
        assert result["sync_record_id"] is None


class TestReplayPendingBatchWriteback:
    """replay_pending_batch 在补发成功/放弃时回写 sync_records 状态"""

    def test_writeback_success_on_replay_success(self):
        """补发成功时回写 sync_records: queued → retried（与手动重试/候选确认补发统一）"""
        from app.services.sync_service import SyncService

        svc = SyncService()
        records = [
            {
                "id": 1,
                "user_name": "user1",
                "subject_id": "123",
                "episode_id": "456",
                "payload_json": '{"title": "测试", "season": 1, "episode": 1, '
                '"user_name": "user1", "source": "plex", "media_type": "episode"}',
                "source": "plex",
                "attempts": 0,
                "sync_record_id": 100,
            }
        ]

        with (
            patch(
                "app.services.sync_service.config_manager.get",
                return_value=50,
            ),
            patch(
                "app.services.sync_service.database_manager.fetch_pending_sync",
                return_value=records,
            ),
            patch.object(svc, "replay_pending_item") as mock_item,
            patch(
                "app.services.sync_service.database_manager.mark_pending_sync_synced"
            ) as mock_mark,
            patch(
                "app.services.sync_service.database_manager.update_sync_record_status"
            ) as mock_update,
        ):
            mock_item.return_value = {
                "success": True,
                "message": "补发成功 mark_status=1",
                "should_mark_synced": True,
                "mark_status": 1,
                "sync_record_id": 100,
            }

            stats = svc.replay_pending_batch(limit=10)

        assert stats["success"] == 1
        mock_mark.assert_called_once_with(1)
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args.args[0] == 100  # sync_record_id
        assert call_args.args[1] == "retried"

    def test_writeback_error_on_abandoned(self):
        """补发超过 max_attempts 时回写 sync_records: queued → error"""
        from app.services.sync_service import SyncService

        svc = SyncService()
        records = [
            {
                "id": 1,
                "user_name": "user1",
                "subject_id": "123",
                "episode_id": "456",
                "payload_json": '{"title": "测试", "season": 1, "episode": 1, '
                '"user_name": "user1", "source": "plex", "media_type": "episode"}',
                "source": "plex",
                "attempts": 49,  # 已达上限-1
                "sync_record_id": 200,
            }
        ]

        # 模拟配置：max_attempts=50, threshold=3
        def config_get_side_effect(section, key, fallback=None):
            if key == "max_attempts":
                return 50
            if key == "replay_unreachable_threshold":
                return 3
            return fallback

        with (
            patch(
                "app.services.sync_service.config_manager.get",
                side_effect=config_get_side_effect,
            ),
            patch(
                "app.services.sync_service.database_manager.fetch_pending_sync",
                return_value=records,
            ),
            patch.object(svc, "replay_pending_item") as mock_item,
            patch(
                "app.services.sync_service.database_manager.increment_pending_sync_attempts"
            ),
            patch(
                "app.services.sync_service.database_manager.mark_pending_sync_abandoned"
            ) as mock_abandon,
            patch(
                "app.services.sync_service.database_manager.update_sync_record_status"
            ) as mock_update,
        ):
            mock_item.return_value = {
                "success": False,
                "message": "标记失败: 401 Unauthorized",
                "should_mark_synced": False,
                "sync_record_id": 200,
            }

            stats = svc.replay_pending_batch(limit=10)

        assert stats["failed"] == 1
        mock_abandon.assert_called_once()
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args.args[0] == 200  # sync_record_id
        assert call_args.args[1] == "error"

    def test_skip_writeback_when_sync_record_id_none(self):
        """旧数据 sync_record_id 为 None 时跳过回写"""
        from app.services.sync_service import SyncService

        svc = SyncService()
        records = [
            {
                "id": 1,
                "user_name": "user1",
                "subject_id": "123",
                "episode_id": "456",
                "payload_json": '{"title": "测试", "season": 1, "episode": 1, '
                '"user_name": "user1", "source": "plex", "media_type": "episode"}',
                "source": "plex",
                "attempts": 0,
                "sync_record_id": None,  # 旧数据
            }
        ]

        with (
            patch(
                "app.services.sync_service.config_manager.get",
                return_value=50,
            ),
            patch(
                "app.services.sync_service.database_manager.fetch_pending_sync",
                return_value=records,
            ),
            patch.object(svc, "replay_pending_item") as mock_item,
            patch(
                "app.services.sync_service.database_manager.mark_pending_sync_synced"
            ),
            patch(
                "app.services.sync_service.database_manager.update_sync_record_status"
            ) as mock_update,
        ):
            mock_item.return_value = {
                "success": True,
                "message": "补发成功 mark_status=1",
                "should_mark_synced": True,
                "mark_status": 1,
                "sync_record_id": None,
            }

            stats = svc.replay_pending_batch(limit=10)

        assert stats["success"] == 1
        # sync_record_id 为 None，不应调用 update_sync_record_status
        mock_update.assert_not_called()
