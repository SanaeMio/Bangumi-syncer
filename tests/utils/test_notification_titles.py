"""标题模板回归测试（含 H1：per-type 标题信息不得丢失）

背景：删除 html_builders.py 时，旧实现的 per-type 邮件主题/标题（形如
「同步成功 - {title} S01E02」）一度退化为只有「✅ 同步成功」，导致同一类型
的多条通知无法区分。现由注册表 NotificationTypeMeta.title_template 恢复，
并由 NotificationService 在 {ep_label} 就位后渲染。

本文件钉死这些 per-type 标题，防止再次静默丢失。
"""

import pytest

from app.core.notification_registry import get_type_meta
from app.services.notification_service import NotificationService
from app.utils.notifier.channels_impl import EmailChannel, WebhookChannel

ITEM = {
    "title": "葬送的芙莉莲",
    "season": 1,
    "episode": 2,
    "user_name": "tester",
    "source": "plex",
}


@pytest.fixture
def svc():
    return NotificationService()


@pytest.fixture
def webhook():
    return WebhookChannel("notify-webhook-1", {"url": "https://x", "enabled": True})


@pytest.fixture
def email():
    return EmailChannel(
        "notify-email-1",
        {"smtp_server": "s", "email_to": "d@e.com", "enabled": True},
    )


# ─────────────────────────────────────────────────────────────────────────
# H1：per-type 标题必须带番剧/集数
# ─────────────────────────────────────────────────────────────────────────


class TestPerTypeTitlesRestored:
    @pytest.mark.parametrize(
        "notification_type,expected",
        [
            ("mark_success", "✅ 同步成功 - 葬送的芙莉莲 S01E02"),
            ("mark_failed", "❌ 同步失败 - 葬送的芙莉莲 S01E02"),
            ("mark_skipped", "⏭️ 已看过 - 葬送的芙莉莲 S01E02"),
            ("request_received", "📥 收到同步请求 - 葬送的芙莉莲 S01E02"),
            ("episode_not_found", "📺 未找到剧集 - 葬送的芙莉莲 S01E02"),
            ("pending_candidate", "📝 候选待确认 - 葬送的芙莉莲 S01E02"),
            ("bangumi_id_found", "🔍 匹配到番剧 - 葬送的芙莉莲"),
            ("anime_not_found", "🔍 未找到番剧 - 葬送的芙莉莲"),
        ],
    )
    def test_title_contains_anime(self, svc, webhook, notification_type, expected):
        rendered = svc._render_for_channel(webhook, notification_type, ITEM)
        assert rendered["payload"]["title"] == expected

    def test_watching_summary_title_has_job_name(self, svc, webhook):
        rendered = svc._render_for_channel(
            webhook,
            "watching_summary_每日总结",
            {"job_name": "每日总结", "summary_text": "正文"},
        )
        assert rendered["payload"]["title"] == "📊 追番总结 - 每日总结"

    def test_email_subject_matches_title(self, svc, webhook, email):
        """邮件主题与 webhook 标题同源，避免两处不一致"""
        for t in ("mark_success", "mark_failed", "pending_candidate"):
            w = svc._render_for_channel(webhook, t, ITEM)
            e = svc._render_for_channel(email, t, ITEM)
            assert e["subject"] == w["payload"]["title"]

    def test_types_without_title_template_use_display_name(self, svc, webhook):
        """未声明 title_template 的类型回退为「图标 + 展示名」"""
        rendered = svc._render_for_channel(
            webhook, "config_error", {"error_message": "e"}
        )
        assert rendered["payload"]["title"] == "⚙️ 配置错误"


