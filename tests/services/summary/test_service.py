"""测试 SummaryService：generate_summary 和 execute_job（任务 3.2）。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.memory import MemoryEntry
from app.services.llm.models import ChatResponse, Usage
from app.services.memory.service import MemoryService
from app.services.summary.models import SummaryJobConfig, SummaryRecord
from app.services.summary.service import SummaryService, _utc_to_local_date

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
    """返回两条示例观影记录供测试使用（对齐 get_records_in_date_range 输出键）。"""
    return [
        {
            "id": 1,
            "user_name": "dad",
            "title": "葬送的芙莉莲",
            "season": 1,
            "episode": 10,
            "source": "bangumi",
            "status": "success",
            "bgm_title": "葬送的芙莉莲",
            "timestamp": "2026-07-14 20:30:00",
            "media_type": "episode",
            "consumed_run_ids": set(),
        },
        {
            "id": 2,
            "user_name": "dad",
            "title": "鬼灭之刃",
            "season": 3,
            "episode": 5,
            "source": "bangumi",
            "status": "success",
            "bgm_title": "",
            "timestamp": "2026-07-14 21:00:00",
            "media_type": "movie",
            "consumed_run_ids": set(),
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


def _summary_record(**overrides) -> SummaryRecord:
    defaults = {
        "id": 1,
        "timestamp": "2026-07-14 20:30:00",
        "user_name": "dad",
        "title": "葬送的芙莉莲",
        "bgm_title": "葬送的芙莉莲",
        "season": 1,
        "episode": 10,
        "media_type": "episode",
        "source": "bangumi",
        "status": "success",
        "consumed_run_ids": set(),
    }
    defaults.update(overrides)
    return SummaryRecord(**defaults)


def _records() -> list[SummaryRecord]:
    """两条 SummaryRecord 记录（execute_job 测试用）。"""
    return [
        _summary_record(id=1),
        _summary_record(
            id=2,
            title="鬼灭之刃",
            bgm_title="",
            season=3,
            episode=5,
            media_type="movie",
        ),
    ]


def _temp_db(temp_dir, name="svc.db"):
    """创建临时 DatabaseManager（memory 测试用）。"""
    with patch("app.core.database.logger"):
        from app.core.database import DatabaseManager

        return DatabaseManager(str(temp_dir / name))


# ── generate_summary ────────────────────────────────────────────────────


class TestGenerateSummary:
    """SummaryService.generate_summary() 测试。"""

    @pytest.mark.asyncio
    async def test_date_calculation(self):
        """lookback_days=1 应产生 date_from=昨天, date_to=今天。"""
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
    async def test_include_consumed_true_when_memory_on(self):
        """P1：memory_limit>0 时传 include_consumed=True（消费排除所需）。"""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(user_name="dad", memory_limit=5)

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
        assert call_kwargs["include_consumed"] is True

    @pytest.mark.asyncio
    async def test_include_consumed_false_when_memory_off(self):
        """P1：memory_limit=0（默认）时传 include_consumed=False（轻量查询）。"""
        from app.services.summary.service import SummaryService

        svc = SummaryService()
        config = _make_config(user_name="dad")  # memory_limit 默认 0

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
        assert call_kwargs["include_consumed"] is False

    @pytest.mark.asyncio
    async def test_system_prompt_in_messages(self):
        """LLM 被调用时 messages[0].content 为 system_prompt（role='system'）。"""
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

    def test_records_formatting(self):
        """记录被格式化为紧凑的文本表格。"""
        svc = SummaryService()
        records = [
            _summary_record(
                timestamp="2026-07-14 20:30:00",
                title="葬送的芙莉莲",
                bgm_title="葬送的芙莉莲",
                season=1,
                episode=10,
            ),
            _summary_record(
                id=2,
                timestamp="2026-07-14 21:00:00",
                title="鬼灭之刃",
                bgm_title="",
                season=3,
                episode=5,
                media_type="movie",
            ),
        ]
        formatted = svc._format_records(records)
        lines = formatted.split("\n")

        # 第一条记录：剧集类型
        assert "葬送的芙莉莲" in lines[0]
        assert "S1E10" in lines[0]
        assert "dad" in lines[0]

        # 第二条记录：电影类型 → 剧场版
        assert "鬼灭之刃" in lines[1]
        assert "剧场版" in lines[1]

    def test_empty_records_formatting(self):
        """空记录列表产生'（无记录）'。"""
        svc = SummaryService()
        formatted = svc._format_records([])
        assert formatted == "（无记录）"

    @pytest.mark.asyncio
    async def test_empty_records_in_generate_summary(self):
        """空记录时 generate_summary 正常工作，返回 record_count=0。"""
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
    """SummaryService.execute_job() 测试（2.0.2 重构后：_query_records → chat → 通知）。"""

    @staticmethod
    def _make_svc() -> SummaryService:
        return SummaryService()

    def _patch_llm(self, response: ChatResponse):
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(return_value=response)
        return patch(
            "app.services.summary.service.get_llm_client",
            return_value=mock_client,
        ), mock_client

    @pytest.mark.asyncio
    async def test_notification_type_empty_user(self):
        """notification_type 使用任务名称，而非 user_name。"""
        svc = self._make_svc()
        config = _make_config(user_name="")

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
        ):
            await svc.execute_job(config)

        mock_ns.notify.assert_called_once()
        call_args = mock_ns.notify.call_args
        assert call_args.args[0] == "watching_summary_test_job"

    @pytest.mark.asyncio
    async def test_notification_type_with_user(self):
        """notification_type 使用任务 ID，而非 user_name。"""
        svc = self._make_svc()
        config = _make_config(user_name="dad")

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
        ):
            await svc.execute_job(config)

        mock_ns.notify.assert_called_once()
        call_args = mock_ns.notify.call_args
        assert call_args.args[0] == "watching_summary_test_job"

    @pytest.mark.asyncio
    async def test_data_dict_has_required_fields(self):
        """传递给 notifier 的数据字典包含所有预期的键和值。"""
        svc = self._make_svc()
        config = _make_config(name="my_job", user_name="dad", lookback_days=3)
        usage = Usage(prompt_tokens=200, completion_tokens=100, total_tokens=300)
        response = _mock_chat_response(
            content="AI generated summary", model="claude-3", usage=usage
        )

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-12", "2026-07-15"),
            ),
            self._patch_llm(response)[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
        ):
            await svc.execute_job(config)

        data = mock_ns.notify.call_args.kwargs

        assert data["job_name"] == "my_job"
        assert data["user_name"] == "dad"
        assert data["summary_text"] == "AI generated summary"
        assert data["date_range"] == "2026-07-12 ~ 2026-07-15"
        assert data["record_count"] == 2
        assert data["lookback_days"] == 3
        assert data["model"] == "claude-3"
        assert data["tokens_used"] == 300

    @pytest.mark.asyncio
    async def test_notifier_called_once(self):
        """每次 execute_job 调用恰好触发一次 Notifier。"""
        svc = self._make_svc()
        config = _make_config()

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
        ):
            await svc.execute_job(config)

        assert mock_ns.notify.call_count == 1

    @pytest.mark.asyncio
    async def test_exception_in_query_is_caught(self):
        """_query_records 抛出异常时，发送失败通知并记录错误日志。"""
        svc = self._make_svc()
        config = _make_config(name="failing_job")

        with (
            patch.object(svc, "_query_records", side_effect=RuntimeError("LLM down")),
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            await svc.execute_job(config)

        mock_ns.notify.assert_called_once()
        call_args = mock_ns.notify.call_args
        assert call_args.args[0] == "watching_summary_failing_job"
        kwargs = call_args.kwargs
        assert kwargs["in_app_type"] == "summary_job_failed"
        assert "LLM down" in kwargs["summary_text"]
        assert "执行异常" in kwargs["in_app_body"]

        # 日志应记录该错误
        error_msgs = [c[0][0] for c in mock_logger.error.call_args_list if c[0]]
        assert any("failing_job" in m and "LLM down" in m for m in error_msgs)

    @pytest.mark.asyncio
    async def test_chat_exception_sends_llm_failed_notification(self):
        """chat 异常：按统一策略也要通知，且文案标注入阶段（summary_llm_failed）。"""
        svc = self._make_svc()
        config = _make_config(name="chat_fail_job")
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(side_effect=RuntimeError("API down"))

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_client,
            ),
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            await svc.execute_job(config)

        # 统一策略：出错就通知，但类型/文案按阶段区分（chat → summary_llm_failed）
        mock_ns.notify.assert_called_once()
        call_args = mock_ns.notify.call_args
        assert call_args.args[0] == "watching_summary_chat_fail_job"
        kwargs = call_args.kwargs
        assert kwargs["in_app_type"] == "summary_llm_failed"
        assert "chat_fail_job" in kwargs["in_app_title"]
        assert "LLM 调用阶段" in kwargs["summary_text"]
        assert "API down" in kwargs["summary_text"]
        assert "LLM 调用失败" in kwargs["in_app_body"]

        # 日志记录失败阶段，便于排查
        error_msgs = [c[0][0] for c in mock_logger.error.call_args_list if c[0]]
        assert any("chat_fail_job" in m and "stage=chat" in m for m in error_msgs)

    @pytest.mark.asyncio
    async def test_store_failure_sends_failed_notification(self, temp_dir):
        """记忆写入失败：统一策略下也通知（summary_job_failed），文案标注记忆写入阶段。"""
        svc, _ = self._svc_with_real_memory(temp_dir, job_name="store_fail_job")
        config = _make_config(name="store_fail_job", memory_limit=5)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            svc.memory.extract_and_store = AsyncMock(
                side_effect=RuntimeError("db locked")
            )
            await svc.execute_job(config)

        mock_ns.notify.assert_called_once()
        call_args = mock_ns.notify.call_args
        assert call_args.args[0] == "watching_summary_store_fail_job"
        kwargs = call_args.kwargs
        assert kwargs["in_app_type"] == "summary_job_failed"
        assert "记忆写入阶段" in kwargs["summary_text"]
        assert "db locked" in kwargs["summary_text"]
        assert "记忆写入失败" in kwargs["in_app_body"]

        error_msgs = [c[0][0] for c in mock_logger.error.call_args_list if c[0]]
        assert any("store_fail_job" in m and "stage=store" in m for m in error_msgs)

    @pytest.mark.asyncio
    async def test_notification_failure_no_second_notification(self):
        """spec 失败语义：通知失败 → 已写消费标记，不二次通知（只尝试一次）。"""
        svc = self._make_svc()
        config = _make_config(name="notify_fail_job")

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            mock_ns.notify.side_effect = RuntimeError("notify down")
            await svc.execute_job(config)

        # 只尝试一次通知（失败后不落入通用 summary_job_failed 二次通知）
        mock_ns.notify.assert_called_once()
        error_msgs = [c[0][0] for c in mock_logger.error.call_args_list if c[0]]
        assert any("notify_fail_job" in m and "notify down" in m for m in error_msgs)

    @pytest.mark.asyncio
    async def test_empty_llm_content_sends_llm_failed_notification(self):
        """LLM 返回空内容时，发送 summary_llm_failed 通知并写入收件箱。"""
        svc = self._make_svc()
        config = _make_config(name="empty_llm_job")
        empty = ChatResponse(content="", model="", usage=None, latency=5)

        with (
            patch.object(
                svc, "_query_records", return_value=([], "2026-07-14", "2026-07-15")
            ),
            TestExecuteJob._patch_llm(svc, empty)[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            await svc.execute_job(config)

        mock_ns.notify.assert_called_once()
        call_args = mock_ns.notify.call_args
        assert call_args.args[0] == "watching_summary_empty_llm_job"
        kwargs = call_args.kwargs
        assert kwargs["in_app_type"] == "summary_llm_failed"
        assert "empty_llm_job" in kwargs["in_app_title"]
        assert "LLM 返回空内容" in kwargs["summary_text"]
        assert kwargs["model"] == ""

        # 应记录错误日志
        error_msgs = [c[0][0] for c in mock_logger.error.call_args_list if c[0]]
        assert any("empty_llm_job" in m and "LLM 返回空内容" in m for m in error_msgs)

    @pytest.mark.asyncio
    async def test_tokens_used_zero_when_usage_is_none(self):
        """当 LLM 返回 usage=None 时，tokens_used 默认为 0。"""
        svc = self._make_svc()
        config = _make_config()
        response = ChatResponse(content="test", model="gpt-4", usage=None)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            self._patch_llm(response)[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
        ):
            await svc.execute_job(config)

        data = mock_ns.notify.call_args.kwargs
        assert data["tokens_used"] == 0

    # ── 记忆注入（Phase 2.0.2）──────────────────────────────────────

    @staticmethod
    def _svc_with_real_memory(temp_dir, job_name="test_job"):
        """构造绑定了临时 DB 记忆 repo 的 SummaryService（真实 MemoryService）。"""
        db = _temp_db(temp_dir)
        svc = SummaryService()
        svc.memory = MemoryService(db.memory)
        # 真实检索链路 + 可断言的 extract（不真正调 LLM 摘要）
        svc.memory.extract_and_store = AsyncMock()
        return svc, db

    @pytest.mark.asyncio
    async def test_memory_disabled_short_circuits(self, temp_dir, reset_singletons):
        """R4：memory_limit=0（默认）→ 不注入不写入。"""
        svc, db = TestExecuteJob._svc_with_real_memory(temp_dir)
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-test_job",
                run_id="run-1",
                summary="昨日看了芙莉莲",
            ),
            [],
        )
        config = _make_config()  # memory_limit=0
        _llm_patch, mock_client = self._patch_llm(_mock_chat_response())

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        messages = mock_client.chat.call_args.args[0]
        assert messages[0].content == "You are a helpful assistant."
        assert "历史执行上下文" not in messages[0].content
        svc.memory.extract_and_store.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_memory_injects_recent_context(self, temp_dir, reset_singletons):
        """R1：memory_limit>0 → system prompt 含历史上下文（最近 2 条摘要）。"""
        svc, db = TestExecuteJob._svc_with_real_memory(temp_dir)
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-test_job",
                run_id="run-1",
                summary="昨日看了芙莉莲",
            ),
            [],
        )
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-test_job",
                run_id="run-2",
                summary="用户反馈：不要太啰嗦",
            ),
            [],
        )
        config = _make_config(memory_limit=5)
        _llm_patch, mock_client = self._patch_llm(_mock_chat_response())

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        messages = mock_client.chat.call_args.args[0]
        assert len([m for m in messages if m.role == "system"]) == 1
        assert "## 历史执行上下文" in messages[0].content
        assert "- 昨日看了芙莉莲" in messages[0].content
        assert "- 用户反馈：不要太啰嗦" in messages[0].content
        # 原文 system prompt 保留在注入内容之后
        assert "You are a helpful assistant." in messages[0].content

    @pytest.mark.asyncio
    async def test_recent_limit_passed_to_service(self, temp_dir, reset_singletons):
        """R8：memory_limit 透传给 memory.recent；related_limit=0 时不调 related。"""
        svc, _ = self._svc_with_real_memory(temp_dir)
        config = _make_config(memory_limit=3, related_limit=0)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service"),
            patch.object(svc, "memory") as mock_memory,
        ):
            mock_memory.recent.return_value = []
            mock_memory.related.return_value = []
            mock_memory.get_task_run_ids.return_value = set()
            mock_memory.extract_and_store = AsyncMock()
            await svc.execute_job(config)

        mock_memory.related.assert_not_called()

    async def test_extract_failure_does_not_block_notification(
        self, temp_dir, reset_singletons
    ):
        """R7：提取记忆失败（DB 异常）不影响 _dispatch_notification。"""
        svc, _ = self._svc_with_real_memory(temp_dir)
        svc.memory.extract_and_store = AsyncMock(side_effect=RuntimeError("db down"))
        config = _make_config(memory_limit=5)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch("app.services.summary.service.logger") as mock_logger,
        ):
            await svc.execute_job(config)

        mock_ns.notify.assert_called_once()
        assert mock_logger.error.called  # 异常已记录日志

    @pytest.mark.asyncio
    async def test_extract_called_with_full_context_when_enabled(
        self, temp_dir, reset_singletons
    ):
        """记忆开启时：extract_and_store 收到完整上下文与今日记录 id。"""
        svc, _ = self._svc_with_real_memory(temp_dir)
        config = _make_config(memory_limit=5)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            self._patch_llm(
                _mock_chat_response(
                    "summary here",
                    usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                )
            )[0],
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        svc.memory.extract_and_store.assert_awaited_once()
        kwargs = svc.memory.extract_and_store.call_args.kwargs
        assert kwargs["task_type"] == "summary"
        assert kwargs["task_id"] == "summary-test_job"
        assert kwargs["outcome"] == "success"
        assert kwargs["tokens_used"] == 3
        assert kwargs["record_ids"] == [1, 2]
        assert kwargs["response"].content == "summary here"

    # ── 消费排除（memory_limit>0 时已消费记录不进 prompt，信息由摘要承继）──

    @pytest.mark.asyncio
    async def test_no_consumed_no_exclusion(self, temp_dir, reset_singletons):
        """S3a：明细全部未消费 → 记录全部进 user prompt，无排除。"""
        svc, _ = self._svc_with_real_memory(temp_dir)
        config = _make_config(memory_limit=5)
        _llm_patch, mock_client = self._patch_llm(_mock_chat_response())

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        messages = mock_client.chat.call_args.args[0]
        assert "葬送的芙莉莲" in messages[1].content
        assert "鬼灭之刃" in messages[1].content

    @pytest.mark.asyncio
    async def test_consumed_records_excluded_from_prompt(
        self, temp_dir, reset_singletons
    ):
        """S2：本任务已消费记录不进 user prompt（信息由摘要承继）；未消费记录正常。"""
        svc, db = TestExecuteJob._svc_with_real_memory(temp_dir)
        # 先写入本任务的记忆条目（run-own 属于本任务），再标记对应记录已消费
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-test_job",
                run_id="run-own",
                summary="昨日看了番剧A",
            ),
            [],
        )
        records = [
            _summary_record(
                id=1, title="番剧A", bgm_title="番剧A", consumed_run_ids={"run-own"}
            ),
            _summary_record(
                id=2, title="番剧B", bgm_title="番剧B", consumed_run_ids=set()
            ),
        ]
        config = _make_config(memory_limit=5)
        _llm_patch, mock_client = self._patch_llm(_mock_chat_response())

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(records, "2026-07-14", "2026-07-15"),
            ),
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        user_content = mock_client.chat.call_args.args[0][1].content
        assert "番剧A" not in user_content  # 本任务已消费 → 排除
        assert "番剧B" in user_content  # 未消费 → 保留

    @pytest.mark.asyncio
    async def test_consumed_by_other_task_kept_in_prompt(
        self, temp_dir, reset_singletons
    ):
        """S2c（跨任务隔离）：记录被其他任务消费（consumed_run_id 非空但不属于
        本任务）→ 仍保留在 prompt——每日总结消费后年度总结仍可消费。"""
        svc, db = TestExecuteJob._svc_with_real_memory(temp_dir)
        # 其他任务（summary-yearly）消费了 run-other；本任务无该 run
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-yearly",
                run_id="run-other",
                summary="年度总结",
            ),
            [],
        )
        records = [
            _summary_record(
                id=1, title="番剧A", bgm_title="番剧A", consumed_run_ids={"run-other"}
            ),
        ]
        config = _make_config(memory_limit=5)
        _llm_patch, mock_client = self._patch_llm(_mock_chat_response())

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(records, "2026-07-14", "2026-07-15"),
            ),
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        user_content = mock_client.chat.call_args.args[0][1].content
        assert "番剧A" in user_content  # 其他任务消费 → 本任务保留

    @pytest.mark.asyncio
    async def test_all_consumed_results_in_no_records(self, temp_dir, reset_singletons):
        """S2b：窗口内全部被【本任务】消费 → user prompt 记录为空（走"无记录"路径）。"""
        svc, db = TestExecuteJob._svc_with_real_memory(temp_dir)
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-test_job",
                run_id="run-own",
                summary="昨日总结",
            ),
            [],
        )
        records = [
            _summary_record(id=1, consumed_run_ids={"run-own"}),
            _summary_record(id=2, consumed_run_ids={"run-own"}),
        ]
        config = _make_config(memory_limit=5)
        _llm_patch, mock_client = self._patch_llm(_mock_chat_response())

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(records, "2026-07-14", "2026-07-15"),
            ),
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        user_content = mock_client.chat.call_args.args[0][1].content
        assert "（无记录）" in user_content

    @pytest.mark.asyncio
    async def test_placeholder_row_prevents_rerun_of_consumed_records(
        self, temp_dir, reset_singletons
    ):
        """T2 端到端防重跑：第一次执行摘要提取失败 → 写占位行 + 消费标记；
        第二次执行同窗口 → 这批记录被消费排除（不再进 LLM prompt）。"""
        db = _temp_db(temp_dir)
        svc = SummaryService()
        svc.memory = MemoryService(
            db.memory
        )  # 真实 extractor（不 mock extract_and_store）
        config = _make_config(memory_limit=5, lookback_days=7)

        # 真实记录入库（第二次执行走真实 _query_records → 回填消费标记）
        db.log_sync_record(
            user_name="dad",
            title="葬送的芙莉莲",
            ori_title=None,
            season=1,
            episode=10,
            bgm_title="葬送的芙莉莲",
        )
        db.log_sync_record(
            user_name="dad",
            title="鬼灭之刃",
            ori_title=None,
            season=3,
            episode=5,
            bgm_title="",
        )

        # 主总结成功；摘要提取 LLM 失败 → 触发占位行
        failing_summary_client = MagicMock()
        failing_summary_client.chat = AsyncMock(
            side_effect=RuntimeError("summary llm down")
        )
        _llm_patch, _mock_client = self._patch_llm(_mock_chat_response("主总结正文"))

        with (
            patch("app.services.summary.service.database_manager", db),
            _llm_patch,
            patch(
                "app.services.memory.extractor.get_llm_client",
                return_value=failing_summary_client,
            ),
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        # 占位行已写入（summary 空、outcome 标记失败来源）
        entries = db.memory.get_recent("summary", "summary-test_job", limit=10)
        assert len(entries) == 1
        assert entries[0].summary == ""
        assert entries[0].outcome == "summary_failed"
        run_id = entries[0].run_id

        # 消费标记已写入（真实查询回填 consumed_run_ids）
        rows = db.get_records_in_date_range(
            date_from=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
            date_to=datetime.now().strftime("%Y-%m-%d"),
            include_consumed=True,
        )
        assert rows
        assert all(r["consumed_run_ids"] == {run_id} for r in rows)

        # 第二次执行：真实 _query_records（含消费回填）→ 这批记录被排除
        _llm_patch2, mock_client2 = self._patch_llm(_mock_chat_response("第二次总结"))
        with (
            patch("app.services.summary.service.database_manager", db),
            _llm_patch2,
            patch(
                "app.services.memory.extractor.get_llm_client",
                return_value=failing_summary_client,
            ),
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        user_content = mock_client2.chat.call_args.args[0][1].content
        assert "葬送的芙莉莲" not in user_content
        assert "鬼灭之刃" not in user_content
        assert "（无记录）" in user_content

    @pytest.mark.asyncio
    async def test_related_titles_from_today_records(self, temp_dir, reset_singletons):
        """related_limit>0：titles = 今日明细 bgm_title 去重（空值过滤，全量不截前 5）。"""
        svc, _ = self._svc_with_real_memory(temp_dir)
        records = [
            _summary_record(id=1, bgm_title="葬送的芙莉莲"),
            _summary_record(id=2, bgm_title="葬送的芙莉莲"),
            _summary_record(id=3, bgm_title="", title="无bgm标题"),
        ]
        config = _make_config(memory_limit=5, related_limit=3)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(records, "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service"),
            patch.object(svc, "memory") as mock_memory,
        ):
            mock_memory.recent.return_value = []
            mock_memory.related.return_value = []
            mock_memory.get_task_run_ids.return_value = set()
            mock_memory.extract_and_store = AsyncMock()
            await svc.execute_job(config)

        mock_memory.related.assert_called_once_with(
            "summary", "summary-test_job", ["葬送的芙莉莲"], limit=3
        )

    @pytest.mark.asyncio
    async def test_new_records_rendered_normally(self, temp_dir, reset_singletons):
        svc, _ = self._svc_with_real_memory(temp_dir)
        records = [
            _summary_record(id=1, consumed_run_ids=set()),
            _summary_record(id=2, consumed_run_ids=set()),
        ]
        config = _make_config(memory_limit=5)
        _llm_patch, mock_client = self._patch_llm(_mock_chat_response())

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(records, "2026-07-14", "2026-07-15"),
            ),
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        messages = mock_client.chat.call_args.args[0]
        user_content = messages[1].content
        assert "葬送的芙莉莲" in user_content

        """D4 演化：未消费记录正常呈现；已消费记录进摘要记忆而非 prompt。"""
        svc, _ = self._svc_with_real_memory(temp_dir)
        records = [
            _summary_record(id=1, consumed_run_ids=set()),
            _summary_record(id=2, consumed_run_ids=set()),
        ]
        config = _make_config(memory_limit=5)
        _llm_patch, mock_client = self._patch_llm(_mock_chat_response())

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(records, "2026-07-14", "2026-07-15"),
            ),
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        messages = mock_client.chat.call_args.args[0]
        user_content = messages[1].content
        assert "葬送的芙莉莲" in user_content


class TestRelatedInjection:
    """related 注入：合并去重 + [同剧历史] 前缀（F2 S5/S6）。"""

    @pytest.mark.asyncio
    async def test_related_prefix_and_dedup(self, temp_dir, reset_singletons):
        """recent 与 related 共享 run_id 时去重（recent 优先无前缀）；纯 related 冠前缀。"""
        from app.models.memory import MemoryEntry

        svc, db = TestExecuteJob._svc_with_real_memory(temp_dir)
        records = [_summary_record(id=1, title="番剧A", bgm_title="番剧A")]

        shared = MemoryEntry(
            run_id="run-shared",
            summary="芙莉莲近况",
            task_type="summary",
            task_id="summary-test_job",
        )
        only_related = MemoryEntry(
            run_id="run-old",
            summary="上一季芙莉莲",
            task_type="summary",
            task_id="summary-test_job",
        )

        # 真实 repo：让 related 联表命中（build_memory_context 直接调 db.memory）——
        # 用 mock 替换 memory 服务即可验证合并逻辑，无需构造联表数据
        mock_memory = MagicMock()
        mock_memory.recent.return_value = [shared]
        mock_memory.related.return_value = [shared, only_related]

        # 直接用真实 service 的合并逻辑（替换 memory 为 mock）
        svc.memory = mock_memory
        ctx = SummaryService._build_memory_context.__get__(svc)(
            SummaryJobConfig(name="test_job", memory_limit=5, related_limit=3),
            "summary-test_job",
            records,
        )
        assert "- 芙莉莲近况" in ctx  # recent 无前缀
        assert "- [同剧历史] 上一季芙莉莲" in ctx  # related 冠前缀
        assert ctx.count("芙莉莲近况") == 1  # 双路径命中按 run_id 去重
        mock_memory.recent.assert_called_once()
        mock_memory.related.assert_called_once()


class TestMemoryContextSkipsPlaceholder:
    """T2：摘要失败占位行（summary=""）不得进入提示词注入（recent 与 related 两路径）。"""

    def test_placeholder_skipped_in_recent(self):
        """recent 返回 [正常行, 占位行] → 注入文本只含正常摘要，无空条目。"""
        svc = SummaryService()
        mock_memory = MagicMock()
        mock_memory.recent.return_value = [
            MemoryEntry(
                run_id="r-ok",
                summary="昨日看了芙莉莲",
                task_type="summary",
                task_id="summary-test_job",
            ),
            MemoryEntry(
                run_id="r-placeholder",
                summary="",
                outcome="summary_failed",
                task_type="summary",
                task_id="summary-test_job",
            ),
        ]
        svc.memory = mock_memory

        ctx = svc._build_memory_context(
            _make_config(memory_limit=5), "summary-test_job", [_summary_record()]
        )

        assert ctx == "- 昨日看了芙莉莲"  # 占位行被完全跳过（无裸 "- " 条目）
        mock_memory.recent.assert_called_once_with(
            "summary", "summary-test_job", limit=5
        )

    def test_placeholder_skipped_in_related(self):
        """related 返回占位行 → 跳过；正常同剧历史冠 [同剧历史] 前缀保留。"""
        svc = SummaryService()
        mock_memory = MagicMock()
        mock_memory.recent.return_value = []
        mock_memory.related.return_value = [
            MemoryEntry(
                run_id="r-old",
                summary="上一季芙莉莲",
                task_type="summary",
                task_id="summary-test_job",
            ),
            MemoryEntry(
                run_id="r-placeholder",
                summary="",
                outcome="summary_failed",
                task_type="summary",
                task_id="summary-test_job",
            ),
        ]
        svc.memory = mock_memory

        ctx = svc._build_memory_context(
            _make_config(memory_limit=5, related_limit=3),
            "summary-test_job",
            [_summary_record(bgm_title="葬送的芙莉莲")],
        )

        assert ctx == "- [同剧历史] 上一季芙莉莲"
        mock_memory.related.assert_called_once()


class TestUTCToLocalDate:
    """`_utc_to_local_date`：SQLite datetime('now') 的 UTC 时间串 → 指定时区日期。

    显式注入 tz，避免断言随运行环境系统时区（CI=UTC、开发机可能 UTC+8）漂移。
    """

    def test_utc_to_local_date_utc_plus8_crosses_day(self):
        """UTC 16:30 + UTC+8 → 次日（跨日）。"""
        result = _utc_to_local_date(
            "2026-07-10 16:30:00", tz=timezone(timedelta(hours=8))
        )
        assert result == "2026-07-11"

    def test_utc_to_local_date_utc_plus8_same_day(self):
        """UTC 02:00 + UTC+8 → 同日。"""
        result = _utc_to_local_date(
            "2026-07-10 02:00:00", tz=timezone(timedelta(hours=8))
        )
        assert result == "2026-07-10"

    def test_utc_to_local_date_utc_minus8_no_inversion(self):
        """UTC 2026-07-11 04:00 + UTC-8 → 2026-07-10（西半球不产生未来日期）。"""
        result = _utc_to_local_date(
            "2026-07-11 04:00:00", tz=timezone(timedelta(hours=-8))
        )
        assert result == "2026-07-10"

    def test_utc_to_local_date_empty_returns_none(self):
        """空字符串 → None（调用方据此回退 lookback）。"""
        assert _utc_to_local_date("", tz=timezone.utc) is None

    def test_utc_to_local_date_invalid_format_returns_none(self):
        """非法格式 → None 且不抛异常。"""
        assert _utc_to_local_date("not-a-date", tz=timezone.utc) is None
        assert _utc_to_local_date("2026-07-10", tz=timezone.utc) is None


class TestIncrementalWindow:
    """T3 增量窗口：记忆开启时 date_from = 上次总结点（本任务最后一条记忆的
    created_at 日期）；无历史记忆时回退 lookback_days。"""

    def _svc(self, temp_dir):
        db = _temp_db(temp_dir)
        svc = SummaryService()
        svc.memory = MemoryService(db.memory)
        return svc, db

    def test_memory_enabled_uses_last_summary_date(self, temp_dir, reset_singletons):
        """记忆开启 + 有历史记忆 → date_from = 最后一条记忆的日期（非 lookback）。

        显式注入 UTC 时区，使 created_at（UTC）→ 日期断言与运行环境系统时区无关。
        """
        svc, db = self._svc(temp_dir)
        # store_and_mark 的 INSERT 不含 created_at（DB 默认 UTC 当前时间），
        # 故用 patch memory.recent 模拟"上次总结点在 2026-07-10"
        with (
            patch.object(
                svc.memory,
                "recent",
                return_value=[
                    MemoryEntry(
                        task_type="summary",
                        task_id="summary-test_job",
                        run_id="run-1",
                        summary="昨日总结",
                        created_at="2026-07-10 21:00:00",
                    )
                ],
            ),
            patch(
                "app.services.summary.service._utc_to_local_date",
                partial(_utc_to_local_date, tz=timezone.utc),
            ),
        ):
            config = _make_config(memory_limit=5, lookback_days=7)
            with patch("app.services.summary.service.database_manager") as mock_db:
                mock_db.get_records_in_date_range.return_value = []
                records, date_from, date_to = svc._query_records(
                    config, incremental=True
                )

        # 窗口起点 = 上次总结日期（UTC 下即 created_at 日期），而非 now-7
        assert date_from == "2026-07-10"
        mock_db.get_records_in_date_range.assert_called_once()
        assert (
            mock_db.get_records_in_date_range.call_args.kwargs["date_from"]
            == "2026-07-10"
        )

    def test_memory_enabled_utc_plus8_crosses_day(self, temp_dir, reset_singletons):
        """集成：注入 UTC+8 → created_at 16:30 转出次日，date_from 用转换结果。"""
        svc, db = self._svc(temp_dir)
        with (
            patch.object(
                svc.memory,
                "recent",
                return_value=[
                    MemoryEntry(
                        task_type="summary",
                        task_id="summary-test_job",
                        run_id="run-1",
                        summary="昨日总结",
                        created_at="2026-07-10 16:30:00",
                    )
                ],
            ),
            patch(
                "app.services.summary.service._utc_to_local_date",
                partial(_utc_to_local_date, tz=timezone(timedelta(hours=8))),
            ),
        ):
            config = _make_config(memory_limit=5, lookback_days=7)
            with patch("app.services.summary.service.database_manager") as mock_db:
                mock_db.get_records_in_date_range.return_value = []
                records, date_from, date_to = svc._query_records(
                    config, incremental=True
                )

        assert date_from == "2026-07-11"
        assert (
            mock_db.get_records_in_date_range.call_args.kwargs["date_from"]
            == "2026-07-11"
        )

    def test_future_created_at_clamps_window_to_single_day(
        self, temp_dir, reset_singletons, capsys
    ):
        """防御：时钟回拨等异常使记忆 created_at 落在未来 → 增量起点晚于终点。

        夹紧为单日窗口（date_from = date_to），查询仍正常调用且不抛异常，
        并留下 warning 日志可观测（正常时钟下该分支不触发）。

        注：项目 logger 为自定义实现（print 输出，非 stdlib logging），
        故用 capsys 捕获，而非 caplog。
        """
        svc, db = self._svc(temp_dir)
        with (
            patch.object(
                svc.memory,
                "recent",
                return_value=[
                    MemoryEntry(
                        task_type="summary",
                        task_id="summary-test_job",
                        run_id="run-future",
                        summary="未来记忆",
                        created_at="2099-12-31 12:00:00",
                    )
                ],
            ),
            patch(
                "app.services.summary.service._utc_to_local_date",
                partial(_utc_to_local_date, tz=timezone.utc),
            ),
        ):
            config = _make_config(memory_limit=5, lookback_days=7)
            with patch("app.services.summary.service.database_manager") as mock_db:
                mock_db.get_records_in_date_range.return_value = []
                records, date_from, date_to = svc._query_records(
                    config, incremental=True
                )

        # 倒置区间被夹紧为单日：查询正常调用（非倒置）且不抛异常
        assert date_from == date_to
        mock_db.get_records_in_date_range.assert_called_once()
        call_kwargs = mock_db.get_records_in_date_range.call_args.kwargs
        assert call_kwargs["date_from"] == call_kwargs["date_to"]
        assert "window inverted" in capsys.readouterr().out

    def test_placeholder_row_serves_as_incremental_start(
        self, temp_dir, reset_singletons
    ):
        """T2：最近一条是摘要失败占位行（summary=""）时，增量窗口起点仍取它的
        created_at 日期——占位行是有效的"上次总结点"，不得因空摘要被跳过导致窗口回退。"""
        svc, db = self._svc(temp_dir)
        with (
            patch.object(
                svc.memory,
                "recent",
                return_value=[
                    MemoryEntry(
                        task_type="summary",
                        task_id="summary-test_job",
                        run_id="run-placeholder",
                        summary="",
                        outcome="summary_failed",
                        created_at="2026-07-10 21:00:00",
                    )
                ],
            ) as mock_recent,
            patch(
                "app.services.summary.service._utc_to_local_date",
                partial(_utc_to_local_date, tz=timezone.utc),
            ),
        ):
            config = _make_config(memory_limit=5, lookback_days=7)
            with patch("app.services.summary.service.database_manager") as mock_db:
                mock_db.get_records_in_date_range.return_value = []
                records, date_from, date_to = svc._query_records(
                    config, incremental=True
                )

        assert date_from == "2026-07-10"  # 占位行起点生效，非 lookback
        mock_recent.assert_called_once_with("summary", "summary-test_job", limit=1)

    def test_invalid_created_at_falls_back_to_lookback(
        self, temp_dir, reset_singletons
    ):
        """created_at 非法 → 转换返回 None，回退 lookback_days 且不抛异常。"""
        svc, db = self._svc(temp_dir)
        with patch.object(
            svc.memory,
            "recent",
            return_value=[
                MemoryEntry(
                    task_type="summary",
                    task_id="summary-test_job",
                    run_id="run-1",
                    summary="脏数据",
                    created_at="not-a-date",
                )
            ],
        ):
            config = _make_config(memory_limit=5, lookback_days=7)
            with patch("app.services.summary.service.database_manager") as mock_db:
                mock_db.get_records_in_date_range.return_value = []
                records, date_from, date_to = svc._query_records(
                    config, incremental=True
                )

        expected = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert date_from == expected

    def test_empty_created_at_falls_back_to_lookback(self, temp_dir, reset_singletons):
        """created_at 为空 → 回退 lookback_days，不抛异常。"""
        svc, db = self._svc(temp_dir)
        with patch.object(
            svc.memory,
            "recent",
            return_value=[
                MemoryEntry(
                    task_type="summary",
                    task_id="summary-test_job",
                    run_id="run-1",
                    summary="空时间",
                    created_at="",
                )
            ],
        ):
            config = _make_config(memory_limit=5, lookback_days=7)
            with patch("app.services.summary.service.database_manager") as mock_db:
                mock_db.get_records_in_date_range.return_value = []
                records, date_from, date_to = svc._query_records(
                    config, incremental=True
                )

        expected = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert date_from == expected

    def test_no_memory_falls_back_to_lookback(self, temp_dir, reset_singletons):
        """记忆开启但无历史 → date_from 回退 lookback_days（incremental 分支）。"""
        svc, db = self._svc(temp_dir)
        config = _make_config(memory_limit=5, lookback_days=7)

        with patch("app.services.summary.service.database_manager") as mock_db:
            mock_db.get_records_in_date_range.return_value = []
            records, date_from, date_to = svc._query_records(config, incremental=True)

        expected = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert date_from == expected

    def test_memory_disabled_ignores_incremental(self, temp_dir, reset_singletons):
        """memory_limit=0 → 不查历史记忆，窗口 = lookback_days。"""
        svc, db = self._svc(temp_dir)
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-test_job",
                run_id="run-1",
                summary="昨日总结",
                created_at="2026-07-10 21:00:00",
            ),
            [],
        )
        config = _make_config(memory_limit=0, lookback_days=7)

        with patch("app.services.summary.service.database_manager") as mock_db:
            mock_db.get_records_in_date_range.return_value = []
            records, date_from, date_to = svc._query_records(config, incremental=True)

        expected = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert date_from == expected

    @pytest.mark.asyncio
    async def test_execute_job_passes_incremental_flag(
        self, temp_dir, reset_singletons
    ):
        """wiring：execute_job 调用 _query_records 时必须传 incremental=True。"""
        svc, db = TestExecuteJob._svc_with_real_memory(temp_dir)
        config = _make_config(memory_limit=5, lookback_days=7)
        _llm_patch, mock_client = TestExecuteJob._patch_llm(svc, _mock_chat_response())

        with (
            patch.object(
                svc, "_query_records", return_value=([], "2026-07-14", "2026-07-15")
            ) as mock_query,
            _llm_patch,
            patch("app.services.summary.service.notification_service"),
        ):
            await svc.execute_job(config)

        assert mock_query.call_args.kwargs["incremental"] is True

    @pytest.mark.asyncio
    async def test_preview_does_not_use_incremental(self, temp_dir, reset_singletons):
        """wiring：generate_summary（预览）调用 _query_records 时 incremental 默认 False。"""
        svc = SummaryService()
        config = _make_config(memory_limit=5, lookback_days=7)
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=_mock_chat_response())

        with (
            patch.object(
                svc, "_query_records", return_value=([], "2026-07-14", "2026-07-15")
            ) as mock_query,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm,
            ),
        ):
            await svc.generate_summary(config)

        assert mock_query.call_args.kwargs.get("incremental", False) is False


class TestEmptyContentWithModel:
    """H1：空 content 但 model 非空 → 仍须判失败（旧逻辑误走成功分支）。"""

    @pytest.mark.asyncio
    async def test_empty_content_with_model_name_sends_failure(
        self, temp_dir, reset_singletons
    ):
        """provider 返回空 choices 但带上 model 名 → 仍发 summary_llm_failed。"""
        svc = TestExecuteJob._make_svc()
        config = _make_config(name="empty_model_job")
        empty = ChatResponse(content="", model="gpt-4o-mini", usage=None, latency=5)

        with (
            patch.object(
                svc, "_query_records", return_value=([], "2026-07-14", "2026-07-15")
            ),
            TestExecuteJob._patch_llm(svc, empty)[0],
            patch("app.services.summary.service.notification_service") as mock_ns,
        ):
            await svc.execute_job(config)

        call_args = mock_ns.notify.call_args
        assert call_args.kwargs["in_app_type"] == "summary_llm_failed"


class TestRelatedIndependentOfMemoryLimit:
    """M1：related_limit 独立于 memory_limit（0/关 不影响 related 生效）。"""

    @pytest.mark.asyncio
    async def test_related_works_when_memory_limit_zero(
        self, temp_dir, reset_singletons
    ):
        """memory_limit=0（不写入/不排除）+ related_limit=3 → related 仍被调用。"""
        svc, _ = TestExecuteJob._svc_with_real_memory(temp_dir)
        records = [_summary_record(id=1, bgm_title="葬送的芙莉莲")]
        config = _make_config(memory_limit=0, related_limit=3)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(records, "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service"),
            patch.object(svc, "memory") as mock_memory,
        ):
            mock_memory.recent.return_value = []
            mock_memory.related.return_value = []
            await svc.execute_job(config)

        mock_memory.related.assert_called_once_with(
            "summary", "summary-test_job", ["葬送的芙莉莲"], limit=3
        )
        # 不写入记忆（memory_limit=0）
        assert not mock_memory.extract_and_store.called


# ── 并发执行保护（同一任务运行中登记）────────────────────────────


class TestConcurrentExecutionGuard:
    """execute_job 的任务级进程内互斥：同任务并发只执行一次，其余跳过返回 False。"""

    @staticmethod
    def _gated_llm(release: asyncio.Event, started=None, block_name=None):
        """构造 chat 受 release 控制的 mock client。

        block_name=None 时阻塞所有调用；否则仅阻塞 job_name==block_name 的调用。
        ``started`` 在进入被阻塞调用时 set，供测试等待"已持锁"。
        返回 (client, 已调用的 job_name 列表)。
        """
        client = MagicMock()
        called_jobs: list[str | None] = []

        async def _chat(messages, **kwargs):
            job_name = kwargs.get("job_name")
            called_jobs.append(job_name)
            if block_name is None or job_name == block_name:
                if started is not None:
                    started.set()
                await release.wait()
            return _mock_chat_response()

        client.chat = AsyncMock(side_effect=_chat)
        return client, called_jobs

    @pytest.mark.asyncio
    async def test_same_job_concurrent_only_one_executes(self):
        """同一任务并发触发：恰好一个 True、一个 False；LLM 与通知各一次。"""
        svc = SummaryService()
        config = _make_config(name="concurrent_job")
        started, release = asyncio.Event(), asyncio.Event()
        client, called_jobs = self._gated_llm(release, started)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=client,
            ),
            patch("app.services.summary.service.notification_service") as mock_ns,
        ):
            first = asyncio.create_task(svc.execute_job(config))
            await started.wait()  # 第一个已进入 chat（登记已持有）
            try:
                # 第二个若未被守卫拦截会阻塞在 chat（timeout 保证红阶段不挂起）
                second = await asyncio.wait_for(svc.execute_job(config), timeout=2)
            finally:
                release.set()
            first_result = await first

        assert first_result is True
        assert second is False
        assert called_jobs == ["concurrent_job"]
        assert client.chat.await_count == 1
        mock_ns.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_skipped_call_has_no_side_effects(self):
        """被跳过的调用不查库、不调 LLM、不写记忆、不发通知。"""
        svc = SummaryService()
        config = _make_config(name="side_effect_job", memory_limit=5)
        started, release = asyncio.Event(), asyncio.Event()
        client, _ = self._gated_llm(release, started)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ) as mock_query,
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=client,
            ),
            patch("app.services.summary.service.notification_service") as mock_ns,
            patch.object(svc, "memory") as mock_memory,
        ):
            mock_memory.recent.return_value = []
            mock_memory.related.return_value = []
            mock_memory.get_task_run_ids.return_value = set()
            mock_memory.extract_and_store = AsyncMock()

            first = asyncio.create_task(svc.execute_job(config))
            await started.wait()
            try:
                second = await asyncio.wait_for(svc.execute_job(config), timeout=2)
            finally:
                release.set()
            first_result = await first

        assert first_result is True
        assert second is False
        # 跳过的调用零副作用：查询/LLM/记忆/通知都只发生第一次
        assert mock_query.call_count == 1
        assert client.chat.await_count == 1
        assert mock_memory.extract_and_store.await_count == 1
        mock_ns.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_guard_released_after_error(self):
        """一次执行内部出错（消化为失败通知）后登记释放，同任务可再次执行。"""
        svc = SummaryService()
        config = _make_config(name="release_job")

        with (
            patch.object(svc, "_query_records", side_effect=RuntimeError("boom")),
            patch("app.services.summary.service.notification_service"),
        ):
            first = await svc.execute_job(config)

        assert first is True  # 异常被消化为失败通知，本次视为已执行
        assert config.name not in svc._running

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service"),
        ):
            second = await svc.execute_job(config)

        assert second is True

    @pytest.mark.asyncio
    async def test_guard_released_after_cancellation(self):
        """执行被取消（超时）后登记释放，后续同任务可正常执行。"""
        svc = SummaryService()
        config = _make_config(name="cancel_job")
        started, release = asyncio.Event(), asyncio.Event()
        client, _ = self._gated_llm(release, started)

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=client,
            ),
            patch("app.services.summary.service.notification_service"),
        ):
            task = asyncio.create_task(svc.execute_job(config))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert config.name not in svc._running

        # 释放后可再次正常执行
        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            TestExecuteJob._patch_llm(svc, _mock_chat_response())[0],
            patch("app.services.summary.service.notification_service"),
        ):
            assert await svc.execute_job(config) is True

    @pytest.mark.asyncio
    async def test_different_jobs_run_concurrently(self):
        """不同 name 的任务互不影响，可并发执行且都返回 True。"""
        svc = SummaryService()
        config_a = _make_config(name="job_a")
        config_b = _make_config(name="job_b")
        started_a, release_a = asyncio.Event(), asyncio.Event()
        client, called_jobs = self._gated_llm(release_a, started_a, block_name="job_a")

        with (
            patch.object(
                svc,
                "_query_records",
                return_value=(_records(), "2026-07-14", "2026-07-15"),
            ),
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=client,
            ),
            patch("app.services.summary.service.notification_service"),
        ):
            task_a = asyncio.create_task(svc.execute_job(config_a))
            await started_a.wait()  # job_a 持登记并阻塞
            try:
                # 不同任务不应被 job_a 阻塞；timeout 保证实现错误时不挂起
                result_b = await asyncio.wait_for(svc.execute_job(config_b), timeout=2)
            finally:
                release_a.set()
            result_a = await task_a

        assert result_a is True
        assert result_b is True
        assert set(called_jobs) == {"job_a", "job_b"}
