"""测试 SummaryService：generate_summary 和 execute_job（任务 3.2）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm.models import ChatResponse, Usage
from app.services.summary.models import SummaryJobConfig

# ── helpers ────────────────────────────────────────────────────────────


def _make_config(**overrides) -> SummaryJobConfig:
    """使用默认测试值构建最小 SummaryJobConfig。"""
    defaults = {
        "name": "test_job",
        "enabled": True,
        "cron": "0 21 * * *",
        "lookback_days": 1,
        "user_name": "",
        "system_prompt": "You are a helpful assistant.",
        "max_records": 200,
    }
    defaults.update(overrides)
    return SummaryJobConfig(**defaults)


def _sample_records() -> list[dict]:
    """返回两条示例观影记录供测试使用。"""
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
    """SummaryService.generate_summary() 测试。"""

    @pytest.mark.asyncio
    async def test_date_calculation(self):
        """lookback_days=1 应产生 date_from=昨天, date_to=今天。"""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(lookback_days=1)
        mock_records = _sample_records()

        # Patch LLM 客户端，使 chat() 返回 mock 响应。
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

        # 验证日期
        now = datetime.now()
        expected_date_to = now.strftime("%Y-%m-%d")
        expected_date_from = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        assert result["date_from"] == expected_date_from
        assert result["date_to"] == expected_date_to

    @pytest.mark.asyncio
    async def test_user_name_filter_passed_to_db(self):
        """设置 user_name 时，应将其转发给数据库查询。"""
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

        # 验证数据库调用参数
        call_kwargs = mock_db.get_records_in_date_range.call_args.kwargs
        assert call_kwargs["user_name"] == "dad"
        assert "date_from" in call_kwargs
        assert "date_to" in call_kwargs

    @pytest.mark.asyncio
    async def test_user_name_none_when_empty(self):
        """空 user_name 以 None 传给数据库查询。"""
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
        """LLM 被调用时 messages[0].content 为 system_prompt（role='system'）。"""
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
        """当 system_prompt 为空/空白时，使用类的默认值。"""
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
        # 应使用类的默认值，而非空白字符串
        assert messages[0].content == SummaryJobConfig.system_prompt

    @pytest.mark.asyncio
    async def test_user_prompt_template_rendered(self):
        """用户消息包含日期范围、记录数和记录文本。"""
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
        """记录被格式化为紧凑的文本表格。"""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        formatted = svc._format_records(_sample_records())
        lines = formatted.split("\n")

        # 第一条记录：剧集类型
        assert "葬送的芙莉莲" in lines[0]
        assert "S1E10" in lines[0]
        assert "dad" in lines[0]

        # 第二条记录：电影类型 → 剧场版
        assert "鬼灭之刃" in lines[1]
        assert "剧场版" in lines[1]

    @pytest.mark.asyncio
    async def test_empty_records_formatting(self):
        """空记录列表产生'（无记录）'。"""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        formatted = svc._format_records([])
        assert formatted == "（无记录）"

    @pytest.mark.asyncio
    async def test_empty_records_in_generate_summary(self):
        """空记录时 generate_summary 正常工作，返回 record_count=0。"""
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
        # 验证用户提示中包含"（无记录）"
        args, _ = mock_llm_client.chat.call_args
        user_content = args[0][1].content
        assert "（无记录）" in user_content

    @pytest.mark.asyncio
    async def test_returns_llm_response_fields(self):
        """返回的字典包含 summary_text、model、usage、record_count、dates。"""
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
    """SummaryService.execute_job() 测试。"""

    @pytest.mark.asyncio
    async def test_notification_type_empty_user(self):
        """notification_type 使用任务名称，而非 user_name。"""
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
        assert notif_type == "watching_summary_test_job"

    @pytest.mark.asyncio
    async def test_notification_type_with_user(self):
        """notification_type 使用任务 ID，而非 user_name。"""
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
        assert notif_type == "watching_summary_test_job"

    @pytest.mark.asyncio
    async def test_data_dict_has_required_fields(self):
        """传递给 notifier 的数据字典包含所有预期的键和值。"""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(name="my_job", user_name="dad", lookback_days=3)

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
        assert data["user_name"] == "dad"
        assert data["summary_text"] == "AI generated summary"
        assert data["date_range"] == "2026-07-12 ~ 2026-07-15"
        assert data["record_count"] == 10
        assert data["lookback_days"] == 3
        assert data["model"] == "claude-3"
        assert data["tokens_used"] == 300

    @pytest.mark.asyncio
    async def test_notifier_called_once(self):
        """每次 execute_job 调用恰好触发一次 Notifier。"""
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
        """generate_summary 抛出异常时，发送失败通知并记录错误日志。"""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(name="failing_job")

        with (
            patch.object(svc, "generate_summary") as mock_gen,
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            mock_gen.side_effect = RuntimeError("LLM down")

            # 不应抛出异常
            await svc.execute_job(config)

        # P4.7：应通过 notification_service.notify 发送失败通知
        mock_ns.notify.assert_called_once()
        call_args = mock_ns.notify.call_args
        assert call_args.args[0] == "watching_summary_failing_job"
        kwargs = call_args.kwargs
        assert kwargs["in_app_type"] == "summary_job_failed"
        assert "LLM down" in kwargs["summary_text"]
        assert "执行异常" in kwargs["in_app_body"]

        # 日志应记录该错误
        error_calls = [
            c[0][0]
            for c in mock_logger.error.call_args_list
            if isinstance(c[0], tuple) and c[0]
        ]
        assert any("failing_job" in msg for msg in error_calls)
        assert any("LLM down" in msg for msg in error_calls)

    @pytest.mark.asyncio
    async def test_empty_llm_content_sends_llm_failed_notification(self):
        """LLM 返回空内容时，发送 summary_llm_failed 通知并写入收件箱。"""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(name="empty_llm_job")

        with (
            patch.object(svc, "generate_summary") as mock_gen,
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            mock_gen.return_value = {
                "summary_text": "",
                "model": "",
                "usage": None,
                "record_count": 0,
                "date_from": "2026-07-14",
                "date_to": "2026-07-15",
            }

            await svc.execute_job(config)

        # P4.7：应通过 notification_service.notify 发送失败通知（而非成功通知）
        mock_ns.notify.assert_called_once()
        call_args = mock_ns.notify.call_args
        assert call_args.args[0] == "watching_summary_empty_llm_job"
        kwargs = call_args.kwargs
        assert kwargs["in_app_type"] == "summary_llm_failed"
        assert "empty_llm_job" in kwargs["in_app_title"]
        assert "LLM 返回空内容" in kwargs["summary_text"]
        assert kwargs["model"] == ""

        # 应记录错误日志
        error_calls = [
            c[0][0]
            for c in mock_logger.error.call_args_list
            if isinstance(c[0], tuple) and c[0]
        ]
        assert any("LLM 返回空内容" in msg for msg in error_calls)

    @pytest.mark.asyncio
    async def test_tokens_used_zero_when_usage_is_none(self):
        """当 LLM 返回 usage=None 时，tokens_used 默认为 0。"""
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
