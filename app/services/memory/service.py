"""记忆服务层：统一包装记忆操作，业务层只调此入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.database.agent_memory import AgentMemoryRepository
from app.models.memory import MemoryEntry
from app.services.llm.models import ChatResponse

from .extractor import MemoryExtractor
from .retriever import MemoryRetriever

if TYPE_CHECKING:
    # 仅类型注解用；运行时导入会经 summary 包 __init__ 回到 memory.service 形成环
    from app.services.summary.models import SummaryRecord


class MemoryService:
    """记忆服务层：统一包装记忆操作，业务层只调此入口。

    2.0.1/2.0.2 的底层能力（extract_and_store / retrieve）与 2.0.3 的
    清理能力（rename_task / clear_task）统一经此暴露。
    （mark_consumed 已折叠进 extract_and_store → store_and_mark，非独立入口）
    """

    def __init__(self, memory_repo: AgentMemoryRepository):
        # 消费标记（sync_records 表）的写/清经共享 connection 在 store_and_mark /
        # clear_task 事务内穿透式访问，不注入 sync repo（见 hy-review20260817 #7）。
        self._memory = memory_repo
        self._extractor = MemoryExtractor(memory_repo)
        self._retriever = MemoryRetriever(memory_repo)

    # ------------------------------------------------------------------
    # 写入 / 读取（2.0.1/2.0.2 能力收口）
    # ------------------------------------------------------------------

    async def extract_and_store(
        self,
        task_type: str,
        task_id: str,
        run_id: str,
        messages,
        response: ChatResponse,
        outcome: str,
        tokens_used: int,
        record_ids: list[int],
        job_name: str | None = None,
    ) -> None:
        """总结执行成功后提炼摘要并写入记忆（含消费标记）。

        job_name 透传给摘要 LLM 调用：摘要 token 在 llm_usage 中与主调用
        归属同一任务，用量统计口径完整。
        """
        await self._extractor.extract_and_store(
            task_type=task_type,
            task_id=task_id,
            run_id=run_id,
            messages=messages,
            response=response,
            outcome=outcome,
            tokens_used=tokens_used,
            record_ids=record_ids,
            job_name=job_name,
        )

    def recent(self, task_type: str, task_id: str, limit: int = 5) -> list[MemoryEntry]:
        """最近 N 条同任务摘要（通用，不做业务合并）。"""
        return self._retriever.recent(task_type, task_id, limit=limit)

    def get_task_run_ids(self, task_type: str, task_id: str) -> set[str]:
        """本任务全部 run_id 集合（含归档）。

        供消费排除按任务隔离——判断记录的 consumed_run_ids 是否与当前任务
        的 run_id 集合有交集。
        """
        return self._memory.get_task_run_ids(task_type, task_id)

    def related(
        self,
        task_type: str,
        task_id: str,
        titles: list[str],
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """同剧关联摘要（含归档冷层，日期倒序）。"""
        return self._retriever.related(task_type, task_id, titles, limit=limit)

    def retrieve(
        self,
        task_type: str,
        task_id: str,
        limit: int = 5,
        keywords: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """[deprecated] 旧组合检索（recent + FTS keywords），业务层不再调用。"""
        return self._retriever.retrieve(
            task_type=task_type, task_id=task_id, limit=limit, keywords=keywords
        )

    def format_memory_context(self, entries: list[MemoryEntry]) -> str:
        return self._retriever.format_memory_context(entries)

    def find_overlaps(self, records: list[SummaryRecord]) -> list[SummaryRecord]:
        return self._retriever.find_overlaps(records)

    # ------------------------------------------------------------------
    # 清理与重置（2.0.3）
    # ------------------------------------------------------------------

    def rename_task(self, task_type: str, old_task_id: str, new_task_id: str) -> int:
        """改名迁移：同一事务 UPDATE 主表 + 归档表的 task_id（记忆跟随任务）。"""
        return self._memory.rename_task(task_type, old_task_id, new_task_id)

    def clear_task(self, task_type: str, task_id: str) -> int:
        """显式清空（不可恢复，调用方二次确认）：记忆 + 归档 + 消费标记原子清空

        （不筛 outcome，全部删除；想保留偏好重新开始的场景用"复制新 job"）。
        """
        return self._memory.clear_task(task_type, task_id)
