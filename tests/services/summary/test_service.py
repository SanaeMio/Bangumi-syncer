"""Test SummaryService: generate_summary and execute_job (Task 3.2)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm.models import ChatResponse, Usage
from app.services.summary.models import SummaryJobConfig

# ── helpers ────────────────────────────────────────────────────────────


def _make_config(**overrides) -> SummaryJobConfig:
    """Build a minimal SummaryJobConfig with default test values."""
    defaults = {
        "id": 1,
        "enabled": True,
        "name": "test_job",
        "cron": "0 21 * * *",
        "lookback_days": 1,
        "user_name": "",
        "system_prompt": "You are a helpful assistant.",
        "max_records": 200,
    }
    defaults.update(overrides)
    return SummaryJobConfig(**defaults)


def _sample_records() -> list[dict]:
    """Return two sample watching records for tests."""
    return [
        {
            "user_name": "dad",
            "title": "葬送的芙莉莲",
            "season": 1,
            "episode": 10,
            "source": "bangumi",
            "status": "success",
            "bgm_title": "葬送的芙莉莲",
            "timestamp": "2026-07-14 20:30:00",
            "media_type": "episode",
        },
        {
            "user_name": "dad",
            "title": "鬼灭之刃",
            "season": 3,
            "episode": 5,
            "source": "bangumi",
            "status": "success",
            "bgm_title": "",
            "timestamp": "2026-07-14 21:00:00",
            "media_type": "movie",
        },
    ]


def _mock_chat_response(
    content: str = "Test summary",
    model: str = "test-model",
    usage: Usage | None = None,
) -> ChatResponse:
    if usage is None:
        usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    return ChatResponse(content=content, model=model, usage=usage)


# ── generate_summary ────────────────────────────────────────────────────


class TestGenerateSummary:
    """Tests for SummaryService.generate_summary()."""

    @pytest.mark.asyncio
    async def test_date_calculation(self):
        """lookback_days=1 produces date_from=yesterday, date_to=today."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(lookback_days=1)
        mock_records = _sample_records()

        # Patch the LLM client so chat() returns a mock response.
        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=_mock_chat_response())

        with (
            patch("app.services.summary.service.database_manager") as mock_db,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
        ):
            mock_db.get_records_in_date_range.return_value = mock_records

            result = await svc.generate_summary(config)

        # Verify dates
        now = datetime.now()
        expected_date_to = now.strftime("%Y-%m-%d")
        expected_date_from = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        assert result["date_from"] == expected_date_from
        assert result["date_to"] == expected_date_to

    @pytest.mark.asyncio
    async def test_user_name_filter_passed_to_db(self):
        """When user_name is set, it is forwarded to DB query."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(user_name="dad")

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=_mock_chat_response())

        with (
            patch("app.services.summary.service.database_manager") as mock_db,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
        ):
            mock_db.get_records_in_date_range.return_value = _sample_records()

            await svc.generate_summary(config)

        # Verify DB call args
        call_kwargs = mock_db.get_records_in_date_range.call_args.kwargs
        assert call_kwargs["user_name"] == "dad"
        assert "date_from" in call_kwargs
        assert "date_to" in call_kwargs

    @pytest.mark.asyncio
    async def test_user_name_none_when_empty(self):
        """Empty user_name is passed as None to DB query."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(user_name="")

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=_mock_chat_response())

        with (
            patch("app.services.summary.service.database_manager") as mock_db,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
        ):
            mock_db.get_records_in_date_range.return_value = []

            await svc.generate_summary(config)

        call_kwargs = mock_db.get_records_in_date_range.call_args.kwargs
        assert call_kwargs["user_name"] is None

    @pytest.mark.asyncio
    async def test_system_prompt_in_messages(self):
        """LLM is called with system_prompt as messages[0].content (role='system')."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(system_prompt="Custom system instruction.")

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=_mock_chat_response())

        with (
            patch("app.services.summary.service.database_manager") as mock_db,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
        ):
            mock_db.get_records_in_date_range.return_value = _sample_records()

            await svc.generate_summary(config)

        args, _ = mock_llm_client.chat.call_args
        messages = args[0]
        assert len(messages) >= 2
        assert messages[0].role == "system"
        assert messages[0].content == "Custom system instruction."

    @pytest.mark.asyncio
    async def test_default_system_prompt_when_empty(self):
        """When system_prompt is empty/whitespace, the class default is used."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(system_prompt="   ")

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=_mock_chat_response())

        with (
            patch("app.services.summary.service.database_manager") as mock_db,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
        ):
            mock_db.get_records_in_date_range.return_value = _sample_records()

            await svc.generate_summary(config)

        args, _ = mock_llm_client.chat.call_args
        messages = args[0]
        # Should use the class default, not the whitespace string
        assert messages[0].content == SummaryJobConfig.system_prompt

    @pytest.mark.asyncio
    async def test_user_prompt_template_rendered(self):
        """User message contains date range, record count, and record text."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(lookback_days=7)

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=_mock_chat_response())

        with (
            patch("app.services.summary.service.database_manager") as mock_db,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
        ):
            mock_db.get_records_in_date_range.return_value = _sample_records()

            await svc.generate_summary(config)

        args, _ = mock_llm_client.chat.call_args
        messages = args[0]
        user_content = messages[1].content  # role="user"

        assert messages[1].role == "user"
        assert "葬送的芙莉莲" in user_content
        assert "共 2 条" in user_content  # record_count=2

    @pytest.mark.asyncio
    async def test_records_formatting(self):
        """Records are formatted into a compact text table."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        formatted = svc._format_records(_sample_records())
        lines = formatted.split("\n")

        # First record: episode type
        assert "葬送的芙莉莲" in lines[0]
        assert "S1E10" in lines[0]
        assert "dad" in lines[0]

        # Second record: movie type → 剧场版
        assert "鬼灭之刃" in lines[1]
        assert "剧场版" in lines[1]

    @pytest.mark.asyncio
    async def test_empty_records_formatting(self):
        """Empty record list produces '（无记录）'."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        formatted = svc._format_records([])
        assert formatted == "（无记录）"

    @pytest.mark.asyncio
    async def test_empty_records_in_generate_summary(self):
        """generate_summary works with empty records and returns record_count=0."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config()

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=_mock_chat_response())

        with (
            patch("app.services.summary.service.database_manager") as mock_db,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
        ):
            mock_db.get_records_in_date_range.return_value = []

            result = await svc.generate_summary(config)

        assert result["record_count"] == 0
        # Verify "（无记录）" appears in the user prompt
        args, _ = mock_llm_client.chat.call_args
        user_content = args[0][1].content
        assert "（无记录）" in user_content

    @pytest.mark.asyncio
    async def test_returns_llm_response_fields(self):
        """Returned dict includes summary_text, model, usage, record_count, dates."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config()
        expected_usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        expected_response = ChatResponse(
            content="summary here", model="gpt-4", usage=expected_usage
        )

        mock_llm_client = MagicMock()
        mock_llm_client.chat = AsyncMock(return_value=expected_response)

        with (
            patch("app.services.summary.service.database_manager") as mock_db,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
        ):
            mock_db.get_records_in_date_range.return_value = _sample_records()

            result = await svc.generate_summary(config)

        assert result["summary_text"] == "summary here"
        assert result["model"] == "gpt-4"
        assert result["usage"] is expected_usage
        assert result["record_count"] == 2
        assert result["date_from"] is not None
        assert result["date_to"] is not None


# ── execute_job ─────────────────────────────────────────────────────────


class TestExecuteJob:
    """Tests for SummaryService.execute_job()."""

    @pytest.mark.asyncio
    async def test_notification_type_empty_user(self):
        """When user_name is empty, notification_type is 'watching_summary'."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(user_name="")

        mock_notifier = MagicMock()
        mock_notifier.send_notification_by_type = MagicMock()

        with (
            patch.object(svc, "generate_summary") as mock_gen,
            patch(
                "app.services.summary.service.get_notifier", return_value=mock_notifier
            ),
        ):
            mock_gen.return_value = {
                "summary_text": "test",
                "model": "gpt-4",
                "usage": Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                "record_count": 5,
                "date_from": "2026-07-14",
                "date_to": "2026-07-15",
            }

            await svc.execute_job(config)

        mock_notifier.send_notification_by_type.assert_called_once()
        call_args = mock_notifier.send_notification_by_type.call_args
        notif_type = call_args[0][0]
        assert notif_type == "watching_summary"

    @pytest.mark.asyncio
    async def test_notification_type_with_user(self):
        """When user_name='dad', notification_type is 'watching_summary_dad'."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(user_name="dad")

        mock_notifier = MagicMock()
        mock_notifier.send_notification_by_type = MagicMock()

        with (
            patch.object(svc, "generate_summary") as mock_gen,
            patch(
                "app.services.summary.service.get_notifier", return_value=mock_notifier
            ),
        ):
            mock_gen.return_value = {
                "summary_text": "test",
                "model": "gpt-4",
                "usage": Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                "record_count": 5,
                "date_from": "2026-07-14",
                "date_to": "2026-07-15",
            }

            await svc.execute_job(config)

        mock_notifier.send_notification_by_type.assert_called_once()
        call_args = mock_notifier.send_notification_by_type.call_args
        notif_type = call_args[0][0]
        assert notif_type == "watching_summary_dad"

    @pytest.mark.asyncio
    async def test_data_dict_has_required_fields(self):
        """Data dict passed to notifier has all expected keys and values."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(id=42, name="my_job", user_name="dad", lookback_days=3)

        mock_notifier = MagicMock()
        mock_notifier.send_notification_by_type = MagicMock()

        with (
            patch.object(svc, "generate_summary") as mock_gen,
            patch(
                "app.services.summary.service.get_notifier", return_value=mock_notifier
            ),
        ):
            mock_gen.return_value = {
                "summary_text": "AI generated summary",
                "model": "claude-3",
                "usage": Usage(
                    prompt_tokens=200, completion_tokens=100, total_tokens=300
                ),
                "record_count": 10,
                "date_from": "2026-07-12",
                "date_to": "2026-07-15",
            }

            await svc.execute_job(config)

        data = mock_notifier.send_notification_by_type.call_args[0][1]

        assert "timestamp" in data
        assert data["job_name"] == "my_job"
        assert data["job_id"] == 42
        assert data["user_name"] == "dad"
        assert data["summary_text"] == "AI generated summary"
        assert data["date_range"] == "2026-07-12 ~ 2026-07-15"
        assert data["record_count"] == 10
        assert data["lookback_days"] == 3
        assert data["model"] == "claude-3"
        assert data["tokens_used"] == 300

    @pytest.mark.asyncio
    async def test_notifier_called_once(self):
        """Notifier is invoked exactly once per execute_job call."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config()

        mock_notifier = MagicMock()
        mock_notifier.send_notification_by_type = MagicMock()

        with (
            patch.object(svc, "generate_summary") as mock_gen,
            patch(
                "app.services.summary.service.get_notifier", return_value=mock_notifier
            ),
        ):
            mock_gen.return_value = {
                "summary_text": "test",
                "model": "gpt-4",
                "usage": Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                "record_count": 5,
                "date_from": "2026-07-14",
                "date_to": "2026-07-15",
            }

            await svc.execute_job(config)

        assert mock_notifier.send_notification_by_type.call_count == 1

    @pytest.mark.asyncio
    async def test_exception_in_generate_summary_is_caught(self):
        """When generate_summary raises, error is logged but not re-raised."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(name="failing_job", id=99)

        with (
            patch.object(svc, "generate_summary") as mock_gen,
            patch("app.services.summary.service.get_notifier") as mock_get_notifier,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            mock_gen.side_effect = RuntimeError("LLM down")

            # Should not raise
            await svc.execute_job(config)

        # Notifier should NOT be called
        mock_get_notifier.return_value.send_notification_by_type.assert_not_called()

        # Logger should record the error
        mock_logger.error.assert_called_once()
        error_msg = mock_logger.error.call_args[0][0]
        assert "failing_job" in error_msg
        assert "99" in error_msg
        assert "LLM down" in error_msg

    @pytest.mark.asyncio
    async def test_tokens_used_zero_when_usage_is_none(self):
        """When LLM returns usage=None, tokens_used defaults to 0."""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config()

        mock_notifier = MagicMock()
        mock_notifier.send_notification_by_type = MagicMock()

        with (
            patch.object(svc, "generate_summary") as mock_gen,
            patch(
                "app.services.summary.service.get_notifier", return_value=mock_notifier
            ),
        ):
            mock_gen.return_value = {
                "summary_text": "test",
                "model": "gpt-4",
                "usage": None,
                "record_count": 0,
                "date_from": "2026-07-14",
                "date_to": "2026-07-15",
            }

            await svc.execute_job(config)

        data = mock_notifier.send_notification_by_type.call_args[0][1]
        assert data["tokens_used"] == 0