class TestEpisodeLabel:
    def test_two_digit_padding(self, svc, webhook):
        """集数补零，保持与旧实现 S01E02 一致"""
        r = svc._render_for_channel(
            webhook, "mark_success", {"title": "T", "season": 1, "episode": 2}
        )
        assert r["payload"]["episode"] == "S01E02"

    def test_large_numbers_not_truncated(self, svc, webhook):
        r = svc._render_for_channel(
            webhook, "mark_success", {"title": "T", "season": 12, "episode": 25}
        )
        assert r["payload"]["episode"] == "S12E25"

    def test_movie_uses_special_label(self, svc, webhook):
        r = svc._render_for_channel(
            webhook, "mark_success", {"title": "T", "media_type": "movie"}
        )
        assert r["payload"]["episode"] == "剧场版"

    def test_missing_season_episode_omits_label(self, svc, webhook):
        """无季/集号信息时集数标签为空串（「无值省略」），而不是 S00E00。

        原先补零兜底会在正文/标题里留下无意义的 S00E00；现在由
        derive_ep_label 返回空串，交由模板的「无值省略」处理。
        """
        r = svc._render_for_channel(webhook, "mark_success", {"title": "T"})
        assert r["payload"]["episode"] == ""
        # 标题里不应出现凭空补出来的 S00E00
        assert "S00E00" not in r["payload"]["title"]

    def test_existing_ep_label_preserved(self, svc, webhook):
        """调用方已显式给出 ep_label 时不应被覆盖"""
        r = svc._render_for_channel(
            webhook, "mark_success", {"title": "T", "ep_label": "自定义标签"}
        )
        assert r["payload"]["episode"] == "自定义标签"
        assert "自定义标签" in r["payload"]["title"]


class TestTitleTemplateMechanism:
    def test_all_item_level_types_have_titles(self):
        """条目级类型的标题必须能区分具体条目（即含 {title}）"""
        from app.core.notification_registry import all_types

        missing = [
            m.id
            for m in all_types()
            if m.is_item_level and "{title}" not in (m.title_template or "")
        ]
        assert missing == [], f"这些条目级类型标题缺 title 占位符: {missing}"

    def test_default_title_uses_icon(self):
        meta = get_type_meta("mark_success")
        assert meta.default_title().startswith("✅ ")

    def test_unknown_type_falls_back(self, svc, webhook):
        r = svc._render_for_channel(webhook, "no_such_type", {"title": "T"})
        assert r["payload"]["title"] == "T"


class TestDocsExampleMatchesRealOutput:
    """文档里的「实际发出去的内容」示例必须与真实渲染一致。

    背景（review 反馈）：docs/config/notification-configuration.md 用追番总结
    演示 webhook 的最终形态。该示例中的 ``"episode": "S00E00"`` 在无集数类型
    改为空串后**没有同步更新**，读者会以为追番总结仍会带 S00E00。

    这类「文档示例与代码脱节」不会被任何现有测试发现（测试只断言代码行为，
    不读文档）。此处直接把文档里的 JSON 块解析出来与真实渲染结果比对，
    让示例一旦漂移就失败。
    """

    DOC_PATH = "docs/config/notification-configuration.md"

    @staticmethod
    def _summary_data() -> dict:
        """追番总结（无集数信息）的渲染数据，字段取自 _WATCHING_SUMMARY_META"""
        return {
            "payload_title": "📊 追番总结 - 每日总结",
            "notification_type": "watching_summary_daily",
            "timestamp": "2026-08-15 09:00:00",
            "user_name": "alice",
            "title": "",
            "source": "summary",
            "error_message": "",
            "job_name": "每日总结",
            "summary_text": "本周共观看 3 部番剧，合计 12 集……",
            "date_range": "2026-08-09 ~ 2026-08-15",
            "record_count": 12,
        }

    def _doc_example(self) -> dict:
        """从文档中抽出「追番总结」那段 JSON 示例"""
        import json
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        text = (root / self.DOC_PATH).read_text(encoding="utf-8")
        # 定位演示块：含 watching_summary_daily 的那个 json 代码块
        for block in re.findall(r"```json\n(.*?)```", text, re.DOTALL):
            if "watching_summary_daily" in block:
                return json.loads(block)
        raise AssertionError(f"{self.DOC_PATH} 中找不到追番总结的 JSON 示例")

    def test_doc_example_episode_is_empty(self):
        """示例里的 episode 必须是空串（无集数信息），不能是 S00E00"""
        assert self._doc_example()["episode"] == ""

    def test_doc_example_matches_real_render(self):
        """文档示例的每个字段都要与真实渲染结果一致"""
        from app.utils.notifier.template_manager import template_manager

        expected = template_manager.render_webhook_payload(self._summary_data())
        doc = self._doc_example()

        assert doc == expected, (
            "文档示例与真实渲染不一致（文档可能已过期）:\n"
            f"  仅文档有: { {k: v for k, v in doc.items() if expected.get(k) != v} }\n"
            f"  仅代码有: { {k: v for k, v in expected.items() if doc.get(k) != v} }"
        )

    def test_doc_example_has_no_extra_key(self):
        """示例必须体现哨兵被摘除（顶层没有 extra 键）"""
        assert "extra" not in self._doc_example()
