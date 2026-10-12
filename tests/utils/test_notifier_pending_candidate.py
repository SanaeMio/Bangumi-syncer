"""pending_candidate 通知的 payload 与冷却测试（新架构）

原 tests/utils/test_notifier_pending_candidate.py 针对已删除的 Notifier /
html_builders 编写。本次重构后该文件已被删除，这里保留仍然成立的契约：

1. pending_candidate 的类型专属字段（候选数、首选候选）必须能进入渲染后的
   webhook payload —— 该字段由 NotificationTypeMeta.payload_fields 声明。
2. 条目级冷却必须按 title/season/episode 区分，避免"不同番剧互相静默"。

注意与旧实现的差异（旧断言已不成立，勿照搬）：
- 旧实现在字段缺失时输出 ``0`` / ``""``；新实现由 type_fields 丢弃空值，
  因此断言的是"字段不存在"而非"值为 0"。
- 旧 ``type`` 字段是归一化后的 ``watching_summary``；现在是完整类型 id。
"""

import pytest

from app.services.notification_service import CooldownPolicy
from app.utils.notifier.template_manager import NotificationTemplateManager


def _pending_data(**overrides) -> dict:
    data = {
        "notification_type": "pending_candidate",
        "type_display_name": "候选待确认",
        "type_icon": "📝",
        "timestamp": "2026-07-16 12:00:00",
        "user_name": "tester",
        "title": "测试番剧",
        "ori_title": "test anime",
        "season": 2,
        "episode": 5,
        "source": "plex",
        "candidates_count": 3,
        "top_candidate_id": "386809",
        "top_candidate_name": "我推的孩子",
        "error_message": "",
    }
    data.update(overrides)
    return data


# ─────────────────────────────────────────────────────────────────────────
# payload：类型专属字段
# ─────────────────────────────────────────────────────────────────────────


class TestPendingCandidatePayload:
    def test_candidate_fields_reach_payload(self, tmp_path):
        """候选信息必须出现在渲染后的 payload 中，否则用户看不到可选条目"""
        payload = NotificationTemplateManager(
            default_dir=None, custom_dir=tmp_path
        ).render_webhook_payload(_pending_data())
        assert payload["candidates_count"] == 3
        assert payload["top_candidate_id"] == "386809"
        assert payload["top_candidate_name"] == "我推的孩子"

    def test_common_fields_preserved(self, tmp_path):
        payload = NotificationTemplateManager(
            default_dir=None, custom_dir=tmp_path
        ).render_webhook_payload(_pending_data())
        assert payload["type"] == "pending_candidate"
        assert payload["anime"] == "测试番剧"
        assert payload["user"] == "tester"
        assert payload["source"] == "plex"

    def test_title_carries_anime_and_episode(self):
        """标题需带番剧与集数，否则同一类型的多条通知无法区分。

        旧实现（html_builders）曾是「候选待确认 - {title} S02E05」，本次
        重构一度退化为只有「📝 候选待确认」；已由注册表 title_template 恢复。
        """
        from app.services.notification_service import NotificationService
        from app.utils.notifier.channels_impl import WebhookChannel

        svc = NotificationService()
        channel = WebhookChannel(
            "notify-webhook-1", {"url": "https://x", "enabled": True}
        )
        rendered = svc._render_for_channel(
            channel, "pending_candidate", _pending_data()
        )
        title = rendered["payload"]["title"]
        assert title == "📝 候选待确认 - 测试番剧 S02E05"

    def test_empty_candidate_fields_are_dropped(self, tmp_path):
        """字段缺失时不输出 0/空串，而是整个键不存在（与旧行为不同）"""
        data = _pending_data(
            candidates_count=None, top_candidate_id="", top_candidate_name=""
        )
        payload = NotificationTemplateManager(
            default_dir=None, custom_dir=tmp_path
        ).render_webhook_payload(data)
        assert "candidates_count" not in payload
        assert "top_candidate_id" not in payload
        assert "top_candidate_name" not in payload


# ─────────────────────────────────────────────────────────────────────────
# 条目级冷却：不同番剧不应互相静默
# ─────────────────────────────────────────────────────────────────────────


