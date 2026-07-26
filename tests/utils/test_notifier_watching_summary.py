"""
Notifier 中 watching_summary 通知类型测试。
"""

from unittest.mock import MagicMock

from app.utils.notifier import Notifier


class TestWatchingSummaryPayload:
    """_build_payload_by_type watching_summary 类型测试。"""

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
        """watching_summary 的 payload 应包含所有预期字段。"""
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
        """watching_summary_dad 的 payload 应包含所有预期字段。"""
        notifier = self._make_notifier()
        data = self._make_watching_data()
        data["job_name"] = "DadSummary"

        payload = notifier._build_payload_by_type("watching_summary_dad", data, "")

        assert payload["title"] == "📊 追番总结 - DadSummary"
        assert payload["type"] == "watching_summary"
        assert payload["summary"] == "本周共追番5部，更新12集"

    def test_payload_handles_missing_fields_gracefully(self):
        """数据字段缺失时应使用默认值。"""
        notifier = self._make_notifier()
        data: dict = {}

        payload = notifier._build_payload_by_type("watching_summary", data, "")

        assert payload["title"] == "📊 追番总结 - "
        assert payload["summary"] == ""
        assert payload["date_range"] == ""
        assert payload["record_count"] == 0
        assert payload["user_name"] == ""


class TestWatchingSummaryEmailSubject:
    """_build_email_subject_by_type watching_summary 类型测试。"""

    def test_subject_for_watching_summary(self):
        """邮件主题应包含 job_name 和正确的前缀。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"job_name": "WeeklyReport"}

        result = notifier._build_email_subject_by_type("watching_summary", data)

        assert "追番总结" in result
        assert "WeeklyReport" in result
        assert result.startswith("[Bangumi-Syncer]")

    def test_subject_for_watching_summary_default_name(self):
        """即使 job_name 缺失，邮件主题也应正常工作。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)

        result = notifier._build_email_subject_by_type("watching_summary", {})

        assert "追番总结" in result


class TestWatchingSummaryDynamicContent:
    """_build_email_dynamic_content watching_summary 类型测试。"""

    def test_dynamic_content_for_watching_summary_contains_key_info(self):
        """动态 HTML 内容应包含 job_name、date_range、record_count、summary_text。"""
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
        """动态 HTML 内容应支持 watching_summary_dad 变体。"""
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
    """回归测试，确保现有通知类型不受影响。"""

    def test_mark_success_payload_unchanged(self):
        """mark_success payload 仍应正常工作。"""
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
        """mark_failed payload 仍应正常工作。"""
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
        """request_received payload 仍应正常工作。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "测试番剧", "user_name": "test", "source": "emby"}

        payload = notifier._build_payload_by_type("request_received", data, "")

        assert payload["title"] == "📥 收到同步请求"
        assert payload["anime"] == "测试番剧"

    def test_mark_success_subject_unchanged(self):
        """mark_success 邮件主题仍应正常工作。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "测试番剧", "season": 1, "episode": 5}

        result = notifier._build_email_subject_by_type("mark_success", data)

        assert "同步成功" in result
        assert "测试番剧" in result

    def test_mark_failed_dynamic_content_unchanged(self):
        """mark_failed 动态内容仍应正常工作。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"error_message": "测试错误"}

        result = notifier._build_email_dynamic_content("mark_failed", data)

        assert "错误详情" in result
        assert "测试错误" in result

    def test_unknown_type_payload_still_works(self):
        """未知通知类型仍应获得回退 payload（不会因子串检查而崩溃）。"""
        mock_config = MagicMock()
        notifier = Notifier(mock_config)
        data = {"title": "anything"}

        payload = notifier._build_payload_by_type("some_new_future_type", data, "")

        assert payload["type"] == "some_new_future_type"
        assert "data" in payload
