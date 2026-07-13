"""fongmi sync_service 同步流程与去重"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.sync import SyncResponse
from app.services.fongmi.models import FongmiWatchRecord
from app.services.fongmi.sync_service import (
    FONGMI_SYNC_SOURCE,
    fongmi_sync_service,
)


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


def _sample_record(**kwargs) -> FongmiWatchRecord:
    base = dict(
        device_ip="192.168.1.100",
        device_name="客厅电视",
        title="测试番剧",
        episode=1,
        season=1,
        episode_url="http://x/S01E001.mp4",
        artist=None,
        release_date="",
    )
    base.update(kwargs)
    return FongmiWatchRecord(**base)


def test_record_to_custom_item_episode():
    rec = _sample_record()
    item = fongmi_sync_service._record_to_custom_item(rec)
    assert item.media_type == "episode"
    assert item.title == "测试番剧"
    assert item.season == 1
    assert item.episode == 1
    assert item.user_name == "客厅电视"
    assert item.source == FONGMI_SYNC_SOURCE


def test_sync_source_constant():
    assert FONGMI_SYNC_SOURCE == "fongmi"


@pytest.mark.asyncio
async def test_run_sync_not_enabled():
    with patch("app.services.fongmi.sync_service.config_manager") as cm:
        cm.get_fongmi_config.return_value = {"enabled": False}
        r = await fongmi_sync_service.run_sync()
    assert r.success is True
    assert "未启用" in r.message
    assert r.synced_count == 0


@pytest.mark.asyncio
async def test_run_sync_no_devices():
    with patch("app.services.fongmi.sync_service.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        with patch.object(fongmi_sync_service, "_resolve_devices", return_value=[]):
            r = await fongmi_sync_service.run_sync()
    assert r.success is False
    assert "未发现" in r.message


@pytest.mark.asyncio
async def test_run_sync_success(tmp_path):
    rec = _sample_record()
    fongmi_sync_service._synced_keys.clear()

    async def fake_to_thread(*_a, **_kw):
        return SyncResponse(status="success", message="已标记为看过")

    with patch("app.services.fongmi.sync_service.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        with patch.object(
            fongmi_sync_service, "_resolve_devices", return_value=[object()]
        ):
            with patch(
                "app.services.fongmi.sync_service.fetch_completed_records",
                return_value=[rec],
            ):
                with patch(
                    "app.services.fongmi.sync_service.asyncio.to_thread",
                    side_effect=fake_to_thread,
                ):
                    with patch(
                        "app.services.fongmi.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await fongmi_sync_service.run_sync()
    assert r.success is True
    assert r.synced_count == 1
    assert r.error_count == 0
    # 去重集合应包含该记录
    assert ("192.168.1.100", "http://x/S01E001.mp4") in fongmi_sync_service._synced_keys


@pytest.mark.asyncio
async def test_run_sync_skip_already_synced():
    """同一设备同一集第二次同步应跳过"""
    rec = _sample_record()
    fongmi_sync_service._synced_keys.clear()
    fongmi_sync_service._synced_keys.add(("192.168.1.100", "http://x/S01E001.mp4"))

    async def fail_to_thread(*_a, **_kw):
        raise AssertionError("不应调用 Bangumi 同步")

    with patch("app.services.fongmi.sync_service.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        with patch.object(
            fongmi_sync_service, "_resolve_devices", return_value=[object()]
        ):
            with patch(
                "app.services.fongmi.sync_service.fetch_completed_records",
                return_value=[rec],
            ):
                with patch(
                    "app.services.fongmi.sync_service.asyncio.to_thread",
                    side_effect=fail_to_thread,
                ):
                    with patch(
                        "app.services.fongmi.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await fongmi_sync_service.run_sync()
    assert r.success is True
    assert r.skipped_count == 1
    assert r.synced_count == 0


@pytest.mark.asyncio
async def test_run_sync_error_counts():
    rec = _sample_record()
    fongmi_sync_service._synced_keys.clear()

    async def boom(*_a, **_kw):
        raise RuntimeError("sync thread failed")

    with patch("app.services.fongmi.sync_service.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        with patch.object(
            fongmi_sync_service, "_resolve_devices", return_value=[object()]
        ):
            with patch(
                "app.services.fongmi.sync_service.fetch_completed_records",
                return_value=[rec],
            ):
                with patch(
                    "app.services.fongmi.sync_service.asyncio.to_thread",
                    side_effect=boom,
                ):
                    with patch(
                        "app.services.fongmi.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        with patch("app.services.fongmi.sync_service.logger") as log:
                            r = await fongmi_sync_service.run_sync()
    assert r.success is False
    assert r.error_count == 1
    log.error.assert_called()


@pytest.mark.asyncio
async def test_run_sync_ignored_counts_skipped():
    rec = _sample_record()
    fongmi_sync_service._synced_keys.clear()

    async def fake_to_thread(*_a, **_kw):
        return SyncResponse(status="ignored", message="屏蔽关键词")

    with patch("app.services.fongmi.sync_service.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg()
        with patch.object(
            fongmi_sync_service, "_resolve_devices", return_value=[object()]
        ):
            with patch(
                "app.services.fongmi.sync_service.fetch_completed_records",
                return_value=[rec],
            ):
                with patch(
                    "app.services.fongmi.sync_service.asyncio.to_thread",
                    side_effect=fake_to_thread,
                ):
                    with patch(
                        "app.services.fongmi.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await fongmi_sync_service.run_sync()
    assert r.success is True
    assert r.skipped_count == 1
    assert r.synced_count == 0


@pytest.mark.asyncio
async def test_run_sync_ignore_enabled_when_switch_off():
    """ignore_enabled 时即使开关为关也执行扫描"""
    rec = _sample_record()
    fongmi_sync_service._synced_keys.clear()

    async def fake_to_thread(*_a, **_kw):
        return SyncResponse(status="success", message="ok")

    with patch("app.services.fongmi.sync_service.config_manager") as cm:
        cm.get_fongmi_config.return_value = _enabled_cfg(enabled=False)
        with patch.object(
            fongmi_sync_service, "_resolve_devices", return_value=[object()]
        ):
            with patch(
                "app.services.fongmi.sync_service.fetch_completed_records",
                return_value=[rec],
            ):
                with patch(
                    "app.services.fongmi.sync_service.asyncio.to_thread",
                    side_effect=fake_to_thread,
                ):
                    with patch(
                        "app.services.fongmi.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await fongmi_sync_service.run_sync(ignore_enabled=True)
    assert r.synced_count == 1
