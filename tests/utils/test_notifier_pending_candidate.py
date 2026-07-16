"""pending_candidate 通知类型构建器测试

覆盖 webhook 默认 payload、email simple_html/subject/text/dynamic_content。
"""

from app.core.config import config_manager
from app.utils.notifier import Notifier


def _make_notifier() -> Notifier:
    return Notifier(config_manager)


def _pending_data() -> dict:
    return {
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
    }


class TestWebhookPayloadPendingCandidate:
    """webhook 默认 payload 构建"""

    def test_payload_contains_pending_candidate_fields(self):
        notifier = _make_notifier()
        payload = notifier._build_payload_by_type(
            "pending_candidate", _pending_data(), template=""
        )
        assert payload["type"] == "pending_candidate"
        assert payload["title"] == "📝 候选待确认"
        assert payload["user"] == "tester"
        assert payload["anime"] == "测试番剧"
        assert payload["episode"] == "S02E05"
        assert payload["source"] == "plex"
        assert payload["candidates_count"] == 3
        assert payload["top_candidate_id"] == "386809"
        assert payload["top_candidate_name"] == "我推的孩子"

    def test_payload_with_empty_candidates(self):
        """无候选数据时字段有默认值"""
        notifier = _make_notifier()
        payload = notifier._build_payload_by_type(
            "pending_candidate",
            {
                "timestamp": "t",
                "user_name": "u",
                "title": "x",
                "season": 1,
                "episode": 1,
            },
            template="",
        )
        assert payload["candidates_count"] == 0
        assert payload["top_candidate_id"] == ""
        assert payload["top_candidate_name"] == ""


class TestEmailSubjectPendingCandidate:
    def test_subject_contains_title_and_episode(self):
        notifier = _make_notifier()
        subject = notifier._build_email_subject_by_type(
            "pending_candidate", _pending_data()
        )
        assert "候选待确认" in subject
        assert "测试番剧" in subject
        assert "S02E05" in subject


class TestEmailSimpleHtmlPendingCandidate:
    def test_simple_html_contains_candidates_info(self):
        notifier = _make_notifier()
        html = notifier._build_simple_email_html(
            {"notification_type": "pending_candidate", **_pending_data()}
        )
        assert "候选待确认" in html
        assert "候选数" in html
        assert "3" in html
        assert "首选候选" in html
        assert "我推的孩子" in html
        assert "386809" in html


class TestEmailTextPendingCandidate:
    def test_text_contains_candidates_info(self):
        notifier = _make_notifier()
        text = notifier._build_email_text_by_type("pending_candidate", _pending_data())
        assert "候选待确认" in text
        assert "候选数: 3" in text
        assert "首选候选: 我推的孩子" in text
        assert "386809" in text


class TestEmailDynamicContentPendingCandidate:
    def test_dynamic_content_builder_registered(self):
        """dynamic_content builder 注册并能返回 HTML"""
        notifier = _make_notifier()
        html = notifier._build_email_dynamic_content(
            "pending_candidate", _pending_data()
        )
        assert "候选待确认" in html
        assert "候选数" in html
        assert "首选候选" in html
        assert "我推的孩子" in html

    def test_dynamic_content_without_top_candidate(self):
        """无首选候选时不渲染首选行"""
        notifier = _make_notifier()
        data = _pending_data()
        data["top_candidate_name"] = ""
        data["top_candidate_id"] = ""
        html = notifier._build_email_dynamic_content("pending_candidate", data)
        assert "候选待确认" in html
        assert "首选候选" not in html
