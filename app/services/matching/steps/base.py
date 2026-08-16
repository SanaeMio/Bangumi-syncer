"""步骤抽象基类

每个 step 职责单一：接收 ctx，执行匹配操作，返回 outcome。
不做 IO（不写 DB / 不发通知 / 不创建 bgm）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.matching.context import MatchContext
from app.services.sync_service.match_trace import MatchCandidate


@dataclass
class StepOutcome:
    """单个 step 的执行结果

    管道据此填充 trace 并决定是否终止。
    """

    status: str  # hit / miss / skipped / error / low_confidence
    subject_id: str | None = None
    reason: str = ""
    score: float | None = None
    candidates: list[MatchCandidate] = field(default_factory=list)
    is_terminal: bool = False  # 命中即终止 / 失败终止
    # trace 扩展字段（按需填充，对应 MatchStep 同名字段）
    processed_payload: dict[str, Any] = field(default_factory=dict)
    request_params: dict[str, Any] = field(default_factory=dict)
    api_response_summary: dict[str, Any] = field(default_factory=dict)
    error_detail: dict[str, Any] = field(default_factory=dict)
    # stage 覆盖：APISearchStep archive 短路命中时，trace.step.stage 应标记为 "archive"
    # 而非 step.stage="api_search"，使归档匹配在同步记录详情中可见。
    # 其他场景为 None，trace.step.stage 取 step.stage。
    stage_override: str | None = None


class MatchStepBase:
    """匹配步骤抽象基类

    子类需定义 stage 类属性并实现 execute 方法。
    """

    stage: str  # receive / normalize / custom_mapping / ...

    def execute(self, ctx: MatchContext) -> StepOutcome:
        """执行该步骤，返回结果。子类必须实现。"""
        raise NotImplementedError
