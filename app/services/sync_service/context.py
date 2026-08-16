"""执行阶段管道中间产物

承载集数解析 → 跨季回退 → 标记 → 结果结算各 step 间的显式状态传递，
与匹配阶段（app/services/matching/context.py）对称。trace 与匹配阶段共享
同一 MatchTrace 实例，steps 追加进同一 trace.steps。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.sync import CustomItem
from app.services.sync_service.match_trace import MatchTrace


@dataclass
class ExecutionContext:
    """执行阶段管道中间产物，步骤间传递

    编排器注入 item / bgm / trace / service / subject_id 等输入，
    各 step 执行时读写 ctx 字段，最终由编排器按 ctx 终态收尾。
    """

    # 请求输入（编排器注入）
    item: CustomItem
    bgm: Any  # 完整 BangumiApi 实例（标记阶段需要写方法）
    trace: MatchTrace
    service: Any  # SyncService 实例句柄
    actual_source: str
    subject_id: str
    is_season_matched_id: bool

    # 阶段输出：episode 解析
    bgm_se_id: str | None = None
    bgm_ep_id: str | None = None

    # 阶段输出：跨季改选
    cross_season_hit: bool = False
    cross_season_subject_id: str | None = None
    cross_season_ep_id: str | None = None
    cross_season_path: str = ""  # chain / franchise_archive / franchise_online

    # 阶段输出：标记
    mark_status: int | None = None
    result_message: str = ""
    # ResultStep 回填的条目标题（供编排器通知/持久化复用；queued 分支为空由编排器补取）
    bgm_title: str = ""

    # 管线终态（SyncPipeline.run 回填）：(stage, outcome) 或 None（全部执行完）
    terminal: tuple[str, Any] | None = None
