"""MemoryExtractor 测试（摘要生成）。

覆盖 BDD 场景 W1/W2/W3/W5。
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.memory import MemoryEntry
from app.services.llm import LLMCallError
from app.services.llm.models import ChatResponse, Message, StreamChunk, Usage
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


def _make_llm(
    content: str = "一句话摘要",
    *,
    model: str = "m",
    side_effect=None,
) -> MagicMock:
    """mock LLM 客户端：``stream_chat`` 产出 text_delta 事件流（契约）。

    ``side_effect`` 传入时直接作为 ``stream_chat`` 的副作用（可抛异常或产出
    自定义事件流）；否则按 ``content`` 生成单条 text_delta 事件。
    """
    llm = MagicMock()
    if side_effect is not None:
        llm.stream_chat = MagicMock(side_effect=side_effect)
        return llm

    async def _stream(messages, *, job_name=None, **kwargs):
        yield StreamChunk(type="text_delta", text=content, model=model)

    llm.stream_chat = MagicMock(side_effect=_stream)
    return llm


def _make_extractor(
    repo=None, llm=None
) -> tuple[MemoryExtractor, MagicMock, MagicMock]:
    repo = repo or MagicMock()
    llm = llm or _make_llm()
    extractor = MemoryExtractor(repo, llm_client=llm)
    return extractor, repo, llm


# ── 成功路径写入 ─────────────────────────────────────────────────────


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
        args = llm.stream_chat.call_args.args[0]
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

    @pytest.mark.asyncio
    async def test_forwards_job_name_to_stream_chat(self):
        """job_name 原样透传给 stream_chat（摘要 token 归属 llm_usage）。"""
        extractor, _repo, llm = _make_extractor()

        await extractor._summarize(_messages(), _response(), job_name="summary_daily")

        assert llm.stream_chat.call_args.kwargs["job_name"] == "summary_daily"


# ── 摘要 LLM 失败规则兜底 ────────────────────────────────────────────


class TestLazyLlmClient:
    @pytest.mark.asyncio
    async def test_no_injected_client_uses_global_singleton_per_call(self):
        """未注入 llm_client 时，_summarize 每次现取 get_llm_client()（reset 后不滞留旧实例）。"""
        mock_llm = _make_llm("一句话")
        extractor = MemoryExtractor(MagicMock(), llm_client=None)

        with patch(
            "app.services.memory.extractor.get_llm_client", return_value=mock_llm
        ):
            summary = await extractor._summarize(_messages(), _response())

        assert summary == "一句话"
        mock_llm.stream_chat.assert_called_once()


# ── 摘要失败写「消费占位行」（无截断兜底，所有非空入库摘要均为 LLM 完整输出）──


class TestSummarizeFailWritesPlaceholder:
    """摘要失败（异常/空摘要）写 summary 留空的占位行，让 run 有归属。

    消费标记与占位行同事务写入（store_and_mark），消费排除因此生效，
    下次调度不再重复总结同一批记录（避免重复通知 + token 白烧）。
    """

    @pytest.mark.asyncio
    async def test_llm_exception_writes_placeholder_and_prunes(self):
        """_summarize LLM 抛异常 → 返回空串，extract_and_store 反写占位行。"""
        extractor, repo, llm = _make_extractor(
            llm=_make_llm(side_effect=RuntimeError("llm down"))
        )
        response = _response("很长的全文" * 100)

        summary = await extractor._summarize(_messages(), response)
        assert summary == ""

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

        repo.store_and_mark.assert_called_once()
        entry: MemoryEntry = repo.store_and_mark.call_args.args[0]
        assert entry.task_type == "summary"
        assert entry.task_id == "summary-daily"
        assert entry.run_id == "run-1"
        assert entry.summary == ""  # 占位行留空（NOT NULL 用空串）
        assert entry.full_text == "很长的全文" * 100  # 主总结全文照存（回溯用）
        assert entry.outcome == "summary_failed"
        assert entry.tokens_used == 150  # 与成功行同口径（主调用 token）
        assert repo.store_and_mark.call_args.kwargs["record_ids"] == [1, 2]
        repo.prune.assert_called_once_with("summary", "summary-daily", keep=1000)

    @pytest.mark.asyncio
    async def test_empty_llm_summary_writes_placeholder(self):
        """摘要 LLM 返回空内容 → 返回空串，同样写占位行（与异常同路径）。"""
        extractor, repo, llm = _make_extractor(llm=_make_llm(""))
        response = _response("兜底全文")

        summary = await extractor._summarize(_messages(), response)
        assert summary == ""

        await extractor.extract_and_store(
            task_type="summary",
            task_id="summary-daily",
            run_id="run-1",
            messages=_messages(),
            response=response,
            outcome="success",
            tokens_used=150,
            record_ids=[7],
        )

        repo.store_and_mark.assert_called_once()
        entry: MemoryEntry = repo.store_and_mark.call_args.args[0]
        assert entry.summary == ""
        assert entry.full_text == "兜底全文"
        assert entry.outcome == "summary_failed"
        assert entry.tokens_used == 150
        assert repo.store_and_mark.call_args.kwargs["record_ids"] == [7]
        repo.prune.assert_called_once_with("summary", "summary-daily", keep=1000)

    @pytest.mark.asyncio
    async def test_empty_record_ids_still_writes_placeholder(self):
        """空 record_ids（今日无新记录）→ 占位行照写（与成功路径对称）。"""
        extractor, repo, llm = _make_extractor(
            llm=_make_llm(side_effect=RuntimeError("llm down"))
        )
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

        repo.store_and_mark.assert_called_once()
        assert repo.store_and_mark.call_args.kwargs["record_ids"] == []
        entry: MemoryEntry = repo.store_and_mark.call_args.args[0]
        assert entry.summary == ""
        assert entry.outcome == "summary_failed"
        repo.prune.assert_called_once_with("summary", "summary-daily", keep=1000)

    @pytest.mark.asyncio
    async def test_long_summary_kept_intact(self):
        """成功路径零截断——LLM 输出多长存多长（无 _SUMMARY_MAX_LEN）。"""
        long_text = "长" * 500
        extractor, repo, llm = _make_extractor(llm=_make_llm(long_text))
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


# ── 主总结空响应：占位行（full_text 空）────────────────────────────────


class TestEmptyResponse:
    @pytest.mark.asyncio
    async def test_empty_response_writes_placeholder(self):
        """主总结空响应 → 占位行（full_text 空、outcome=summary_failed），不触发摘要调用。"""
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
            record_ids=[3],
        )

        repo.store_and_mark.assert_called_once()
        entry: MemoryEntry = repo.store_and_mark.call_args.args[0]
        assert entry.summary == ""
        assert entry.full_text == ""  # 主总结空 → 占位行全文留空
        assert entry.outcome == "summary_failed"
        assert repo.store_and_mark.call_args.kwargs["record_ids"] == [3]
        repo.prune.assert_called_once_with("summary", "summary-daily", keep=1000)
        llm.stream_chat.assert_not_called()  # 空响应不触发摘要调用

    @pytest.mark.asyncio
    async def test_llm_call_error_mid_stream_writes_placeholder(self):
        """失败语义：流中途抛 LLMCallError → 摘要返回空串，写消费占位行。"""

        async def _boom(messages, *, job_name=None, **kwargs):
            yield StreamChunk(type="text_delta", text="部分输出")
            raise LLMCallError("stream broke", retryable=True)

        extractor, repo, llm = _make_extractor()
        llm.stream_chat = MagicMock(side_effect=_boom)
        response = _response("全文")

        summary = await extractor._summarize(_messages(), response)
        assert summary == ""

        await extractor.extract_and_store(
            task_type="summary",
            task_id="summary-daily",
            run_id="run-1",
            messages=_messages(),
            response=response,
            outcome="success",
            tokens_used=150,
            record_ids=[1],
        )

        repo.store_and_mark.assert_called_once()
        entry: MemoryEntry = repo.store_and_mark.call_args.args[0]
        assert entry.summary == ""
        assert entry.full_text == "全文"
        assert entry.outcome == "summary_failed"
        assert entry.tokens_used == 150


# ── 写入失败不影响调用方 ─────────────────────────────────────────────


class TestFailurePropagation:
    @pytest.mark.asyncio
    async def test_repo_failure_propagates_to_caller(self):
        """DB 写入异常向上传播（execute_job 外层 try/except 处理）。"""
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
