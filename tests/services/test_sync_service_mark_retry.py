"""sync_service：标记看过重试（主同步路径中的 _retry_mark_episode）。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.sync_service import SyncService


def test_retry_mark_episode_succeeds_after_one_failure():
    svc = SyncService()
    bgm = MagicMock()
    bgm.mark_episode_watched.side_effect = [ConnectionError("timeout"), 1]

    with patch("app.services.sync_service.time.sleep"):
        status = svc._retry_mark_episode(bgm, "subj", "ep1", max_retries=3)

    assert status == 1
    assert bgm.mark_episode_watched.call_count == 2


def test_retry_mark_episode_succeeds_after_runtime_error():
    svc = SyncService()
    bgm = MagicMock()
    bgm.mark_episode_watched = MagicMock(side_effect=[RuntimeError("x"), 1])
    with patch("app.services.sync_service.time.sleep"):
        r = svc._retry_mark_episode(bgm, "1", "2", max_retries=3)
    assert r == 1


def test_retry_mark_episode_exhausted_raises_last_error():
    svc = SyncService()
    bgm = MagicMock()
    bgm.mark_episode_watched = MagicMock(side_effect=RuntimeError("always"))
    with patch("app.services.sync_service.time.sleep"):
        with pytest.raises(RuntimeError, match="always"):
            svc._retry_mark_episode(bgm, "1", "2", max_retries=1)


@pytest.mark.asyncio
async def test_retry_mark_episode_async_succeeds_after_one_failure():
    svc = SyncService()
    bgm = MagicMock()
    bgm.mark_episode_watched = MagicMock(side_effect=[OSError("a"), 0])
    with patch("app.services.sync_service.asyncio.sleep", new_callable=AsyncMock):
        r = await svc._retry_mark_episode_async(bgm, "9", "8", max_retries=2)
    assert r == 0


@pytest.mark.asyncio
async def test_retry_mark_episode_async_exhausted_raises():
    svc = SyncService()
    bgm = MagicMock()
    bgm.mark_episode_watched = MagicMock(side_effect=OSError("bad"))
    with patch("app.services.sync_service.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(OSError, match="bad"):
            await svc._retry_mark_episode_async(bgm, "9", "8", max_retries=0)
