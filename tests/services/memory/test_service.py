"""MemoryService 测试（Phase 2.0.3 统一入口）。

覆盖：rename_task / clear_task 委托（C1/C3 语义）+ 读写能力收口。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.memory import MemoryEntry
from app.services.llm.models import ChatResponse
from app.services.memory.service import MemoryService
from app.services.summary.models import SummaryRecord


def _make_db(temp_dir, name="svc.db"):
    db_path = temp_dir / name
    with patch("app.core.database.logger"):
        from app.core.database import DatabaseManager

        return DatabaseManager(str(db_path))


def _make_service(db) -> MemoryService:
    return MemoryService(db.memory)


class TestRenameTask:
    def test_rename_migrates_memory(self, temp_dir, reset_singletons):
        """C1：经 MemoryService 改名迁移记忆（主表）。"""
        db = _make_db(temp_dir)
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-daily",
                run_id="run-1",
                summary="s",
            ),
            [],
        )
        svc = _make_service(db)

        n = svc.rename_task("summary", "summary-daily", "summary-daily2")

        assert n == 1
        rows = db.memory.get_recent("summary", "summary-daily2")
        assert [r.run_id for r in rows] == ["run-1"]
        assert db.memory.get_recent("summary", "summary-daily") == []


class TestClearTask:
    def test_clear_removes_memory_and_marks(self, temp_dir, reset_singletons):
        """C3：经 MemoryService 清空记忆 + 消费标记。"""
        db = _make_db(temp_dir)
        r1 = db.log_sync_record(
            "dad",
            "芙莉莲",
            "",
            1,
            1,
            status="success",
            source="plex",
            bgm_title="芙莉莲",
        )
        db.memory.store_and_mark(
            MemoryEntry(
                task_type="summary",
                task_id="summary-daily",
                run_id="run-1",
                summary="s",
            ),
            [r1],
        )
        svc = _make_service(db)

        n = svc.clear_task("summary", "summary-daily")

        assert n == 2  # 1 记忆 + 1 消费标记
        recs = db.get_records_in_date_range(
            "2000-01-01", "2100-01-01", include_consumed=True
        )
        assert recs[0]["consumed_run_ids"] == set()

    def test_clear_idempotent(self, temp_dir, reset_singletons):
        db = _make_db(temp_dir)
        svc = _make_service(db)
        assert svc.clear_task("summary", "summary-daily") == 0


class TestReadWriteDelegation:
    @pytest.mark.asyncio
    async def test_extract_and_store_delegates(self):
        repo = MagicMock()
        svc = MemoryService(repo)
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=ChatResponse(content="一句话", model="m"))
        svc._extractor._llm = llm

        await svc.extract_and_store(
            task_type="summary",
            task_id="summary-daily",
            run_id="run-1",
            messages=[],
            response=ChatResponse(content="全文"),
            outcome="success",
            tokens_used=10,
            record_ids=[1],
        )

        repo.store_and_mark.assert_called_once()

    def test_retrieve_delegates(self):
        repo = MagicMock()
        repo.get_recent.return_value = [MemoryEntry(run_id="run-1", summary="s")]
        svc = MemoryService(repo)

        entries = svc.retrieve("summary", "summary-daily", limit=3, keywords=["芙莉莲"])

        repo.get_recent.assert_called_once_with("summary", "summary-daily", limit=3)
        assert [e.run_id for e in entries] == ["run-1"]

    def test_format_memory_context(self):
        svc = MemoryService(MagicMock())
        text = svc.format_memory_context(
            [MemoryEntry(run_id="a", summary="看了芙莉莲")]
        )
        assert text == "- 看了芙莉莲"

    def test_find_overlaps(self):
        svc = MemoryService(MagicMock())
        records = [
            SummaryRecord(
                id=1,
                timestamp="t",
                user_name="u",
                title="t",
                bgm_title="b",
                season=1,
                episode=1,
                media_type="episode",
                source="s",
                status="success",
                consumed_run_ids={"run-1"},
            ),
            SummaryRecord(
                id=2,
                timestamp="t",
                user_name="u",
                title="t",
                bgm_title="b",
                season=1,
                episode=2,
                media_type="episode",
                source="s",
                status="success",
                consumed_run_ids=set(),
            ),
        ]
        assert [r.id for r in svc.find_overlaps(records)] == [1]
