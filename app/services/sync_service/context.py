"""执行阶段管道中间产物

承载集数解析 → 跨季回退 → 标记 → 结果结算各 step 间的结果链传递。
与匹配阶段（app/services/matching/context.py）对称：匹配阶段是"任一命中
即终止 + 多 step 竞争写同一字段"的共享黑板（ctx 字段合理）；执行阶段是
线性链，产物由 SyncPipeline 统一维护——step 只"读上游（prev/current）、
产出 outputs"，不直接写 ctx 输出字段。

- step_outputs：stage → 该步本次执行的 outputs（线性管线每步恰好执行一次，
  无需按次保留；重复执行时覆盖）
- current_outputs：合并后的当前有效产物（后一步 outputs 同键覆盖前一步，
  跨季改选即依赖此语义；空值不覆盖）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.sync import CustomItem
from app.services.sync_service.match_trace import MatchTrace


@dataclass
class ExecutionContext:
    """执行阶段管道中间产物，步骤间传递

    编排器注入 item / bgm / trace / service / subject_id 等输入，
    step 从上游产物（SyncPipeline 传入的 prev / ctx.step_outputs）读取输入，
    产出经 outcome.outputs 由管线统一回填到结果链，最终编排器按
    ctx.current_outputs / ctx.terminal 收尾。
    """

    # 请求输入（编排器注入）
    item: CustomItem
    bgm: Any  # 完整 BangumiApi 实例（标记阶段需要写方法）
    trace: MatchTrace
    service: Any  # SyncService 实例句柄
    actual_source: str
    subject_id: str  # 匹配阶段产物（来自 MatchPipeline）
    is_season_matched_id: bool

    # 结果链（SyncPipeline 统一回填，step 只读不写）
    step_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_outputs: dict[str, Any] = field(default_factory=dict)

    # 管线终态（SyncPipeline.run 回填）：(stage, outcome) 或 None（全部执行完）
    terminal: tuple[str, Any] | None = None
