"""测试 SummaryService.generate_summary_stream：试生成流式变体。

覆盖：
- 事件透传（text_delta/usage/stop 原样产出，顺序不变）
- 消息构造与 job_name / thinking_level 透传（复用 generate_summary 的公共路径）
- 终态元数据由业务自行收集（model / usage / latency / record_count；去鸭子容器契约）
- 异常透传（不吞错，由上层转 SSE error 事件）
- generate_summary（聚合路径）走 collect(stream_chat)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.llm.models import StreamChunk, Usage
from app.services.summary.models import SummaryJobConfig
from app.services.summary.service import SummaryService, SummaryStreamResult

# ── helpers ────────────────────────────────────────────────────────────


def _make_config(**overrides) -> SummaryJobConfig:
    defaults: dict[str, Any] = {
        "name": "stream_job",
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


def _stream_chunks() -> list[StreamChunk]:
    return [
        StreamChunk(type="text_delta", text="你"),
        StreamChunk(type="text_delta", text="好"),
        StreamChunk(
            type="usage",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        ),
        StreamChunk(type="stop", stop_reason="end_turn"),
    ]


def _mock_stream_client(
    chunks: list[StreamChunk],
    *,
    capture: dict | None = None,
) -> MagicMock:
    """构造 mock LLM 客户端：stream_chat 为 async generator，原样产出给定事件。"""

    def _stream_chat(messages, **kwargs):
        if capture is not None:
            capture["messages"] = messages
            capture["job_name"] = kwargs.get("job_name")
            capture["thinking_level"] = kwargs.get("thinking_level")

        async def _gen():
            for chunk in chunks:
                yield chunk

        return _gen()

    client = MagicMock()
    client.stream_chat = MagicMock(side_effect=_stream_chat)
    return client


@contextmanager
def _patch_db_and_llm(client, records=None):
    """同时 patch database_manager 与 get_llm_client，并预设查询返回。"""
    with (
        patch("app.services.summary.service.database_manager") as mock_db,
        patch(
            "app.services.summary.service.get_llm_client",
            return_value=client,
        ),
    ):
        mock_db.get_records_in_date_range.return_value = (
            records if records is not None else _sample_records()
        )
        yield mock_db


# ── generate_summary_stream ─────────────────────────────────────────────


class TestGenerateSummaryStream:
    """SummaryService.generate_summary_stream() 测试。"""

    @pytest.mark.asyncio
    async def test_passes_through_chunks_in_order(self):
        """client.stream_chat 的事件原样透传，顺序与内容不变。"""
        svc = SummaryService()
        config = _make_config()
        chunks = _stream_chunks()
        client = _mock_stream_client(chunks)

        with _patch_db_and_llm(client):
            produced = [c async for c in svc.generate_summary_stream(config)]

        assert produced == chunks

    @pytest.mark.asyncio
    async def test_builds_messages_and_passes_job_name(self):
        """消息构造与 generate_summary 一致：system + user，job_name 透传。"""
        svc = SummaryService()
        config = _make_config(
            name="my_stream", system_prompt="Custom system instruction."
        )
        capture: dict = {}
        client = _mock_stream_client(_stream_chunks(), capture=capture)

        with _patch_db_and_llm(client):
            _ = [c async for c in svc.generate_summary_stream(config)]

        messages = capture["messages"]
        assert messages[0].role == "system"
        assert messages[0].content == "Custom system instruction."
        assert messages[1].role == "user"
        assert "葬送的芙莉莲" in messages[1].content
        assert "共 2 条" in messages[1].content
        assert capture["job_name"] == "my_stream"
        assert capture["thinking_level"] == config.thinking_level

    @pytest.mark.asyncio
    async def test_default_system_prompt_when_empty(self):
        """system_prompt 为空白时回落类默认值（与 generate_summary 同源）。"""
        svc = SummaryService()
        config = _make_config(system_prompt="   ")
        capture: dict = {}
        client = _mock_stream_client(_stream_chunks(), capture=capture)

        with _patch_db_and_llm(client):
            _ = [c async for c in svc.generate_summary_stream(config)]

        assert capture["messages"][0].content == SummaryJobConfig.system_prompt

    @pytest.mark.asyncio
    async def test_fills_result_meta(self):
        """耗尽后 result 回填业务自采元数据：model / usage / latency / record_count。

        后 client 不再回填容器：model 取自事件 chunk.model，latency 由 service
        自行计时（此处只断言非负，不依赖 client 传值）。
        """
        svc = SummaryService()
        config = _make_config()
        chunks = [
            StreamChunk(type="text_delta", text="你", model="gpt-4o"),
            StreamChunk(type="text_delta", text="好"),
            StreamChunk(
                type="usage",
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            ),
            StreamChunk(type="stop", stop_reason="end_turn"),
        ]
        client = _mock_stream_client(chunks)
        result = SummaryStreamResult()

        with _patch_db_and_llm(client):
            _ = [c async for c in svc.generate_summary_stream(config, result)]

        assert result.model == "gpt-4o"
        assert result.latency_ms >= 0
        assert result.usage is not None
        assert result.usage.total_tokens == 15
        assert result.record_count == 2

    @pytest.mark.asyncio
    async def test_model_falls_back_to_config_when_absent(self):
        """事件未携带 model → 回退当前 LLM 配置的 model（展示口径）。"""
        svc = SummaryService()
        config = _make_config()
        client = _mock_stream_client(_stream_chunks())  # 事件均不带 model
        result = SummaryStreamResult()

        with (
            _patch_db_and_llm(client),
            patch(
                "app.services.summary.service.config_manager.get_llm_config",
                return_value={"model": "cfg-model"},
            ),
        ):
            _ = [c async for c in svc.generate_summary_stream(config, result)]

        assert result.model == "cfg-model"

    @pytest.mark.asyncio
    async def test_propagates_stream_error(self):
        """流中抛异常向上透传（由 API 层转 error 事件），不静默吞掉。"""
        svc = SummaryService()
        config = _make_config()

        async def _boom(messages, **_):
            yield StreamChunk(type="text_delta", text="a")
            raise RuntimeError("llm down")

        client = MagicMock()
        client.stream_chat = _boom

        with _patch_db_and_llm(client):
            with pytest.raises(RuntimeError, match="llm down"):
                _ = [c async for c in svc.generate_summary_stream(config)]

    @pytest.mark.asyncio
    async def test_uses_only_stream_chat(self):
        """流式变体只走 stream_chat（无 chat() 别名，天然不会双份调用）。"""
        svc = SummaryService()
        config = _make_config()
        client = _mock_stream_client(_stream_chunks())

        with _patch_db_and_llm(client):
            _ = [c async for c in svc.generate_summary_stream(config)]

        client.stream_chat.assert_called_once()


class TestGenerateSummaryRegression:
    """聚合路径 generate_summary 走 collect(stream_chat)（回归）。"""

    @pytest.mark.asyncio
    async def test_generate_summary_uses_stream_chat_collect(self):
        """generate_summary 消费 collect(stream_chat) 聚合出完整字段。"""
        svc = SummaryService()
        config = _make_config()
        usage = Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        client = _mock_stream_client(
            [
                StreamChunk(type="text_delta", text="聚合正文", model="gpt-4"),
                StreamChunk(type="usage", usage=usage),
            ]
        )

        with _patch_db_and_llm(client):
            result = await svc.generate_summary(config)

        assert result.summary_text == "聚合正文"
        assert result.model == "gpt-4"
        assert result.usage is usage
        assert result.record_count == 2
        client.stream_chat.assert_called_once()
