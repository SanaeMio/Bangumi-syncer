"""BangumiReplayScheduler 单元测试

此前 _probe_api / _run_sync_job / _is_enabled / _get_driver_config / get_status
仅在 tests/api/test_bangumi_replay.py 中以 MagicMock 全量替换，从未真正执行。
本文件直接实例化 BangumiReplayScheduler 并对真实方法做分支覆盖，重点保护
_probe_api 中 "single 模式读 [bangumi] 段、multi 模式取首个 mapping" 的分支
（曾出过线上 bug）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.bangumi_replay_scheduler import BangumiReplayScheduler


def _make_get_side_effect(values: dict[tuple[str, str], object]):
    """构造 config_manager.get 的 side_effect：按 (section, key) 查表，未命中返回 fallback。"""

    def _get(section, key, fallback=None):
        return values.get((section, key), fallback)

    return _get


# ----------------------------------------------------------------------
# 1. _is_enabled
# ----------------------------------------------------------------------


class TestIsEnabled:
    """[bangumi-replay] enabled 的判定（默认 True，与 archive 解耦）"""

    def test_replay_enabled_true_returns_true(self):
        s = BangumiReplayScheduler()
        with patch("app.services.bangumi_replay_scheduler.config_manager") as cm:
            cm.get.side_effect = _make_get_side_effect(
                {
                    ("bangumi-replay", "enabled"): True,
                }
            )
            assert s._is_enabled() is True

    def test_replay_enabled_false_returns_false(self):
        s = BangumiReplayScheduler()
        with patch("app.services.bangumi_replay_scheduler.config_manager") as cm:
            cm.get.side_effect = _make_get_side_effect(
                {
                    ("bangumi-replay", "enabled"): False,
                }
            )
            assert s._is_enabled() is False

    def test_replay_unconfigured_fallback_true(self):
        """未配置 enabled 时 fallback True"""
        s = BangumiReplayScheduler()
        with patch("app.services.bangumi_replay_scheduler.config_manager") as cm:
            cm.get.side_effect = _make_get_side_effect({})
            assert s._is_enabled() is True

    def test_replay_independent_of_archive(self):
        """replay 启用与 archive 无关：archive 关闭但 replay 启用时仍应返回 True"""
        s = BangumiReplayScheduler()
        with patch("app.services.bangumi_replay_scheduler.config_manager") as cm:
            cm.get.side_effect = _make_get_side_effect(
                {
                    ("bangumi-archive", "enabled"): False,
                    ("bangumi-replay", "enabled"): True,
                }
            )
            assert s._is_enabled() is True


# ----------------------------------------------------------------------
# 2. _get_driver_config
# ----------------------------------------------------------------------


class TestGetDriverConfig:
    """replay_cron 默认值与自定义值"""

    def test_default_cron_when_replay_cron_unconfigured(self):
        s = BangumiReplayScheduler()
        with patch("app.services.bangumi_replay_scheduler.config_manager") as cm:
            cm.get.side_effect = _make_get_side_effect({})
            cfg = s._get_driver_config()
        assert cfg["sync_interval"] == "*/10 * * * *"

    def test_custom_cron_when_replay_cron_configured(self):
        s = BangumiReplayScheduler()
        with patch("app.services.bangumi_replay_scheduler.config_manager") as cm:
            cm.get.side_effect = _make_get_side_effect(
                {
                    ("bangumi-replay", "replay_cron"): "0 * * * *",
                }
            )
            cfg = s._get_driver_config()
        assert cfg["sync_interval"] == "0 * * * *"


# ----------------------------------------------------------------------
# 3. get_status
# ----------------------------------------------------------------------


class TestGetStatus:
    """get_status 返回 enabled/cron/running/queue_stats"""

    def test_returns_status_dict_when_enabled(self):
        s = BangumiReplayScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        s.scheduler = mock_sched
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch("app.core.database.database_manager") as db,
        ):
            cm.get.side_effect = _make_get_side_effect(
                {
                    ("bangumi-replay", "enabled"): True,
                    ("bangumi-replay", "replay_cron"): "0 * * * *",
                }
            )
            db.get_pending_sync_stats.return_value = {
                "pending": 5,
                "synced": 10,
                "abandoned": 2,
            }
            status = s.get_status()

        assert status["enabled"] is True
        assert status["cron"] == "0 * * * *"
        assert status["running"] is True
        assert status["queue_stats"] == {
            "pending": 5,
            "synced": 10,
            "abandoned": 2,
        }

    def test_running_false_when_scheduler_none(self):
        s = BangumiReplayScheduler()
        s.scheduler = None
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch("app.core.database.database_manager") as db,
        ):
            cm.get.side_effect = _make_get_side_effect(
                {
                    ("bangumi-replay", "enabled"): True,
                }
            )
            db.get_pending_sync_stats.return_value = {
                "pending": 0,
                "synced": 0,
                "abandoned": 0,
            }
            status = s.get_status()

        assert status["running"] is False
        assert status["queue_stats"]["pending"] == 0

    def test_running_false_when_scheduler_not_running(self):
        s = BangumiReplayScheduler()
        mock_sched = MagicMock()
        mock_sched.running = False
        s.scheduler = mock_sched
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch("app.core.database.database_manager") as db,
        ):
            cm.get.side_effect = _make_get_side_effect(
                {
                    ("bangumi-replay", "enabled"): False,
                }
            )
            db.get_pending_sync_stats.return_value = {
                "pending": 1,
                "synced": 0,
                "abandoned": 0,
            }
            status = s.get_status()

        assert status["running"] is False
        assert status["enabled"] is False
        assert status["queue_stats"]["pending"] == 1
        # 默认 cron 兜底
        assert status["cron"] == "*/10 * * * *"


# ----------------------------------------------------------------------
# 4. _probe_api（关键，覆盖曾出 bug 的分支）
# ----------------------------------------------------------------------


def _bangumi_account_config(**overrides) -> dict:
    """get_active_bangumi_config() 的有效返回值"""
    cfg = {"username": "testuser", "access_token": "testtoken", "private": False}
    cfg.update(overrides)
    return cfg


def _dev_http_snapshot(**overrides) -> dict:
    """get_dev_http_snapshot() 的返回值"""
    snap = {
        "script_proxy": "",
        "ssl_verify": True,
        "bgm_api_proxy": "",
        "bgm_next_proxy": "",
    }
    snap.update(overrides)
    return snap


class TestProbeApiSingleMode:
    """single 模式：从 [bangumi] 段直接读取账号配置"""

    @pytest.mark.asyncio
    async def test_get_200_returns_true(self):
        s = BangumiReplayScheduler()
        mock_instance = MagicMock()
        mock_instance.get.return_value = MagicMock(status_code=200)
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            cm.get_dev_http_snapshot.return_value = _dev_http_snapshot()
            mock_cls.return_value = mock_instance
            result = await s._probe_api()

        assert result is True
        mock_cls.assert_called_once_with(
            username="testuser",
            access_token="testtoken",
            private=False,
            http_proxy="",
            ssl_verify=True,
            bgm_api_proxy="",
            bgm_next_proxy="",
        )
        mock_instance.mark_api_reachable.assert_called_once()
        mock_instance.get.assert_called_once_with("subjects/1")
        mock_instance.req.close.assert_called_once()
        mock_instance._req_not_auth.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_404_returns_true(self):
        """404 也算可达（说明 API 通了，只是 subject/1 不存在）"""
        s = BangumiReplayScheduler()
        mock_instance = MagicMock()
        mock_instance.get.return_value = MagicMock(status_code=404)
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            cm.get_dev_http_snapshot.return_value = _dev_http_snapshot()
            mock_cls.return_value = mock_instance
            result = await s._probe_api()

        assert result is True

    @pytest.mark.asyncio
    async def test_get_500_returns_false(self):
        """5xx 视为不可达"""
        s = BangumiReplayScheduler()
        mock_instance = MagicMock()
        mock_instance.get.return_value = MagicMock(status_code=500)
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            cm.get_dev_http_snapshot.return_value = _dev_http_snapshot()
            mock_cls.return_value = mock_instance
            result = await s._probe_api()

        assert result is False

    @pytest.mark.asyncio
    async def test_get_raises_returns_false(self):
        """GET 抛异常 → 外层 except 捕获后返回 False"""
        s = BangumiReplayScheduler()
        mock_instance = MagicMock()
        mock_instance.get.side_effect = ValueError("network error")
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            cm.get_dev_http_snapshot.return_value = _dev_http_snapshot()
            mock_cls.return_value = mock_instance
            result = await s._probe_api()

        assert result is False
        # finally 仍应关闭 req / _req_not_auth
        mock_instance.req.close.assert_called_once()
        mock_instance._req_not_auth.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_username_returns_false_without_instantiating_api(self):
        """single 模式 + username 为空 → False，且不实例化 BangumiApi"""
        s = BangumiReplayScheduler()
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager"),
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(username=""),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            result = await s._probe_api()

        assert result is False
        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_access_token_returns_false_without_instantiating_api(self):
        """single 模式 + access_token 为空 → False，且不实例化 BangumiApi"""
        s = BangumiReplayScheduler()
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager"),
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(access_token=""),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            result = await s._probe_api()

        assert result is False
        mock_cls.assert_not_called()


class TestProbeApiMultiMode:
    """multi 模式：取首个 user_mapping 对应的配置"""

    @pytest.mark.asyncio
    async def test_multi_mode_with_user_mapping_returns_true(self):
        s = BangumiReplayScheduler()
        mock_instance = MagicMock()
        mock_instance.get.return_value = MagicMock(status_code=200)
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(username="u1", access_token="t1"),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            cm.get_dev_http_snapshot.return_value = _dev_http_snapshot()
            mock_cls.return_value = mock_instance
            result = await s._probe_api()

        assert result is True
        # 验证取的是首个映射对应的账号配置
        mock_cls.assert_called_once_with(
            username="u1",
            access_token="t1",
            private=False,
            http_proxy="",
            ssl_verify=True,
            bgm_api_proxy="",
            bgm_next_proxy="",
        )

    @pytest.mark.asyncio
    async def test_multi_mode_no_user_mappings_returns_false(self):
        """multi 模式 + 无用户映射 → cfg 为空 → False"""
        s = BangumiReplayScheduler()
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager"),
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=None,
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            result = await s._probe_api()

        assert result is False
        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_mode_mapping_points_to_missing_config_returns_false(self):
        """multi 模式 + 映射指向不存在的配置段 → cfg 为空 → False"""
        s = BangumiReplayScheduler()
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager"),
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=None,
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
        ):
            result = await s._probe_api()

        assert result is False
        mock_cls.assert_not_called()


# ----------------------------------------------------------------------
# 5. _run_sync_job
# ----------------------------------------------------------------------


class TestRunSyncJob:
    """_run_sync_job 流程：启用检查 → 队列计数 → 探测 → 补发"""

    @pytest.mark.asyncio
    async def test_disabled_returns_without_probe(self):
        s = BangumiReplayScheduler()
        with (
            patch.object(s, "_is_enabled", return_value=False),
            patch.object(s, "_probe_api", new_callable=AsyncMock) as probe,
        ):
            await s._run_sync_job()
        probe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_probe_false_returns_without_replay(self):
        s = BangumiReplayScheduler()
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch.object(
                s, "_probe_api", new_callable=AsyncMock, return_value=False
            ) as probe,
            patch("app.services.sync_service.sync_service") as mock_svc,
            patch(
                "app.core.database.database_manager.count_pending_sync", return_value=5
            ),
        ):
            await s._run_sync_job()
        probe.assert_awaited_once()
        mock_svc.replay_pending_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_queue_skips_probe_and_replay(self):
        """队列为空时应直接跳过，不探测、不补发"""
        s = BangumiReplayScheduler()
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch.object(s, "_probe_api", new_callable=AsyncMock) as probe,
            patch("app.services.sync_service.sync_service") as mock_svc,
            patch(
                "app.core.database.database_manager.count_pending_sync", return_value=0
            ),
        ):
            await s._run_sync_job()
        probe.assert_not_awaited()
        mock_svc.replay_pending_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_probe_true_replay_success(self):
        s = BangumiReplayScheduler()
        s._scheduler_config = {"job_timeout": 300}
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch.object(s, "_probe_api", new_callable=AsyncMock, return_value=True),
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch("app.services.sync_service.sync_service") as mock_svc,
            patch(
                "app.core.database.database_manager.count_pending_sync", return_value=5
            ),
        ):
            cm.get.side_effect = _make_get_side_effect(
                {("bangumi-replay", "replay_batch_size"): 20}
            )
            mock_svc.replay_pending_batch.return_value = {
                "total": 3,
                "success": 2,
                "failed": 1,
                "still_unreachable": 0,
            }
            await s._run_sync_job()

        mock_svc.replay_pending_batch.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_probe_true_replay_zero_total_does_not_raise(self):
        """补发 0 条时也应正常返回（不触发日志 info 分支）"""
        s = BangumiReplayScheduler()
        s._scheduler_config = {"job_timeout": 300}
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch.object(s, "_probe_api", new_callable=AsyncMock, return_value=True),
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch("app.services.sync_service.sync_service") as mock_svc,
            patch(
                "app.core.database.database_manager.count_pending_sync", return_value=5
            ),
        ):
            cm.get.side_effect = _make_get_side_effect(
                {("bangumi-replay", "replay_batch_size"): 20}
            )
            mock_svc.replay_pending_batch.return_value = {
                "total": 0,
                "success": 0,
                "failed": 0,
                "still_unreachable": 0,
            }
            await s._run_sync_job()

        mock_svc.replay_pending_batch.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_probe_true_replay_timeout_logs_error(self):
        """补发超时不应抛出，由 _run_sync_job 内部捕获并记录错误"""
        s = BangumiReplayScheduler()
        s._scheduler_config = {"job_timeout": 300}
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch.object(s, "_probe_api", new_callable=AsyncMock, return_value=True),
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch("app.services.sync_service.sync_service") as mock_svc,
            patch(
                "app.services.bangumi_replay_scheduler.asyncio.wait_for",
                new_callable=AsyncMock,
                side_effect=asyncio.TimeoutError,
            ),
            patch(
                "app.core.database.database_manager.count_pending_sync", return_value=5
            ),
        ):
            cm.get.side_effect = _make_get_side_effect(
                {("bangumi-replay", "replay_batch_size"): 20}
            )
            mock_svc.replay_pending_batch.return_value = {
                "total": 0,
                "success": 0,
                "failed": 0,
                "still_unreachable": 0,
            }
            # 不应抛出
            await s._run_sync_job()

    @pytest.mark.asyncio
    async def test_probe_true_replay_raises_logs_error(self):
        """replay_pending_batch 抛异常时不应向外传播"""
        s = BangumiReplayScheduler()
        s._scheduler_config = {"job_timeout": 300}
        with (
            patch.object(s, "_is_enabled", return_value=True),
            patch.object(s, "_probe_api", new_callable=AsyncMock, return_value=True),
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch("app.services.sync_service.sync_service") as mock_svc,
            patch(
                "app.core.database.database_manager.count_pending_sync", return_value=5
            ),
        ):
            cm.get.side_effect = _make_get_side_effect(
                {("bangumi-replay", "replay_batch_size"): 20}
            )
            mock_svc.replay_pending_batch.side_effect = RuntimeError("db error")
            # 不应抛出
            await s._run_sync_job()

        mock_svc.replay_pending_batch.assert_called_once_with(20)


# ----------------------------------------------------------------------
# 6. trigger_immediate_run
# ----------------------------------------------------------------------


class TestTriggerImmediateRun:
    """入队后立即触发补发：防抖 + 调度器状态判断"""

    def test_disabled_does_nothing(self):
        s = BangumiReplayScheduler()
        with patch.object(s, "_is_enabled", return_value=False):
            s.trigger_immediate_run()
        # scheduler 未创建，不应抛出

    def test_scheduler_not_running_does_nothing(self):
        s = BangumiReplayScheduler()
        fake_scheduler = MagicMock()
        fake_scheduler.running = False
        s.scheduler = fake_scheduler
        with patch.object(s, "_is_enabled", return_value=True):
            s.trigger_immediate_run()
        fake_scheduler.add_job.assert_not_called()

    def test_running_scheduler_adds_immediate_job(self):
        s = BangumiReplayScheduler()
        fake_scheduler = MagicMock()
        fake_scheduler.running = True
        s.scheduler = fake_scheduler
        with patch.object(s, "_is_enabled", return_value=True):
            s.trigger_immediate_run()
        fake_scheduler.add_job.assert_called_once()
        kwargs = fake_scheduler.add_job.call_args.kwargs
        assert kwargs["trigger"] == "date"
        assert kwargs["id"] == "bangumi_replay_pending_immediate"
        assert kwargs["replace_existing"] is True

    def test_debounce_within_500ms_skips_second_call(self):
        s = BangumiReplayScheduler()
        fake_scheduler = MagicMock()
        fake_scheduler.running = True
        s.scheduler = fake_scheduler
        with patch.object(s, "_is_enabled", return_value=True):
            s.trigger_immediate_run()
            # 500ms 内再次触发，应被防抖丢弃
            s.trigger_immediate_run()
        assert fake_scheduler.add_job.call_count == 1


# ----------------------------------------------------------------------
# 7. 探测成功后统一复位 sync_service 缓存实例的不可达标记
# ----------------------------------------------------------------------


class TestProbeApiResetsUnreachableFlags:
    """_probe_api 探测成功时调用 reset_all_api_unreachable_flags"""

    @pytest.mark.asyncio
    async def test_reset_called_when_probe_succeeds(self):
        """探测 200 → 复位缓存实例的不可达标记"""
        s = BangumiReplayScheduler()
        mock_instance = MagicMock()
        mock_instance.get.return_value = MagicMock(status_code=200)
        mock_sync_service = MagicMock()
        mock_sync_service.reset_all_api_unreachable_flags.return_value = 2
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
            patch("app.services.sync_service.sync_service", mock_sync_service),
        ):
            cm.get_dev_http_snapshot.return_value = _dev_http_snapshot()
            mock_cls.return_value = mock_instance
            result = await s._probe_api()

        assert result is True
        mock_sync_service.reset_all_api_unreachable_flags.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_not_called_when_probe_fails(self):
        """探测失败（5xx）→ 维持不可达状态，不调用复位"""
        s = BangumiReplayScheduler()
        mock_instance = MagicMock()
        mock_instance.get.return_value = MagicMock(status_code=500)
        mock_sync_service = MagicMock()
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
            patch("app.services.sync_service.sync_service", mock_sync_service),
        ):
            cm.get_dev_http_snapshot.return_value = _dev_http_snapshot()
            mock_cls.return_value = mock_instance
            result = await s._probe_api()

        assert result is False
        mock_sync_service.reset_all_api_unreachable_flags.assert_not_called()

    @pytest.mark.asyncio
    async def test_reset_failure_does_not_affect_probe_result(self):
        """复位过程抛异常时降级为 debug 日志，探测结果仍为 True"""
        s = BangumiReplayScheduler()
        mock_instance = MagicMock()
        mock_instance.get.return_value = MagicMock(status_code=200)
        mock_sync_service = MagicMock()
        mock_sync_service.reset_all_api_unreachable_flags.side_effect = RuntimeError(
            "boom"
        )
        with (
            patch("app.services.bangumi_replay_scheduler.config_manager") as cm,
            patch(
                "app.core.accounts.get_active_bangumi_config",
                return_value=_bangumi_account_config(),
            ),
            patch("app.utils.bangumi_api.BangumiApi") as mock_cls,
            patch("app.services.sync_service.sync_service", mock_sync_service),
        ):
            cm.get_dev_http_snapshot.return_value = _dev_http_snapshot()
            mock_cls.return_value = mock_instance
            result = await s._probe_api()

        assert result is True
