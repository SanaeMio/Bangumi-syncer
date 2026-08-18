"""匹配管道

将 SyncService 中散落的匹配逻辑抽取为独立的可步骤化管道。
请求处理与匹配分离，每步记录做了什么，最终输出明确产物传给标记 bgm。

阶段一：建立数据结构（MatchContext / MatchResult / MatchStepBase / MatchPipeline 骨架）
阶段二：bgm_search 拆 4 个子 step
阶段三：_find_subject_id 三段式拆 5 个 Step 类
阶段四：编排器收敛 + 剥离耦合点
阶段五：archive 短路独立成 step

详见 docs/development/matching-pipeline-refactor.md
"""

from app.services.matching.context import MatchContext
from app.services.matching.pipeline import MatchPipeline
from app.services.matching.result import MatchResult
from app.services.matching.steps.base import MatchStepBase, StepOutcome

__all__ = [
    "MatchContext",
    "MatchPipeline",
    "MatchResult",
    "MatchStepBase",
    "StepOutcome",
]
