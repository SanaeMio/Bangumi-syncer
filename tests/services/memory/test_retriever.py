"""MemoryRetriever 测试（Phase 2.0.2 读取侧）。

覆盖 BDD 场景 R2/R3/R5/R9/D2/D5 与 format_memory_context。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.models.memory import MemoryEntry
from app.services.memory.retriever import MemoryRetriever
from app.services.summary.models import SummaryRecord


def _entry(run_id: str, summary: str = "昨日看了芙莉莲") -> MemoryEntry:
    return MemoryEntry(
        task_type="summary", task_id="summary-daily", run_id=run_id, summary=summary
    )


def _record(consumed_run_ids: set[str] | None = None, **overrides) -> SummaryRecord:
    defaults = {
        "id": 1,
        "timestamp": "2026-08-13 20:00:00",
        "user_name": "dad",
        "title": "葬送的芙莉莲",
        "bgm_title": "葬送的芙莉莲",
        "season": 1,
        "episode": 10,
        "media_type": "episode",
        "source": "plex",
        "status": "success",
        "consumed_run_ids": consumed_run_ids or set(),
    }
    defaults.update(overrides)
    return SummaryRecord(**defaults)


def _make_retriever(**repo_mocks) -> tuple[MemoryRetriever, MagicMock]:
    repo = MagicMock()
    for k, v in repo_mocks.items():
        setattr(repo, k, MagicMock(return_value=v))
    return MemoryRetriever(repo), repo


# ── R2 task_type 过滤 / R3 双路径去重 / R9 不占额度 / R5 空关键词 ──────────


class TestRetrieve:
    def test_search_fts_called_with_task_type_filter(self):
        """R2：FTS 检索必须带 task_type 过滤（跨任务不污染上下文）。"""
        retriever, repo = _make_retriever(get_recent=[], search_fts=[])

        retriever.retrieve("summary", "summary-daily", keywords=["芙莉莲"])

        repo.search_fts.assert_called_once_with(
            ["芙莉莲"], task_type="summary", limit=5
        )

    def test_multi_word_keyword_passed_as_single_phrase(self):
        """#4 修复：带空格的标题作为整体短语传递（不被空白切碎）。"""
        retriever, repo = _make_retriever(get_recent=[], search_fts=[])

        retriever.retrieve(
            "summary", "summary-daily", keywords=["Spy x Family", "芙莉莲"]
        )

        repo.search_fts.assert_called_once_with(
            ["Spy x Family", "芙莉莲"], task_type="summary", limit=5
        )

    def test_deduplicate_by_run_id(self):
        """R3：同记忆双路径命中（recent + keywords）只注入一次，recent 优先。"""
        retriever, _ = _make_retriever(
            get_recent=[_entry("run-a"), _entry("run-b")],
            search_fts=[_entry("run-b"), _entry("run-c")],
        )

        result = retriever.retrieve("summary", "summary-daily", keywords=["x"])

        assert [e.run_id for e in result] == ["run-a", "run-b", "run-c"]

    def test_keywords_hits_not_capped_by_limit(self):
        """R9：keywords 命中不占 memory_limit 额度（recent 5 + 命中 3 = 8）。"""
        retriever, _ = _make_retriever(
            get_recent=[_entry(f"r{i}") for i in range(5)],
            search_fts=[_entry(f"k{i}") for i in range(3)],
        )

        result = retriever.retrieve("summary", "summary-daily", limit=5, keywords=["x"])

        assert len(result) == 8

    def test_empty_keywords_skips_fts(self):
        """R5：keywords 含空字符串/None → 过滤掉，FTS 不查空串。"""
        retriever, repo = _make_retriever(get_recent=[], search_fts=[])

        result = retriever.retrieve(
            "summary", "summary-daily", keywords=["", None, "  "]
        )

        assert result == []
        repo.search_fts.assert_not_called()

    def test_no_keywords_skips_fts(self):
        retriever, repo = _make_retriever(get_recent=[_entry("run-a")])

        result = retriever.retrieve("summary", "summary-daily")

        assert [e.run_id for e in result] == ["run-a"]
        repo.search_fts.assert_not_called()

    def test_recent_capped_by_limit(self):
        """recent 路径受 memory_limit 约束（limit 传给 repo）。"""
        retriever, repo = _make_retriever(get_recent=[_entry("r1"), _entry("r2")])

        result = retriever.retrieve("summary", "summary-daily", limit=2)

        repo.get_recent.assert_called_once_with("summary", "summary-daily", limit=2)
        assert len(result) == 2


# ── format_memory_context ──────────────────────────────────────────────


class TestFormatMemoryContext:
    def test_each_entry_one_line(self):
        retriever, _ = _make_retriever()
        text = retriever.format_memory_context(
            [_entry("a", "看了芙莉莲"), _entry("b", "用户反馈：不要太啰嗦")]
        )
        assert text == "- 看了芙莉莲\n- 用户反馈：不要太啰嗦"

    def test_empty_entries(self):
        retriever, _ = _make_retriever()
        assert retriever.format_memory_context([]) == ""


# ── D2 重叠识别 / D5 无窗口限制 ────────────────────────────────────────


class TestFindOverlaps:
    def test_returns_only_consumed_records(self):
        """D2：只返回 consumed_run_ids 非空的记录。"""
        retriever, _ = _make_retriever()
        records = [
            _record(consumed_run_ids={"run-1"}),
            _record(consumed_run_ids=set(), id=2),
            _record(consumed_run_ids={"run-3"}, id=3),
        ]

        overlaps = retriever.find_overlaps(records)

        assert [r.id for r in overlaps] == [1, 3]

    def test_any_old_consumed_record_hits(self):
        """D5：无窗口限制——任意久远被消费都命中（精确到集）。"""
        retriever, _ = _make_retriever()
        records = [_record(consumed_run_ids={"very-old-run-id"}, id=99)]

        overlaps = retriever.find_overlaps(records)

        assert [r.id for r in overlaps] == [99]

    def test_all_new_records_no_overlap(self):
        retriever, _ = _make_retriever()
        records = [
            _record(consumed_run_ids=set()),
            _record(consumed_run_ids=set(), id=2),
        ]

        assert retriever.find_overlaps(records) == []


class TestGenericMethods:
    """通用方法 recent/related（retrieve 保留 deprecated，业务合并上移 service）。"""

    def test_recent_delegates_to_repo(self):
        retriever, repo = _make_retriever()
        repo.get_recent.return_value = [_entry("run-1")]
        assert retriever.recent("summary", "summary-daily", limit=3) == [
            _entry("run-1")
        ]
        repo.get_recent.assert_called_once_with("summary", "summary-daily", limit=3)

    def test_related_delegates_to_repo(self):
        retriever, repo = _make_retriever()
        repo.get_related_titles.return_value = [_entry("run-9")]
        assert retriever.related(
            "summary", "summary-daily", ["葬送的芙莉莲"], limit=2
        ) == [_entry("run-9")]
        repo.get_related_titles.assert_called_once_with(
            "summary", "summary-daily", ["葬送的芙莉莲"], limit=2
        )
