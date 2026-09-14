"""记忆读取器：通用检索方法（业务合并/去重/排序在 SummaryService 层）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.database.agent_memory import AgentMemoryRepository
from app.models.memory import MemoryEntry

if TYPE_CHECKING:
    # 仅类型注解用；运行时导入会经 summary 包 __init__ 回到 memory.retriever 形成环
    from app.services.summary.models import SummaryRecord


class MemoryRetriever:
    def __init__(self, repo: AgentMemoryRepository):
        self._repo = repo

    # ------------------------------------------------------------------
    # 通用检索（业务层组合调用：summary service 自行合并/去重/排序）
    # ------------------------------------------------------------------

    def recent(self, task_type: str, task_id: str, limit: int = 5) -> list[MemoryEntry]:
        """最近 N 次同任务执行摘要（连续性）。"""
        return self._repo.get_recent(task_type, task_id, limit=limit)

    def related(
        self,
        task_type: str,
        task_id: str,
        titles: list[str],
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """同剧关联：按剧名反查历史总结（含归档冷层，日期倒序）。"""
        return self._repo.get_related_titles(task_type, task_id, titles, limit=limit)

    # ------------------------------------------------------------------
    # 旧组合检索（deprecated）
    # ------------------------------------------------------------------

    def retrieve(
        self,
        task_type: str,
        task_id: str,
        limit: int = 5,
        keywords: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """[deprecated] 旧组合检索：recent + FTS keywords。

        FTS 检索已停用（相关回忆由 related 联表承担，见
        specs/agent-phase2-closeout.md §FTS 停用决议）；本方法仅保留
        keywords/FTS 通路供后续 Phase 5 混合检索评估，业务层不再调用。
        """
        entries: list[MemoryEntry] = []

        # 1. 最近 N 次同任务执行
        entries.extend(self._repo.get_recent(task_type, task_id, limit=limit))

        # 2. 关键词搜索（FTS5，task_type 过滤；命中不占 limit 额度）
        if keywords:
            clean = [k for k in keywords if k and k.strip()]  # 过滤空字符串
            if clean:
                entries.extend(
                    self._repo.search_fts(clean, task_type=task_type, limit=limit)
                )

        # 3. 去重（按 run_id，防双路径命中）；keywords 命中不占额度不收束
        return self._deduplicate_and_rank(entries)

    def _deduplicate_and_rank(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        """按 run_id 去重，保留顺序（recent 在前、keywords 命中随后）。

        recent 条数已在 get_recent(limit) 源头受限；keywords 命中不占额度
        全部保留（phase3.x 反馈通道的优先排序也在此扩展）。
        """
        seen: set[str] = set()
        ranked: list[MemoryEntry] = []
        for e in entries:
            if e.run_id in seen:
                continue
            seen.add(e.run_id)
            ranked.append(e)
        return ranked

    def format_memory_context(self, entries: list[MemoryEntry]) -> str:
        """MemoryEntry 列表 → 注入文本（每条一行）。

        phase3.x 引入用户反馈后，此处增加 `[用户反馈]` 前缀标记
        （见 specs/agent-phase3-summary-enhanced.md）。
        """
        return "\n".join(f"- {e.summary}" for e in entries)

    def find_overlaps(self, records: list[SummaryRecord]) -> list[SummaryRecord]:
        """[deprecated] 返回已被任意任务消费的记录（consumed_run_ids 非空）。

        v7 起业务方改为硬排除（execute_job 内过滤），本方法保留为通用工具；
        overlap 软标注流程已移除（见 closeout §4 E3）。
        """
        return [r for r in records if r.consumed_run_ids]
