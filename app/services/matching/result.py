"""管道最终产物

编排器据此决定后续动作（标记 bgm / 失败处理 / 沉淀候选）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.sync_service.match_trace import MatchTrace


@dataclass
class MatchResult:
    """管道最终输出"""

    subject_id: str | None
    bgm_se_id: str | None
    bgm_ep_id: str | None
    bgm_title: str
    is_season_matched_id: bool
    trace: MatchTrace
    failure_detail: str = ""
    is_ambiguous: bool = False
