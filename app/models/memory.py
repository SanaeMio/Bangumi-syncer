"""记忆数据模型（共享层）。

从 ``app/services/memory/models`` 迁出，供 core/database 与 services 共同引用，
消除 core → services 的倒置依赖。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryEntry:
    id: int | None = None
    task_type: str = ""
    task_id: str = ""
    run_id: str = ""
    summary: str = ""  # 一行摘要（注入粒度）
    full_text: str = ""  # 本次总结全文（回溯/诊断用，随 prune/归档同生命周期）
    outcome: str = "success"  # 取值：success | partial（用户反馈类取值由 phase3.x 反馈通道引入，见 specs/agent-phase3-summary-enhanced.md）
    tokens_used: int = 0  # 总结调用的 token（response.usage.total_tokens）
    created_at: str = ""

    @classmethod
    def from_row(cls, row) -> MemoryEntry:
        """从 DB row 构造（列序：id, task_type, task_id, run_id, summary, full_text, outcome, tokens_used, created_at）。"""
        return cls(
            id=row[0],
            task_type=row[1],
            task_id=row[2],
            run_id=row[3],
            summary=row[4],
            full_text=row[5],
            outcome=row[6],
            tokens_used=row[7] or 0,
            created_at=row[8],
        )
