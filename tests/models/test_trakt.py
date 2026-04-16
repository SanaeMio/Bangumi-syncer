"""app.models.trakt Pydantic 模型与令牌时间逻辑。"""

from unittest.mock import MagicMock, patch

import pytest

from app.models import trakt as trakt_models
from app.models.trakt import (
    TraktApiConfigUpdateRequest,
    TraktAuthRequest,
    TraktAuthResponse,
    TraktCallbackRequest,
    TraktCallbackResponse,
    TraktConfig,
    TraktConfigResponse,
    TraktConfigUpdateRequest,
    TraktManualSyncRequest,
    TraktManualSyncResponse,
    TraktSyncHistory,
    TraktSyncStatusResponse,
)


class _MockDateTime:
    _ts = 1_700_000_000.0

    @classmethod
    def now(cls):
        m = MagicMock()
        m.timestamp = lambda: cls._ts
        return m


def test_trakt_config_from_dict_none():
    assert TraktConfig.from_dict(None) is None


def test_trakt_config_from_dict_enabled_variants():
    base = {
        "user_id": "u",
        "access_token": "t",
        "expires_at": 1,
    }
    assert TraktConfig.from_dict({**base, "enabled": 1}).enabled is True
    assert TraktConfig.from_dict({**base, "enabled": 0}).enabled is False
    assert TraktConfig.from_dict(dict(base)).enabled is True


def test_trakt_config_to_dict_roundtrip():
    cfg = TraktConfig(
        user_id="u1",
        access_token="at",
        refresh_token="rt",
        expires_at=99,
        enabled=False,
        sync_interval="0 1 * * *",
        last_sync_time=10,
        created_at=1,
        updated_at=2,
    )
    d = cfg.to_dict()
    assert d["enabled"] == 0
    back = TraktConfig.from_dict(d)
    assert back.user_id == "u1"
    assert back.access_token == "at"
    assert back.enabled is False
    assert back.sync_interval == "0 1 * * *"


@pytest.mark.parametrize(
    ("expires_at", "now_ts", "expected_expired", "expected_refresh"),
    [
        (None, 1_700_000_000, True, True),
        (1_700_000_600, 1_700_000_000, False, False),
        (1_700_000_100, 1_700_000_000, False, True),
        (1_699_999_900, 1_700_000_000, True, True),
    ],
)
def test_trakt_config_token_expiry_and_refresh(
    expires_at, now_ts, expected_expired, expected_refresh
):
    cfg = TraktConfig(user_id="u", access_token="t", expires_at=expires_at)
    with patch.object(trakt_models, "datetime", _MockDateTime):
        _MockDateTime._ts = now_ts
        assert cfg.is_token_expired() is expected_expired
        assert cfg.refresh_if_needed() is expected_refresh


def test_trakt_sync_history_to_dict_and_from_dict():
    h = TraktSyncHistory(
        user_id="u",
        trakt_item_id="i",
        media_type="episode",
        watched_at=5,
        synced_at=6,
    )
    d = h.to_dict()
    assert d["media_type"] == "episode"
    back = TraktSyncHistory.from_dict(d)
    assert back.user_id == "u"
    assert back.synced_at == 6


def test_trakt_sync_history_from_dict_none():
    assert TraktSyncHistory.from_dict(None) is None


def test_trakt_sync_history_synced_at_default():
    with patch.object(trakt_models, "datetime", _MockDateTime):
        _MockDateTime._ts = 42.0
        h = TraktSyncHistory.from_dict(
            {
                "user_id": "u",
                "trakt_item_id": "i",
                "media_type": "movie",
                "watched_at": 1,
            }
        )
        assert h.synced_at == 42


def test_trakt_request_response_models_construct():
    TraktAuthRequest(user_id="x")
    TraktAuthResponse(auth_url="https://a", state="s")
    TraktCallbackRequest(code="c", state="st")
    TraktCallbackResponse(success=True, message="ok")
    TraktConfigResponse(
        user_id="u",
        enabled=True,
        sync_interval="* * * * *",
        is_connected=True,
    )
    TraktConfigUpdateRequest(enabled=False)
    TraktApiConfigUpdateRequest(client_id="id")
    TraktSyncStatusResponse(is_running=False)
    TraktManualSyncRequest(user_id="u")
    TraktManualSyncResponse(success=True, message="done", job_id=None)
