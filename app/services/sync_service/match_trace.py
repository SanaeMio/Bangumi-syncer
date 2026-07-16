"""
匹配过程追踪数据结构

用于 debug 模式下记录三段式匹配的完整过程，供"匹配记录"页面和"调试工具"展示。
非 debug 模式下不创建 MatchTrace 对象，零开销。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MatchCandidate:
    """匹配候选条目"""

    subject_id: str
    name: str = ""
    name_cn: str = ""
    score: float = 0.0
    platform: str = ""
    air_date: str = ""
    source: str = ""  # bangumi_data / api_search

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "name": self.name,
            "name_cn": self.name_cn,
            "score": round(self.score, 4),
            "platform": self.platform,
            "air_date": self.air_date,
            "source": self.source,
        }


@dataclass
class MatchStep:
    """单阶段匹配步骤"""

    stage: str  # custom_mapping / bangumi_data / api_search
    status: str  # hit / miss / skipped / error
    subject_id: str | None = None
    score: float | None = None
    reason: str = ""
    candidates: list[MatchCandidate] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "subject_id": self.subject_id,
            "score": round(self.score, 4) if self.score is not None else None,
            "reason": self.reason,
            "candidates": [c.to_dict() for c in self.candidates],
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class MatchTrace:
    """完整匹配过程追踪

    在 debug 模式下由 _find_subject_id 创建并填充，记录三段式匹配的每个阶段。
    匹配完成后写入 sync_records.match_trace（JSON）并返回给调用方。
    """

    request_title: str = ""
    request_ori_title: str = ""
    request_season: int = 1
    request_platform_hint: str = ""
    normalized_title: str = ""
    steps: list[MatchStep] = field(default_factory=list)
    final_subject_id: str | None = None
    final_match_method: str = ""  # custom_mapping / bangumi_data / api_search / failed
    final_score: float | None = None

    # 内部计时
    _current_step: MatchStep | None = field(default=None, repr=False)
    _step_start: float = field(default=0.0, repr=False)

    def start_step(self, stage: str) -> MatchStep:
        """开始一个新匹配阶段"""
        self._finish_current_step()
        step = MatchStep(stage=stage, status="miss")
        self._current_step = step
        self._step_start = time.perf_counter()
        return step

    def _finish_current_step(self) -> None:
        """完成当前阶段，记录耗时"""
        if self._current_step is None:
            return
        if self._step_start > 0:
            self._current_step.elapsed_ms = int(
                (time.perf_counter() - self._step_start) * 1000
            )
        self.steps.append(self._current_step)
        self._current_step = None
        self._step_start = 0.0

    def finish(self) -> None:
        """完成整个匹配过程"""
        self._finish_current_step()
        if self.final_subject_id is None:
            self.final_match_method = "failed"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 JSON 存储/传输）"""
        # 确保最后一步已收尾
        self._finish_current_step()
        return {
            "request_title": self.request_title,
            "request_ori_title": self.request_ori_title,
            "request_season": self.request_season,
            "request_platform_hint": self.request_platform_hint,
            "normalized_title": self.normalized_title,
            "steps": [s.to_dict() for s in self.steps],
            "final_subject_id": self.final_subject_id,
            "final_match_method": self.final_match_method,
            "final_score": round(self.final_score, 4)
            if self.final_score is not None
            else None,
        }
