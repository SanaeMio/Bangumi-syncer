"""MemoryExtractor 测试（Phase 2.0.1 摘要生成）。

覆盖 BDD 场景 W1/W2/W3/W5。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.memory import MemoryEntry
from app.services.llm.models import ChatResponse, Message, Usage
from app.services.memory.extractor import MemoryExtractor


def _response(content: str = "本次总结全文内容") -> ChatResponse:
    return ChatResponse(
        content=content,
        model="test-model",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _messages() -> list[Message]:
    return [
        Message(role="system", content="system prompt"),
        Message(role="user", content="records..."),
    ]


def _make_extractor(
    repo=None, llm=None
) -> tuple[MemoryExtractor, MagicMock, MagicMock]:
    repo = repo or MagicMock()
    llm = llm or MagicMock()
    llm.chat = AsyncMock(return_value=ChatResponse(content="一句话摘要", model="m"))
    extractor = MemoryExtractor(repo, llm_client=llm)
    return extractor, repo, llm


# ── W1 成功路径写入 ─────────────────────────────────────────────────────


class TestExtractAndStore:
    @pytest.mark.asyncio
    async def test_writes_summary_and_prunes(self):
        """extract_and_store 用 LLM 摘要写入记忆并 prune。"""
        extractor, repo, llm = _make_extractor()
        response = _response("本次总结全文")

        await extractor.extract_and_store(
            task_type="summary",
            task_id="summary-daily",
            run_id="run-1",
            messages=_messages(),
            response=response,
            outcome="success",
            tokens_used=150,
            record_ids=[1, 2],
        )

        # LLM 收到完整上下文（system+user）+ assistant 回复 + 摘要指令（缓存前缀复用）
        args = llm.chat.await_args.args[0]
        assert len(args) == 4
        assert args[2].role == "assistant"
        assert args[2].content == "本次总结全文"
        assert args[3].role == "user"

        repo.store_and_mark.assert_called_once()
        entry: MemoryEntry = repo.store_and_mark.call_args.args[0]
        assert entry.task_type == "summary"
        assert entry.task_id == "summary-daily"
        assert entry.run_id == "run-1"
        assert entry.summary == "一句话摘要"
        assert entry.full_text == "本次总结全文"
        assert entry.outcome == "success"
        assert entry.tokens_used == 150
        assert repo.store_and_mark.call_args.kwargs["record_ids"] == [1, 2]

        repo.prune.assert_called_once_with("summary", "summary-daily", keep=1000)


# ── W2 摘要 LLM 失败规则兜底 ────────────────────────────────────────────


class TestLazyLlmClient:
    @pytest.mark.asyncio
    async def test_no_injected_client_uses_global_singleton_per_call(self):
        """未注入 llm_client 时，_summarize 每次现取 get_llm_client()（reset 后不滞留旧实例）。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            return_value=ChatResponse(content="一句话", model="m")
        )
        extractor = MemoryExtractor(MagicMock(), llm_client=None)

        with patch(
            "app.services.memory.extractor.get_llm_client", return_value=mock_llm
        ):
            summary = await extractor._summarize(_messages(), _response())

        assert summary == "一句话"
        mock_llm.chat.assert_awaited_once()


# ── 摘要失败跳过不写（S9：无截断兜底，所有入库摘要均为 LLM 完整输出）──────


class TestSummarizeFailSkips:
    @pytest.mark.asyncio
    async def test_llm_exception_returns_empty_and_skips_write(self):
        """S9：_summarize LLM 抛异常 → 返回空串（不写记忆，无截断兜底）。"""
        extractor, repo, llm = _make_extractor()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        response = _response("很长的全文" * 100)

        summary = await extractor._summarize(_messages(), response)

        assert summary == ""
        # extract_and_store 空摘要时跳过写入（同空响应路径）
        await extractor.extract_and_store(
            task_type="summary",
            task_id="summary-daily",
            run_id="run-1",
            messages=_messages(),
            response=response,
            outcome="success",
            tokens_used=0,
            record_ids=[],
        )
        repo.store_and_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_llm_summary_returns_empty(self):
        """摘要 LLM 返回空内容 → 返回空串，不写记忆。"""
        extractor, repo, llm = _make_extractor()
        llm.chat = AsyncMock(return_value=ChatResponse(content="", model="m"))
        response = _response("兜底全文" * 100)

        summary = await extractor._summarize(_messages(), response)

        assert summary == ""

    @pytest.mark.asyncio
    async def test_long_summary_kept_intact(self):
        """S8：成功路径零截断——LLM 输出多长存多长（无 _SUMMARY_MAX_LEN）。"""
        long_text = "长" * 500
        extractor, repo, llm = _make_extractor()
        llm.chat = AsyncMock(return_value=ChatResponse(content=long_text, model="m"))
        response = _response("全文")

        await extractor.extract_and_store(
            task_type="summary",
            task_id="summary-daily",
            run_id="run-1",
            messages=_messages(),
            response=response,
            outcome="success",
            tokens_used=0,
            record_ids=[],
        )
        entry: MemoryEntry = repo.store_and_mark.call_args.args[0]
        assert entry.summary == long_text  # 500 字完整原文


# ── 空响应不写记忆 ───────────────────────────────────────────────────


class TestEmptyResponse:
    @pytest.mark.asyncio
    async def test_empty_response_skips_write(self):
        """W3：LLM 重试耗尽返回空响应 → 不写记忆。"""
        extractor, repo, llm = _make_extractor()
        empty = ChatResponse(content="", model="", usage=None, latency=5)

        await extractor.extract_and_store(
            task_type="summary",
            task_id="summary-daily",
            run_id="run-1",
            messages=_messages(),
            response=empty,
            outcome="success",
            tokens_used=0,
            record_ids=[],
        )

        repo.store_and_mark.assert_not_called()
        repo.prune.assert_not_called()
        llm.chat.assert_not_awaited()  # 空响应不触发摘要调用


# ── W5 写入失败不影响调用方 ─────────────────────────────────────────────


class TestFailurePropagation:
    @pytest.mark.asyncio
    async def test_repo_failure_propagates_to_caller(self):
        """W5：DB 写入异常向上传播（execute_job 外层 try/except 处理）。"""
        repo = MagicMock()
        repo.store_and_mark.side_effect = RuntimeError("db down")
        extractor, _, _ = _make_extractor(repo=repo)

        with pytest.raises(RuntimeError):
            await extractor.extract_and_store(
                task_type="summary",
                task_id="summary-daily",
                run_id="run-1",
                messages=_messages(),
                response=_response(),
                outcome="success",
                tokens_used=0,
                record_ids=[],
            )
