"""Bangumi API 写路径降级：限速超时/429 入队，5xx 入队并熔断，4xx 上抛。"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api.collection import _PendingSyncQueued
from app.utils.bangumi_api.rate_limit import RateLimitTimeoutError


def _status_error(status: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        f"HTTP {status}",
        request=httpx.Request("POST", "https://api.example.com"),
        response=httpx.Response(status),
    )


def test_mark_episode_watched_queues_on_rate_limit_timeout(monkeypatch):
    """限速冷却超过 acquire 预算：写操作入队，不标不可达、不冒泡。"""
    api = BangumiApi()
    monkeypatch.setattr(
        api,
        "_do_mark_episode_watched",
        MagicMock(side_effect=RateLimitTimeoutError("x")),
    )

    with pytest.raises(_PendingSyncQueued) as excinfo:
        api.mark_episode_watched("1", "2")

    assert excinfo.value.reason == "rate_limited"
    assert api.is_api_unreachable() is False


def test_ensure_subject_watching_queues_on_rate_limit_timeout(monkeypatch):
    api = BangumiApi()
    monkeypatch.setattr(
        api,
        "_do_ensure_subject_watching",
        MagicMock(side_effect=RateLimitTimeoutError("x")),
    )

    with pytest.raises(_PendingSyncQueued) as excinfo:
        api.ensure_subject_watching("1")

    assert excinfo.value.reason == "rate_limited_movie_watching"
    assert api.is_api_unreachable() is False


def test_mark_episode_watched_429_queues_without_breaker(monkeypatch):
    """429 只入队，不叠 300s 熔断（恢复交给 60s 令牌桶冷却）。"""
    api = BangumiApi()
    monkeypatch.setattr(
        api,
        "_do_mark_episode_watched",
        MagicMock(side_effect=_status_error(429)),
    )

    with pytest.raises(_PendingSyncQueued) as excinfo:
        api.mark_episode_watched("1", "2")

    assert excinfo.value.reason == "http_429"
    assert api.is_api_unreachable() is False


def test_mark_episode_watched_5xx_queues_and_marks_unreachable(monkeypatch):
    api = BangumiApi()
    monkeypatch.setattr(
        api,
        "_do_mark_episode_watched",
        MagicMock(side_effect=_status_error(503)),
    )

    with pytest.raises(_PendingSyncQueued) as excinfo:
        api.mark_episode_watched("1", "2")

    assert excinfo.value.reason == "http_503"
    assert api.is_api_unreachable() is True


def test_mark_episode_watched_client_error_propagates(monkeypatch):
    """400 是业务错误：不入队、不熔断，原样上抛。"""
    api = BangumiApi()
    monkeypatch.setattr(
        api,
        "_do_mark_episode_watched",
        MagicMock(side_effect=_status_error(400)),
    )

    with pytest.raises(httpx.HTTPStatusError):
        api.mark_episode_watched("1", "2")

    assert api.is_api_unreachable() is False
