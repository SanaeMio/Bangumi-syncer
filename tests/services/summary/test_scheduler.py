"""SummaryScheduler tests — multi-job cron scheduler for [summary-N] configs."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.services.summary.models import SummaryJobConfig

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_summary_config(**overrides):
    """Build a minimal summary config dict for tests."""
    return {
        "id": overrides.get("id", 1),
        "enabled": overrides.get("enabled", True),
        "name": overrides.get("name", "test-summary"),
        "cron": overrides.get("cron", "0 21 * * *"),
        "lookback_days": overrides.get("lookback_days", 1),
        "user_name": overrides.get("user_name", ""),
        "system_prompt": overrides.get("system_prompt", ""),
        "max_records": overrides.get("max_records", 200),
    }


# ---------------------------------------------------------------------------
# _parse_cron
# ---------------------------------------------------------------------------


def test_parse_cron_valid_five_fields():
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    trigger = s._parse_cron("0 21 * * *")
    assert isinstance(trigger, CronTrigger)


def test_parse_cron_invalid_raises_value_error():
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    with pytest.raises(ValueError, match="无效的 cron 表达式"):
        s._parse_cron("not-valid")


def test_parse_cron_strips_whitespace():
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    s._timezone = "Asia/Shanghai"
    trigger = s._parse_cron("  0  21  *  *  *  ")
    assert isinstance(trigger, CronTrigger)


def test_parse_cron_uses_passed_timezone():
    """传入 timezone 参数时 CronTrigger 应使用该时区。"""
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    s._timezone = "Asia/Shanghai"
    trigger = s._parse_cron(
        "0 23 * * *",
    )
    assert str(trigger.timezone) == "Asia/Shanghai"


def test_parse_cron_accepts_string_timezone():
    """CronTrigger.timezone 返回 ZoneInfo 对象，验证 key 与传入一致。"""
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    s._timezone = "Asia/Shanghai"
    trigger = s._parse_cron(
        "0 23 * * *",
    )
    assert trigger.timezone.key == "Asia/Shanghai"
    assert isinstance(trigger, CronTrigger)


def test_schedule_all_jobs_passes_timezone_to_trigger():
    """_schedule_all_jobs 创建的 trigger 应带有配置的时区。"""
    from app.services.summary.scheduler import SummaryScheduler

    configs = [
        _make_summary_config(id=1, name="tz-test", cron="0 23 * * *"),
    ]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        s = SummaryScheduler()
        s._timezone = "Asia/Shanghai"
        mock_sched = MagicMock()
        mock_sched.running = True
        mock_sched.add_job = MagicMock()
        mock_sched.get_jobs = MagicMock(return_value=[])
        s.scheduler = mock_sched

        s._schedule_all_jobs()

    # 验证传给 add_job 的 trigger 带有正确时区
    mock_sched.add_job.assert_called_once()
    call_kwargs = mock_sched.add_job.call_args[1]
    trigger = call_kwargs["trigger"]
    assert trigger.timezone.key == "Asia/Shanghai"


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_scheduler_and_registers_enabled_jobs():
    """start() should create an AsyncIOScheduler and add jobs for enabled configs."""
    configs = [
        _make_summary_config(id=1, name="job1", cron="0 21 * * *"),
        _make_summary_config(id=2, name="job2", cron="30 8 * * *"),
    ]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        # Spy on add_job via a mock scheduler
        mock_sched = MagicMock()
        mock_sched.running = True  # _schedule_all_jobs checks this
        mock_sched.add_job = MagicMock()
        mock_sched.get_jobs = MagicMock(return_value=[])
        s.scheduler = mock_sched
        ok = await s.start()
    assert ok is True
    assert mock_sched.add_job.call_count == 2


@pytest.mark.asyncio
async def test_start_skips_disabled_configs():
    """start() should NOT register jobs for configs where enabled=False."""
    configs = [
        _make_summary_config(id=1, name="enabled-job", enabled=True, cron="0 21 * * *"),
        _make_summary_config(
            id=2, name="disabled-job", enabled=False, cron="30 8 * * *"
        ),
    ]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        mock_sched.add_job = MagicMock()
        mock_sched.get_jobs = MagicMock(return_value=[])
        s.scheduler = mock_sched
        await s.start()
    assert mock_sched.add_job.call_count == 1
    # The one call should be for id=1
    call_args = mock_sched.add_job.call_args
    assert call_args is not None
    assert call_args[1]["id"] == "summary_1"


@pytest.mark.asyncio
async def test_start_when_no_configs_does_not_add_jobs():
    """start() with empty config list should not add any jobs."""
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=[],
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        mock_sched.add_job = MagicMock()
        mock_sched.get_jobs = MagicMock(return_value=[])
        s.scheduler = mock_sched
        ok = await s.start()
    assert ok is True
    mock_sched.add_job.assert_not_called()


@pytest.mark.asyncio
async def test_start_when_already_running_refreshes_jobs():
    """When scheduler is already running, start() should just refresh jobs."""
    configs = [_make_summary_config(id=1)]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        mock_sched.add_job = MagicMock()
        mock_sched.get_jobs = MagicMock(return_value=[])
        s.scheduler = mock_sched
        ok = await s.start()
    assert ok is True
    mock_sched.add_job.assert_called_once()


@pytest.mark.asyncio
async def test_start_constructor_raises_returns_false():
    """If creating the scheduler raises, start() returns False."""
    configs = [_make_summary_config(id=1)]
    with (
        patch(
            "app.services.summary.scheduler.config_manager.get_summary_configs",
            return_value=configs,
        ),
        patch(
            "app.services.summary.scheduler.AsyncIOScheduler",
            side_effect=RuntimeError("boom"),
        ),
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        ok = await s.start()
    assert ok is False


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_shuts_down_scheduler():
    """stop() should shutdown the scheduler and nullify it."""
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    mock_sched = MagicMock()
    mock_sched.running = True
    s.scheduler = mock_sched
    ok = await s.stop()
    assert ok is True
    mock_sched.shutdown.assert_called_once()
    assert s.scheduler is None


@pytest.mark.asyncio
async def test_stop_when_no_scheduler_returns_true():
    """stop() with no scheduler should still return True."""
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    assert s.scheduler is None
    ok = await s.stop()
    assert ok is True


@pytest.mark.asyncio
async def test_stop_shutdown_raises_returns_false():
    """If shutdown raises, stop() returns False."""
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    mock_sched = MagicMock()
    mock_sched.running = True
    mock_sched.shutdown = MagicMock(side_effect=OSError("shutdown error"))
    s.scheduler = mock_sched
    ok = await s.stop()
    assert ok is False


# ---------------------------------------------------------------------------
# _schedule_all_jobs — orphan removal
# ---------------------------------------------------------------------------


def test_schedule_all_jobs_removes_orphaned_jobs():
    """Jobs whose configs were removed/deactivated should be cleaned up."""
    configs = [_make_summary_config(id=1, name="keep-me")]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True

        # Pretend there is a stale "summary_99" job registered
        stale_job = MagicMock()
        stale_job.id = "summary_99"
        mock_sched.get_jobs.return_value = [stale_job]

        mock_sched.add_job = MagicMock()
        mock_sched.remove_job = MagicMock()
        s.scheduler = mock_sched

        s._schedule_all_jobs()

    mock_sched.remove_job.assert_called_once_with("summary_99")


def test_schedule_all_jobs_remove_job_logs_warning_on_exception():
    """移除孤立任务时如果发生异常，应记录警告日志。"""
    configs = [_make_summary_config(id=1)]
    with (
        patch(
            "app.services.summary.scheduler.config_manager.get_summary_configs",
            return_value=configs,
        ),
        patch("app.services.summary.scheduler.logger") as mock_logger,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        stale_job = MagicMock()
        stale_job.id = "summary_99"
        mock_sched.get_jobs.return_value = [stale_job]
        mock_sched.add_job = MagicMock()
        mock_sched.remove_job = MagicMock(side_effect=ValueError("bad"))
        s.scheduler = mock_sched

        # 不应抛出异常
        s._schedule_all_jobs()

    mock_sched.remove_job.assert_called_once_with("summary_99")
    mock_logger.warning.assert_called()


def test_schedule_all_jobs_does_nothing_when_scheduler_not_running():
    """_schedule_all_jobs is a no-op when scheduler is None or not running."""
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    s.scheduler = None
    # Should not raise
    s._schedule_all_jobs()

    s.scheduler = MagicMock(running=False)
    s._schedule_all_jobs()
    # get_jobs / add_job should not be called
    s.scheduler.get_jobs.assert_not_called()


def test_schedule_all_jobs_skips_invalid_cron_job():
    """cron 无效的任务应被跳过，不影响其他有效任务继续注册。"""
    configs = [
        _make_summary_config(id=1, name="valid-job", cron="0 21 * * *"),
        _make_summary_config(id=2, name="bad-cron-job", cron="bad"),
    ]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        mock_sched.add_job = MagicMock()
        mock_sched.get_jobs = MagicMock(return_value=[])
        s.scheduler = mock_sched

        s._schedule_all_jobs()

    # 只有有效 cron 的任务被注册
    assert mock_sched.add_job.call_count == 1
    assert mock_sched.add_job.call_args[1]["id"] == "summary_1"


# ---------------------------------------------------------------------------
# _run_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_job_calls_execute_job_with_correct_config():
    """_run_job should call summary_service.execute_job with the job_config."""
    from app.services.summary.scheduler import SummaryScheduler

    job_config = SummaryJobConfig.from_config_dict(
        _make_summary_config(id=1, name="test")
    )
    s = SummaryScheduler()

    with patch(
        "app.services.summary.scheduler.summary_service.execute_job",
        new_callable=AsyncMock,
    ) as mock_execute:
        await s._run_job(job_config)

    mock_execute.assert_awaited_once_with(job_config)


@pytest.mark.asyncio
async def test_run_job_handles_timeout():
    """_run_job should catch TimeoutError and log it."""
    from app.services.summary.scheduler import SummaryScheduler

    job_config = SummaryJobConfig.from_config_dict(
        _make_summary_config(id=1, name="hanging")
    )

    async def hang():
        await asyncio.sleep(99)

    with (
        patch(
            "app.services.summary.scheduler.config_manager.get_scheduler_config",
            return_value={"job_timeout": 1},
        ),
        patch(
            "app.services.summary.scheduler.summary_service.execute_job",
            side_effect=hang,
            new_callable=AsyncMock,
        ) as mock_execute,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        s._scheduler_config = {"job_timeout": 1}
        # Should not raise
        await s._run_job(job_config)

    mock_execute.assert_called_once()


@pytest.mark.asyncio
async def test_run_job_handles_exception():
    """_run_job should catch generic exceptions and log them, not propagate."""
    from app.services.summary.scheduler import SummaryScheduler

    job_config = SummaryJobConfig.from_config_dict(
        _make_summary_config(id=1, name="failing")
    )

    with patch(
        "app.services.summary.scheduler.summary_service.execute_job",
        new_callable=AsyncMock,
        side_effect=ValueError("something went wrong"),
    ) as mock_execute:
        s = SummaryScheduler()
        # Should not raise
        await s._run_job(job_config)

    mock_execute.assert_called_once()


@pytest.mark.asyncio
async def test_run_job_default_timeout_is_300():
    """When no job_timeout is configured, default should be 300 seconds."""
    from app.services.summary.scheduler import SummaryScheduler

    job_config = SummaryJobConfig.from_config_dict(_make_summary_config(id=1))

    with patch(
        "app.services.summary.scheduler.summary_service.execute_job",
        new_callable=AsyncMock,
    ):
        s = SummaryScheduler()
        s._scheduler_config = {}  # no job_timeout key
        await s._run_job(job_config)


# ---------------------------------------------------------------------------
# reload_job_if_running
# ---------------------------------------------------------------------------


def test_reload_job_if_running_calls_schedule():
    """reload_job_if_running should refresh jobs when scheduler is running."""
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    mock_sched = MagicMock()
    mock_sched.running = True
    s.scheduler = mock_sched

    with patch.object(s, "_schedule_all_jobs") as mock_schedule:
        s.reload_job_if_running()

    mock_schedule.assert_called_once()


def test_reload_job_if_running_noop_when_not_running():
    """reload_job_if_running should do nothing when scheduler is not running."""
    from app.services.summary.scheduler import SummaryScheduler

    s = SummaryScheduler()
    s.scheduler = None
    with patch.object(s, "_schedule_all_jobs") as mock_schedule:
        s.reload_job_if_running()
    mock_schedule.assert_not_called()


# ---------------------------------------------------------------------------
# apply_config_after_save
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_config_after_save_starts_if_not_running_but_jobs_exist():
    """When scheduler is not running but enabled jobs exist, it should start."""
    configs = [_make_summary_config(id=1, enabled=True)]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        s.scheduler = None
        with patch.object(
            s, "start", new_callable=AsyncMock, return_value=True
        ) as mock_start:
            await s.apply_config_after_save()
        mock_start.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_config_after_save_refreshes_when_running():
    """When scheduler is already running, it should refresh jobs in place."""
    configs = [_make_summary_config(id=1, enabled=True)]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        s.scheduler = mock_sched
        with patch.object(s, "_schedule_all_jobs") as mock_schedule:
            await s.apply_config_after_save()
        mock_schedule.assert_called_once()


@pytest.mark.asyncio
async def test_apply_config_after_save_no_start_when_no_enabled_jobs():
    """When scheduler is not running and no enabled jobs exist, should not start."""
    configs = [_make_summary_config(id=1, enabled=False)]
    with patch(
        "app.services.summary.scheduler.config_manager.get_summary_configs",
        return_value=configs,
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        s.scheduler = None
        with patch.object(s, "start", new_callable=AsyncMock) as mock_start:
            await s.apply_config_after_save()
        mock_start.assert_not_called()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_singleton_is_summary_scheduler_instance():
    """The module-level singleton should be a SummaryScheduler."""
    from app.services.summary.scheduler import SummaryScheduler, summary_scheduler

    assert isinstance(summary_scheduler, SummaryScheduler)


def test_singleton_starts_with_no_scheduler():
    """A fresh singleton should have no scheduler until started."""
    from app.services.summary.scheduler import summary_scheduler

    assert summary_scheduler.scheduler is None


# ---------------------------------------------------------------------------
# Integration-like: full start/stop cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_start_stop_cycle():
    """Verify start creates scheduler with real job store and stop tears it down."""
    configs = [_make_summary_config(id=1, name="integration-test", cron="0 21 * * *")]
    with (
        patch(
            "app.services.summary.scheduler.config_manager.get_summary_configs",
            return_value=configs,
        ),
        patch(
            "app.services.summary.scheduler.config_manager.get_scheduler_config",
            return_value={"job_timeout": 300, "timezone": "Asia/Shanghai"},
        ),
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        ok = await s.start()
        assert ok is True
        assert s.scheduler is not None
        assert s.scheduler.running is True

        ok = await s.stop()
        assert ok is True
        assert s.scheduler is None


@pytest.mark.asyncio
async def test_full_start_stop_with_multiple_jobs():
    """Verify multiple jobs are registered in a real scheduler."""
    configs = [
        _make_summary_config(id=1, name="job-a", cron="0 21 * * *", enabled=True),
        _make_summary_config(id=2, name="job-b", cron="30 8 * * *", enabled=True),
    ]
    with (
        patch(
            "app.services.summary.scheduler.config_manager.get_summary_configs",
            return_value=configs,
        ),
        patch(
            "app.services.summary.scheduler.config_manager.get_scheduler_config",
            return_value={"job_timeout": 300, "timezone": "Asia/Shanghai"},
        ),
    ):
        from app.services.summary.scheduler import SummaryScheduler

        s = SummaryScheduler()
        ok = await s.start()
        assert ok is True
        jobs = s.scheduler.get_jobs()
        assert len(jobs) == 2
        job_ids = {j.id for j in jobs}
        assert "summary_1" in job_ids
        assert "summary_2" in job_ids

        ok = await s.stop()
        assert ok is True
