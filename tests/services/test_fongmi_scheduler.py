"""fongmi scheduler 配置与生命周期"""

from unittest.mock import patch

import pytest

from app.services.fongmi.scheduler import FongmiScheduler


def _enabled_cfg(**kwargs) -> dict:
    base = {
        "enabled": True,
        "devices": "",
        "subnet": "",
        "auto_scan": False,
        "sync_interval": "*/5 * * * *",
        "min_percent": 95,
    }
    base.update(kwargs)
    return base


def _disabled_cfg() -> dict:
    return _enabled_cfg(enabled=False)


def test_parse_cron_valid():
    s = FongmiScheduler()
    trigger = s._parse_cron("*/5 * * * *")
    assert trigger is not None


def test_parse_cron_invalid_falls_back():
    s = FongmiScheduler()
    trigger = s._parse_cron("bad cron")
    # 应回退到默认 trigger（不抛异常）
    assert trigger is not None


def test_fongmi_enabled_true():
    s = FongmiScheduler()
    with patch("app.services.fongmi.scheduler.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        assert s._fongmi_enabled() is True


def test_fongmi_enabled_false():
    s = FongmiScheduler()
    with patch("app.services.fongmi.scheduler.config_manager") as cm:
        cm.get_fongmi_config.return_value = _disabled_cfg()
        assert s._fongmi_enabled() is False


@pytest.mark.asyncio
async def test_start_when_disabled_no_scheduler_created():
    s = FongmiScheduler()
    with patch("app.services.fongmi.scheduler.config_manager") as cm:
        cm.get_fongmi_config.return_value = _disabled_cfg()
        cm.get_scheduler_config.return_value = {"job_timeout": 300}
        ok = await s.start()
    assert ok is True
    assert s.scheduler is None  # 未启用时不创建调度器


@pytest.mark.asyncio
async def test_start_when_enabled_creates_scheduler():
    s = FongmiScheduler()
    with patch("app.services.fongmi.scheduler.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        cm.get_scheduler_config.return_value = {"job_timeout": 300}
        ok = await s.start()
    assert ok is True
    assert s.scheduler is not None
    assert s.scheduler.running is True
    # 清理
    await s.stop()


@pytest.mark.asyncio
async def test_stop_when_running():
    s = FongmiScheduler()
    with patch("app.services.fongmi.scheduler.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        cm.get_scheduler_config.return_value = {"job_timeout": 300}
        await s.start()
        ok = await s.stop()
    assert ok is True
    assert s.scheduler is None


@pytest.mark.asyncio
async def test_stop_when_not_running():
    s = FongmiScheduler()
    ok = await s.stop()
    assert ok is True


@pytest.mark.asyncio
async def test_apply_config_after_save_disables():
    s = FongmiScheduler()
    with patch("app.services.fongmi.scheduler.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        cm.get_scheduler_config.return_value = {"job_timeout": 300}
        await s.start()
        # 切换为关闭
        cm.get_fongmi_config.return_value = _disabled_cfg()
        await s.apply_config_after_save()
    assert s.scheduler is None
