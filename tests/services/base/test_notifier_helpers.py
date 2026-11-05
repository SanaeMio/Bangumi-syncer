"""``notifier_helpers`` 失败通知的进程内幂等（run_id 级 LRU 去重）。

背景：run 状态机保证 ``failed`` 为单点终态（同一 run 不会二次判定失败），
跨进程/持久幂等由状态机承担；本层提供**进程内**防御——同一个 run_id 重复
触发时只发送一次，缓存有界（LRU 淘汰最旧）避免无界增长。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.base import notifier_helpers


@pytest.fixture(autouse=True)
def _reset_run_notified_cache():
    """每个用例前后清空进程内去重缓存，保证测试独立。"""
    notifier_helpers._reset_run_notified_cache()
    yield
    notifier_helpers._reset_run_notified_cache()


def test_agent_run_failed_sent_once_per_run_id():
    """同一 run_id 重复触发 → 进程内只发送一次；不同 run 不受影响。"""
    with patch("app.services.base.notifier_helpers.notification_service") as notify:
        notifier_helpers.notify_agent_run_failed(
            "run-dedup-1", task_type="match", stop_reason="llm_error"
        )
        notifier_helpers.notify_agent_run_failed(
            "run-dedup-1", task_type="match", stop_reason="llm_error"
        )
        assert notify.notify.call_count == 1, "同一 run 应只通知一次"

        notifier_helpers.notify_agent_run_failed(
            "run-dedup-2", task_type="match", stop_reason="llm_error"
        )
        assert notify.notify.call_count == 2, "不同 run 应各自发送"


def test_agent_run_failed_dedup_cache_bounded(monkeypatch):
    """去重缓存有界：超出上限的最旧 run 被淘汰后可再次发送（防无界增长）。"""
    monkeypatch.setattr(notifier_helpers, "_RUN_NOTIFIED_CACHE_MAX", 2)
    with patch("app.services.base.notifier_helpers.notification_service") as notify:
        for rid in ("run-a", "run-b", "run-c"):
            notifier_helpers.notify_agent_run_failed(
                rid, task_type="match", stop_reason="llm_error"
            )
        assert notify.notify.call_count == 3

        # "run-a" 已被 LRU 淘汰 → 允许再次发送（缓存不再记录它）
        notifier_helpers.notify_agent_run_failed(
            "run-a", task_type="match", stop_reason="llm_error"
        )
        assert notify.notify.call_count == 4
