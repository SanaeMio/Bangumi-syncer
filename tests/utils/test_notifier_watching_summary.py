"""
Tests for watching_summary notification type in Notifier.
"""

from unittest.mock import MagicMock

from app.utils.notifier import Notifier


class TestWatchingSummaryPayload:
    """Tests for _build_payload_by_type with watching_summary type."""

    @staticmethod
    def _make_notifier() -> Notifier:
        mock_config = MagicMock()
        return Notifier(mock_config)

    @staticmethod
    def _make_watching_data() -> dict:
        return {
            "job_name": "MyDailySummary",
            "timestamp": "2024-07-15 10:00:00",
            "summary_text": "本周共追番5部，更新12集",
            "date_range": "2024-07-08 ~ 2024-07-15",
            "record_count": 12,
            "user_name": "testuser",
        }

    def test_payload_for_watching_summary_has_expected_keys(self):
        """Payload for 'watching_summary' should contain all expected fields."""
        notifier = self._make_notifier()
        data = self._make_watching_data()

        payload = notifier._build_payload_by_type("watching_summary", data, "")

        assert payload["title"] == "📊 追番总结 - MyDailySummary"
        assert payload["type"] == "watching_summary"
        assert payload["timestamp"] == "2024-07-15 10:00:00"
        assert payload["summary"] == "本周共追番5部，更新12集"
        assert payload["date_range"] == "2024-07-08 ~ 2024-07-15"
        assert payload["record_count"] == 12
        assert payload["user_name"] == "testuser"

    def test_payload_for_watching_summary_dad_has_expected_keys(self):
        """Payload for 'watching_summary_dad' should contain all expected fields."""
        notifier = self._make_notifier()
        data = self._make_watching_data()
        data["job_name"] = "DadSummary"

        payload = notifier._build_payload_by_type("watching_summary_dad", data, "")

        assert payload["title"] == "📊 追番总结 - DadSummary"
        assert payload["type"] == "watching_summary_dad"
        assert payload["summary"] == "本周共追番5部，更新12集"

    def test_payload_handles_missing_fields_gracefully(self):
        """Payload should use defaults when data fields are missing."""
        notifier = self._make_notifier()
        data: dict = {}

        payload = notifier._build_payload_by_type("watching_summary", data, "")

        assert payload["title"] == "📊 追番总结 - "
        assert payload["summary"] == ""
        assert payload["date_range"] == ""
        assert payload["record_count"] == 0
        assert payload["user_name"] == ""


class TestWatchingSummaryEmailSubject:
    """Tests for _build_email_subject_by_type with watching_summary type."""

    def test_subject_for_watching_summary(self):
        """Email subject should contain job_name and proper prefix."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"job_name": "WeeklyReport"}

        result = notifier._build_email_subject_by_type("watching_summary", data)

        assert "追番总结" in result
        assert "WeeklyReport" in result
        assert result.startswith("[Bangumi-Syncer]")

    def test_subject_for_watching_summary_default_name(self):
        """Email subject should work even when job_name is missing."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        result = notifier._build_email_subject_by_type("watching_summary", {})

        assert "追番总结" in result


class TestWatchingSummaryDynamicContent:
    """Tests for _build_email_dynamic_content with watching_summary type."""

    def test_dynamic_content_for_watching_summary_contains_key_info(self):
        """Dynamic HTML content should include job_name, date_range, record_count, summary_text."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "job_name": "DailySummary",
            "date_range": "2024-07-01 ~ 2024-07-07",
            "record_count": 8,
            "summary_text": "本周更新8集",
        }

        result = notifier._build_email_dynamic_content("watching_summary", data)

        assert "DailySummary" in result
        assert "2024-07-01 ~ 2024-07-07" in result
        assert "8" in result
        assert "本周更新8集" in result

    def test_dynamic_content_for_watching_summary_dad(self):
        """Dynamic HTML content should work for watching_summary_dad variant."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "job_name": "DadReport",
            "date_range": "2024-07-01 ~ 2024-07-07",
            "record_count": 5,
            "summary_text": "爸爸的追番总结",
        }

        result = notifier._build_email_dynamic_content("watching_summary_dad", data)

        assert "DadReport" in result
        assert "爸爸的追番总结" in result


class TestWatchingSummaryRegression:
    """Regression tests to ensure existing notification types are unaffected."""

    def test_mark_success_payload_unchanged(self):
        """mark_success payload should still work correctly."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "title": "测试番剧",
            "user_name": "test",
            "season": 1,
            "episode": 5,
            "source": "emby",
            "subject_id": "123",
            "episode_id": "456",
        }

        payload = notifier._build_payload_by_type("mark_success", data, "")

        assert payload["title"] == "✅ 同步成功"
        assert payload["anime"] == "测试番剧"

    def test_mark_failed_payload_unchanged(self):
        """mark_failed payload should still work correctly."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {
            "title": "测试番剧",
            "error_message": "API错误",
            "error_type": "connection",
        }

        payload = notifier._build_payload_by_type("mark_failed", data, "")

        assert payload["title"] == "❌ 同步失败"
        assert payload["error"] == "API错误"

    def test_request_received_payload_unchanged(self):
        """request_received payload should still work correctly."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "测试番剧", "user_name": "test", "source": "emby"}

        payload = notifier._build_payload_by_type("request_received", data, "")

        assert payload["title"] == "📥 收到同步请求"
        assert payload["anime"] == "测试番剧"

    def test_mark_success_subject_unchanged(self):
        """mark_success email subject should still work correctly."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "测试番剧", "season": 1, "episode": 5}

        result = notifier._build_email_subject_by_type("mark_success", data)

        assert "同步成功" in result
        assert "测试番剧" in result

    def test_mark_failed_dynamic_content_unchanged(self):
        """mark_failed dynamic content should still work correctly."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"error_message": "测试错误"}

        result = notifier._build_email_dynamic_content("mark_failed", data)

        assert "错误详情" in result
        assert "测试错误" in result

    def test_unknown_type_payload_still_works(self):
        """Unknown types should still get the fallback payload (not crash on substring check)."""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "anything"}

        payload = notifier._build_payload_by_type("some_new_future_type", data, "")

        assert payload["type"] == "some_new_future_type"
        assert "data" in payload
