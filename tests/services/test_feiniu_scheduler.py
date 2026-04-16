"""飞牛调度器 Cron 解析"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.services.feiniu.scheduler import FeiniuScheduler


def test_default_feiniu_cron_trigger():
    s = FeiniuScheduler()
    t = s._default_feiniu_cron_trigger()
    assert isinstance(t, CronTrigger)


def test_parse_cron_invalid_falls_back_to_default():
    s = FeiniuScheduler()
    t = s._parse_cron("not-five-fields")
    assert isinstance(t, CronTrigger)


def test_parse_cron_valid_five_fields():
    s = FeiniuScheduler()
    t = s._parse_cron("0 3 * * *")
    assert isinstance(t, CronTrigger)


@pytest.mark.asyncio
async def test_feiniu_start_when_disabled_returns_true_without_scheduler():
    with patch(
        "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
        return_value={"enabled": False},
    ):
        s = FeiniuScheduler()
        ok = await s.start()
        assert ok is True
        assert s.scheduler is None


@pytest.mark.asyncio
async def test_feiniu_stop_when_no_scheduler_returns_true():
    s = FeiniuScheduler()
    assert await s.stop() is True


@pytest.mark.asyncio
async def test_feiniu_start_stop_with_real_db_file(tmp_path):
    db = tmp_path / "trim.db"
    db.write_bytes(b"x")
    with (
        patch(
            "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
            return_value={
                "enabled": True,
                "db_path": str(db),
                "sync_interval": "0 * * * *",
            },
        ),
        patch(
            "app.services.feiniu.scheduler.config_manager.get_scheduler_config",
            return_value={"job_timeout": 30},
        ),
    ):
        s = FeiniuScheduler()
        ok = await s.start()
        assert ok is True
        assert s.scheduler is not None
        assert await s.stop() is True
        assert s.scheduler is None


@pytest.mark.asyncio
async def test_feiniu_start_when_already_running_refreshes_job():
    s = FeiniuScheduler()
    mock_sched = MagicMock()
    mock_sched.running = True
    mock_sched.remove_job = MagicMock(side_effect=Exception("no job"))
    s.scheduler = mock_sched
    with patch.object(s, "_feiniu_enabled_with_db", return_value=True):
        with patch.object(s, "_schedule_or_refresh_job") as m:
            ok = await s.start()
    assert ok is True
    m.assert_called_once()


@pytest.mark.asyncio
async def test_feiniu_start_when_running_but_disabled_removes_job():
    s = FeiniuScheduler()
    mock_sched = MagicMock()
    mock_sched.running = True
    mock_sched.remove_job = MagicMock()
    s.scheduler = mock_sched
    with patch.object(s, "_feiniu_enabled_with_db", return_value=False):
        ok = await s.start()
    assert ok is True
    mock_sched.remove_job.assert_called()


@pytest.mark.asyncio
async def test_feiniu_start_constructor_raises_returns_false():
    with (
        patch(
            "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
            return_value={
                "enabled": True,
                "db_path": "/fake/trim.db",
                "sync_interval": "* * * * *",
            },
        ),
        patch("pathlib.Path.is_file", return_value=True),
        patch(
            "app.services.feiniu.scheduler.AsyncIOScheduler",
            side_effect=RuntimeError("boom"),
        ),
    ):
        s = FeiniuScheduler()
        ok = await s.start()
        assert ok is False


@pytest.mark.asyncio
async def test_feiniu_stop_shutdown_raises_returns_false():
    s = FeiniuScheduler()
    mock_sched = MagicMock()
    mock_sched.running = True
    mock_sched.shutdown = MagicMock(side_effect=OSError("shutdown"))
    s.scheduler = mock_sched
    ok = await s.stop()
    assert ok is False


@pytest.mark.asyncio
async def test_feiniu_reload_job_if_running_calls_schedule(tmp_path):
    db = tmp_path / "t.db"
    db.write_bytes(b"a")
    with patch(
        "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
        return_value={
            "enabled": True,
            "db_path": str(db),
            "sync_interval": "*/15 * * * *",
        },
    ):
        s = FeiniuScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        s.scheduler = mock_sched
        with patch.object(s, "_feiniu_enabled_with_db", return_value=True):
            with patch.object(s, "_schedule_or_refresh_job") as m:
                s.reload_job_if_running()
        m.assert_called_once()


@pytest.mark.asyncio
async def test_feiniu_apply_config_after_save_starts_when_enabled(tmp_path):
    db = tmp_path / "a.db"
    db.write_bytes(b"b")
    with patch(
        "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
        return_value={
            "enabled": True,
            "db_path": str(db),
            "sync_interval": "*/15 * * * *",
        },
    ):
        s = FeiniuScheduler()
        with patch.object(s, "start", new_callable=AsyncMock, return_value=True) as st:
            await s.apply_config_after_save()
        st.assert_awaited_once()


@pytest.mark.asyncio
async def test_feiniu_apply_config_after_save_stops_when_disabled():
    with patch(
        "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
        return_value={"enabled": False},
    ):
        s = FeiniuScheduler()
        with patch.object(s, "stop", new_callable=AsyncMock, return_value=True) as sp:
            await s.apply_config_after_save()
        sp.assert_awaited_once()


@pytest.mark.asyncio
async def test_feiniu_run_sync_job_timeout(tmp_path):
    async def hang():
        await asyncio.sleep(99)

    db = tmp_path / "w.db"
    db.write_bytes(b"c")
    with (
        patch(
            "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
            return_value={
                "enabled": True,
                "db_path": str(db),
                "sync_interval": "*/15 * * * *",
            },
        ),
        patch(
            "app.services.feiniu.scheduler.config_manager.get_scheduler_config",
            return_value={"job_timeout": 1},
        ),
        patch(
            "app.services.feiniu.scheduler.feiniu_sync_service.run_sync",
            side_effect=hang,
        ),
    ):
        s = FeiniuScheduler()
        await s._run_sync_job()


@pytest.mark.asyncio
async def test_feiniu_apply_config_refreshes_job_when_scheduler_running(tmp_path):
    db = tmp_path / "c.db"
    db.write_bytes(b"z")
    with patch(
        "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
        return_value={
            "enabled": True,
            "db_path": str(db),
            "sync_interval": "*/15 * * * *",
        },
    ):
        s = FeiniuScheduler()
        mock_sched = MagicMock()
        mock_sched.running = True
        s.scheduler = mock_sched
        with patch.object(s, "_feiniu_enabled_with_db", return_value=True):
            with patch.object(s, "_schedule_or_refresh_job") as m:
                await s.apply_config_after_save()
        m.assert_called_once()


@pytest.mark.asyncio
async def test_feiniu_run_sync_job_sync_raises(tmp_path):
    db = tmp_path / "e.db"
    db.write_bytes(b"d")
    with (
        patch(
            "app.services.feiniu.scheduler.config_manager.get_feiniu_config",
            return_value={
                "enabled": True,
                "db_path": str(db),
                "sync_interval": "*/15 * * * *",
            },
        ),
        patch(
            "app.services.feiniu.scheduler.config_manager.get_scheduler_config",
            return_value={"job_timeout": 60},
        ),
        patch(
            "app.services.feiniu.scheduler.feiniu_sync_service.run_sync",
            new_callable=AsyncMock,
            side_effect=ValueError("sync err"),
        ),
    ):
        s = FeiniuScheduler()
        await s._run_sync_job()
