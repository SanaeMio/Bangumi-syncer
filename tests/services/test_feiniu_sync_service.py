"""飞牛 sync_service 启动水位与 run_sync"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.sync import SyncResponse
from app.services.feiniu.models import FeiniuWatchRecord
from app.services.feiniu.sync_service import feiniu_sync_service


def _enabled_cfg(db_path: str) -> dict:
    return {
        "enabled": True,
        "db_path": db_path,
        "user_filter": "all",
        "time_range": "all",
        "limit": 100,
        "min_percent": 85,
    }


def _sample_record(**kwargs) -> FeiniuWatchRecord:
    base = dict(
        item_guid="it1",
        user_guid="u1",
        username="viewer",
        display_title="测试番剧",
        original_title=None,
        season=1,
        episode=1,
        release_date="2024-01-01",
        update_time_ms=1_700_000_000_000,
    )
    base.update(kwargs)
    return FeiniuWatchRecord(**base)


def test_ensure_feiniu_startup_watermark_disabled_noop():
    from app.services.feiniu.sync_service import ensure_feiniu_startup_watermark

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = {"enabled": False, "db_path": "/x"}
        with patch("app.services.feiniu.sync_service.database_manager") as dbm:
            ensure_feiniu_startup_watermark()
            dbm.get_feiniu_meta.assert_not_called()


def test_ensure_feiniu_startup_watermark_no_db_path():
    from app.services.feiniu.sync_service import ensure_feiniu_startup_watermark

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = {"enabled": True, "db_path": ""}
        with patch("app.services.feiniu.sync_service.database_manager") as dbm:
            ensure_feiniu_startup_watermark()
            dbm.get_feiniu_meta.assert_not_called()


def test_ensure_feiniu_startup_watermark_db_not_a_file():
    from app.services.feiniu.sync_service import ensure_feiniu_startup_watermark

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = {"enabled": True, "db_path": "/nope.db"}
        with patch("app.services.feiniu.sync_service.Path") as PC:
            path_mock = MagicMock()
            path_mock.is_file.return_value = False
            PC.return_value = path_mock
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                ensure_feiniu_startup_watermark()
                dbm.get_feiniu_meta.assert_not_called()


def test_ensure_feiniu_startup_watermark_when_missing():
    from app.services.feiniu.sync_service import ensure_feiniu_startup_watermark

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = {
            "enabled": True,
            "db_path": "/fake/trimmedia.db",
        }
        with patch("app.services.feiniu.sync_service.Path") as PC:
            path_mock = MagicMock()
            path_mock.is_file.return_value = True
            PC.return_value = path_mock
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_feiniu_meta.return_value = None
                ensure_feiniu_startup_watermark()
                dbm.set_feiniu_min_update_watermark_now.assert_called_once()


def test_ensure_feiniu_startup_watermark_skips_when_meta_exists():
    from app.services.feiniu.sync_service import ensure_feiniu_startup_watermark

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = {
            "enabled": True,
            "db_path": "/fake/trimmedia.db",
        }
        with patch("app.services.feiniu.sync_service.Path") as PC:
            path_mock = MagicMock()
            path_mock.is_file.return_value = True
            PC.return_value = path_mock
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_feiniu_meta.return_value = "123"
                ensure_feiniu_startup_watermark()
                dbm.set_feiniu_min_update_watermark_now.assert_not_called()


@pytest.mark.asyncio
async def test_run_sync_not_enabled():
    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = {"enabled": False, "db_path": "/x"}
        r = await feiniu_sync_service.run_sync()
    assert r.success is True
    assert "未启用" in r.message
    assert r.synced_count == 0


@pytest.mark.asyncio
async def test_run_sync_empty_db_path():
    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = {"enabled": True, "db_path": "  "}
        r = await feiniu_sync_service.run_sync()
    assert r.success is False
    assert "db_path" in r.message


@pytest.mark.asyncio
async def test_run_sync_db_file_missing(tmp_path):
    missing = tmp_path / "nope.db"
    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = _enabled_cfg(str(missing))
        r = await feiniu_sync_service.run_sync()
    assert r.success is False
    assert "不存在" in r.message


@pytest.mark.asyncio
async def test_run_sync_success_saves_history(tmp_path):
    dbf = tmp_path / "trim.db"
    dbf.write_bytes(b"")
    rec = _sample_record()

    async def fake_to_thread(*_a, **_kw):
        return SyncResponse(status="success", message="已标记为看过")

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = _enabled_cfg(str(dbf))
        with patch(
            "app.services.feiniu.sync_service.fetch_completed_watch_records",
            return_value=[rec],
        ):
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_or_create_feiniu_min_update_watermark_ms.return_value = 0
                dbm.is_feiniu_item_synced.return_value = False
                with patch(
                    "app.services.feiniu.sync_service.asyncio.to_thread",
                    side_effect=fake_to_thread,
                ):
                    with patch(
                        "app.services.feiniu.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await feiniu_sync_service.run_sync()
    assert r.success is True
    assert r.synced_count == 1
    assert r.skipped_count == 0
    assert r.error_count == 0
    dbm.save_feiniu_sync_history.assert_called_once_with(
        "u1", "it1", rec.update_time_ms
    )


@pytest.mark.asyncio
async def test_run_sync_to_thread_exception_counts_error(tmp_path):
    dbf = tmp_path / "trim_exc.db"
    dbf.write_bytes(b"")
    rec = _sample_record()

    async def boom(*_a, **_kw):
        raise RuntimeError("sync thread failed")

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = _enabled_cfg(str(dbf))
        with patch(
            "app.services.feiniu.sync_service.fetch_completed_watch_records",
            return_value=[rec],
        ):
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_or_create_feiniu_min_update_watermark_ms.return_value = 0
                dbm.is_feiniu_item_synced.return_value = False
                with patch(
                    "app.services.feiniu.sync_service.asyncio.to_thread",
                    side_effect=boom,
                ):
                    with patch(
                        "app.services.feiniu.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        with patch("app.services.feiniu.sync_service.logger") as log:
                            r = await feiniu_sync_service.run_sync()
    assert r.error_count == 1
    assert r.success is False
    log.error.assert_called()
    dbm.save_feiniu_sync_history.assert_not_called()


@pytest.mark.asyncio
async def test_run_sync_error_does_not_save_history(tmp_path):
    dbf = tmp_path / "trim2.db"
    dbf.write_bytes(b"")
    rec = _sample_record()

    async def fake_to_thread(*_a, **_kw):
        return SyncResponse(status="error", message="未找到匹配的番剧")

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = _enabled_cfg(str(dbf))
        with patch(
            "app.services.feiniu.sync_service.fetch_completed_watch_records",
            return_value=[rec],
        ):
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_or_create_feiniu_min_update_watermark_ms.return_value = 0
                dbm.is_feiniu_item_synced.return_value = False
                with patch(
                    "app.services.feiniu.sync_service.asyncio.to_thread",
                    side_effect=fake_to_thread,
                ):
                    with patch(
                        "app.services.feiniu.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await feiniu_sync_service.run_sync()
    assert r.success is False
    assert r.error_count == 1
    dbm.save_feiniu_sync_history.assert_not_called()


@pytest.mark.asyncio
async def test_run_sync_ignored_counts_skipped(tmp_path):
    dbf = tmp_path / "trim3.db"
    dbf.write_bytes(b"")
    rec = _sample_record()

    async def fake_to_thread(*_a, **_kw):
        return SyncResponse(status="ignored", message="屏蔽关键词")

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = _enabled_cfg(str(dbf))
        with patch(
            "app.services.feiniu.sync_service.fetch_completed_watch_records",
            return_value=[rec],
        ):
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_or_create_feiniu_min_update_watermark_ms.return_value = 0
                dbm.is_feiniu_item_synced.return_value = False
                with patch(
                    "app.services.feiniu.sync_service.asyncio.to_thread",
                    side_effect=fake_to_thread,
                ):
                    with patch(
                        "app.services.feiniu.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await feiniu_sync_service.run_sync()
    assert r.success is True
    assert r.skipped_count == 1
    assert r.synced_count == 0
    dbm.save_feiniu_sync_history.assert_not_called()


@pytest.mark.asyncio
async def test_run_sync_skips_video_placeholder_title(tmp_path):
    dbf = tmp_path / "trim4.db"
    dbf.write_bytes(b"")
    rec = _sample_record(display_title="视频-deadbeef", item_guid="vid1")

    async def fail_to_thread(*_a, **_kw):
        raise AssertionError("不应调用 Bangumi 同步")

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = _enabled_cfg(str(dbf))
        with patch(
            "app.services.feiniu.sync_service.fetch_completed_watch_records",
            return_value=[rec],
        ):
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_or_create_feiniu_min_update_watermark_ms.return_value = 0
                dbm.is_feiniu_item_synced.return_value = False
                with patch(
                    "app.services.feiniu.sync_service.asyncio.to_thread",
                    side_effect=fail_to_thread,
                ):
                    with patch(
                        "app.services.feiniu.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await feiniu_sync_service.run_sync()
    assert r.success is True
    assert r.skipped_count == 1


@pytest.mark.asyncio
async def test_run_sync_skip_already_in_history(tmp_path):
    dbf = tmp_path / "trim5.db"
    dbf.write_bytes(b"")
    rec = _sample_record()

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = _enabled_cfg(str(dbf))
        with patch(
            "app.services.feiniu.sync_service.fetch_completed_watch_records",
            return_value=[rec],
        ):
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_or_create_feiniu_min_update_watermark_ms.return_value = 0
                dbm.is_feiniu_item_synced.return_value = True
                with patch(
                    "app.services.feiniu.sync_service.asyncio.to_thread",
                ) as tt:
                    with patch(
                        "app.services.feiniu.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await feiniu_sync_service.run_sync()
    assert r.success is True
    assert r.skipped_count == 1
    assert r.synced_count == 0
    tt.assert_not_called()


@pytest.mark.asyncio
async def test_run_sync_ignore_enabled_when_switch_off(tmp_path):
    """ignore_enabled 时即使 Web 开关为关也执行扫描"""
    dbf = tmp_path / "trim6.db"
    dbf.write_bytes(b"")
    rec = _sample_record()

    async def fake_to_thread(*_a, **_kw):
        return SyncResponse(status="success", message="ok")

    with patch("app.services.feiniu.sync_service.config_manager") as cm:
        cm.get_feiniu_config.return_value = {
            "enabled": False,
            "db_path": str(dbf),
            "user_filter": "all",
            "time_range": "all",
            "limit": 100,
            "min_percent": 85,
        }
        with patch(
            "app.services.feiniu.sync_service.fetch_completed_watch_records",
            return_value=[rec],
        ):
            with patch("app.services.feiniu.sync_service.database_manager") as dbm:
                dbm.get_or_create_feiniu_min_update_watermark_ms.return_value = 0
                dbm.is_feiniu_item_synced.return_value = False
                with patch(
                    "app.services.feiniu.sync_service.asyncio.to_thread",
                    side_effect=fake_to_thread,
                ):
                    with patch(
                        "app.services.feiniu.sync_service.asyncio.sleep",
                        new_callable=AsyncMock,
                    ):
                        r = await feiniu_sync_service.run_sync(ignore_enabled=True)
    assert r.synced_count == 1