class TestPendingCandidateCooldownPerItem:
    def test_different_anime_not_mutually_silenced(self):
        """同类型同渠道、不同番剧的通知必须各自发出"""
        policy = CooldownPolicy(cooldown_seconds=60)
        a = {"title": "番剧A", "season": 1, "episode": 1}
        b = {"title": "番剧B", "season": 1, "episode": 1}

        assert policy.allow("notify-webhook-1", "pending_candidate", a) is True
        assert policy.allow("notify-webhook-1", "pending_candidate", b) is True

    def test_same_anime_same_episode_throttled(self):
        policy = CooldownPolicy(cooldown_seconds=60)
        a = {"title": "番剧A", "season": 1, "episode": 1}

        assert policy.allow("notify-webhook-1", "pending_candidate", a) is True
        assert policy.allow("notify-webhook-1", "pending_candidate", a) is False

    def test_different_episode_same_anime_allowed(self):
        """同番剧不同集数应各自通知"""
        policy = CooldownPolicy(cooldown_seconds=60)
        assert (
            policy.allow(
                "notify-webhook-1",
                "pending_candidate",
                {"title": "番剧A", "season": 1, "episode": 1},
            )
            is True
        )
        assert (
            policy.allow(
                "notify-webhook-1",
                "pending_candidate",
                {"title": "番剧A", "season": 1, "episode": 2},
            )
            is True
        )

    def test_different_channel_not_mutually_silenced(self):
        """按渠道区分冷却：两个 webhook 各自都能收到"""
        policy = CooldownPolicy(cooldown_seconds=60)
        a = {"title": "番剧A", "season": 1, "episode": 1}

        assert policy.allow("notify-webhook-1", "pending_candidate", a) is True
        assert policy.allow("notify-webhook-2", "pending_candidate", a) is True

    def test_cooldown_key_differs_per_item(self):
        policy = CooldownPolicy()
        key_a = policy._key(
            "c1", "pending_candidate", {"title": "番剧A", "season": 1, "episode": 1}
        )
        key_b = policy._key(
            "c1", "pending_candidate", {"title": "番剧B", "season": 1, "episode": 1}
        )
        assert key_a != key_b

    def test_allow_after_cooldown_expires(self):
        """冷却窗口过后应再次放行（旧测试覆盖、新套件缺失的场景）"""
        policy = CooldownPolicy(cooldown_seconds=0)
        a = {"title": "番剧A", "season": 1, "episode": 1}

        assert policy.allow("notify-webhook-1", "pending_candidate", a) is True
        assert policy.allow("notify-webhook-1", "pending_candidate", a) is True

    def test_skip_bypasses_cooldown(self):
        """skip=True 时无条件放行"""
        policy = CooldownPolicy(cooldown_seconds=3600)
        a = {"title": "番剧A", "season": 1, "episode": 1}

        assert policy.allow("notify-webhook-1", "pending_candidate", a) is True
        assert (
            policy.allow("notify-webhook-1", "pending_candidate", a, skip=True) is True
        )

    def test_system_level_type_cooldown_ignores_item(self):
        """非条目级类型只按 channel+type 冷却，与番剧无关"""
        policy = CooldownPolicy(cooldown_seconds=60)
        assert policy.allow("c1", "source_fetch_failed", {"title": "A"}) is True
        assert policy.allow("c1", "source_fetch_failed", {"title": "B"}) is False


@pytest.mark.parametrize(
    "notification_type,expected_fields",
    [
        (
            "pending_candidate",
            {
                "candidates_count": 3,
                "top_candidate_id": "386809",
                "top_candidate_name": "我推的孩子",
            },
        ),
        (
            "match_ambiguous",
            {
                "final_subject_id": "100",
                "top1_name": "候选一",
                "top1_score": 0.91,
                "top2_name": "候选二",
                "top2_score": 0.89,
                "score_diff": 0.02,
            },
        ),
    ],
)
def test_match_quality_fields_reach_payload(
    tmp_path, notification_type, expected_fields
):
    """匹配质量类通知的专属字段端到端可达 payload（此前无测试覆盖）"""
    data = {
        "notification_type": notification_type,
        "type_display_name": "匹配",
        "type_icon": "🤔",
        "timestamp": "t",
        "user_name": "u",
        "title": "某番",
        "season": 1,
        "episode": 1,
        "source": "plex",
        "error_message": "",
        **expected_fields,
    }
    payload = NotificationTemplateManager(
        default_dir=None, custom_dir=tmp_path
    ).render_webhook_payload(data)
    for key, value in expected_fields.items():
        assert payload.get(key) == value, f"{notification_type} 丢失字段 {key}"
