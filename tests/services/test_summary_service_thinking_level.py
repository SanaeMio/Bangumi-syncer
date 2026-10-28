"""SummaryService thinking_level 透传测试。

覆盖场景：
1. execute_job 执行 thinking_level="high" 的 job → stream_chat 收到 thinking_level="high"
2. execute_job 执行 thinking_level="off" 的 job → stream_chat 收到 thinking_level="off"
3. generate_summary 未配置时回退默认 → 透传 "off"
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.llm.models import StreamChunk, Usage
from app.services.summary.models import SummaryJobConfig


def _make_stream_client() -> MagicMock:
    """构造非空流响应的 mock client（走成功通知路径）。

    summary 服务统一走 ``stream_chat``（流式唯一形态），故 mock 该入口。
    """
    chunks = [
        StreamChunk(type="text_delta", text="测试总结", model="test-model"),
        StreamChunk(
            type="usage",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        ),
    ]
    client = MagicMock()

    def _stream_chat(messages, **kwargs):
        async def _gen():
            for chunk in chunks:
                yield chunk

        return _gen()

    client.stream_chat = MagicMock(side_effect=_stream_chat)
    return client


class TestSummaryServiceThinkingLevel:
    """验证 SummaryService 执行 LLM 调用时按任务级 thinking_level 透传。"""

    @pytest.fixture
    def mock_llm_client(self):
        """模拟 llm_client，返回固定流式响应。"""
        return _make_stream_client()

    @pytest.fixture
    def mock_db(self):
        """模拟 database_manager 返回空记录。"""
        with patch("app.services.summary.service.database_manager") as mock_db:
            mock_db.get_records_in_date_range.return_value = []
            yield mock_db

    @pytest.mark.asyncio
    async def test_execute_job_thinking_level_high_passed_to_chat(
        self, mock_llm_client, mock_db
    ):
        """场景：执行 thinking_level="high" 的 job → stream_chat 收到 "high"。"""
        with (
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
            patch("app.services.summary.service.notification_service"),
        ):
            from app.services.summary.service import SummaryService

            service = SummaryService()
            config = SummaryJobConfig(name="测试", thinking_level="high")

            await service.execute_job(config)

            call_kwargs = mock_llm_client.stream_chat.call_args.kwargs
            assert call_kwargs["thinking_level"] == "high"

    @pytest.mark.asyncio
    async def test_execute_job_thinking_level_off_passed_to_chat(
        self, mock_llm_client, mock_db
    ):
        """场景：执行 thinking_level="off" 的 job → stream_chat 收到 "off"。"""
        with (
            patch(
                "app.services.summary.service.get_llm_client",
                return_value=mock_llm_client,
            ),
            patch("app.services.summary.service.notification_service"),
        ):
            from app.services.summary.service import SummaryService

            service = SummaryService()
            config = SummaryJobConfig(name="测试", thinking_level="off")

            await service.execute_job(config)

            call_kwargs = mock_llm_client.stream_chat.call_args.kwargs
            assert call_kwargs["thinking_level"] == "off"

    @pytest.mark.asyncio
    async def test_generate_summary_default_thinking_level_passed_to_chat(
        self, mock_llm_client, mock_db
    ):
        """场景：未配置时回退默认 → 透传 "off"。"""
        with patch(
            "app.services.summary.service.get_llm_client",
            return_value=mock_llm_client,
        ):
            from app.services.summary.service import SummaryService

            service = SummaryService()
            config = SummaryJobConfig(name="测试")  # 默认 thinking_level="off"

            await service.generate_summary(config)

            call_kwargs = mock_llm_client.stream_chat.call_args.kwargs
            assert call_kwargs["thinking_level"] == "off"
