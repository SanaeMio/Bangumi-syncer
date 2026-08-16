"""执行阶段步骤抽象基类

与匹配阶段（app/services/matching/steps/base.py）的区别：
- 匹配阶段 step 契约：不做 IO（不写 DB / 不发通知 / 不创建 bgm）
- 执行阶段 step 允许副作用（标记、入队等必须调用 bgm 写方法与队列 DB 写入），
  但推荐把通知 / 持久化 / 收藏归档等收口到编排器，step 内只保留不可分割的
  调用链（如 _retry_mark_episode 内部的入队）。

step 的执行模式（结果链）：
- 输入：execute(ctx, prev) 的 prev 为上游产物（SyncPipeline 传入的当前有效
  产物），跨 step 结果不经 ctx 字段中转
- 产出：outcome.outputs 由 SyncPipeline 统一 merge 进结果链
  （ctx.current_outputs / ctx.step_outputs），step 不直接写 ctx 输出字段
- trace.step 的记录（含进入参数 inputs / 产出 outputs / 耗时）由
  SyncPipeline._record_trace 统一完成，step 不直接操作 trace
"""

from __future__ import annotations

from typing import Any

from app.services.matching.steps.base import StepOutcome
from app.services.sync_service.context import ExecutionContext


class ExecutionStepBase:
    """执行阶段步骤抽象基类

    子类需定义 stage 类属性并实现 execute 方法。
    stage 取值：episode_resolve / cross_season / sync_action / result。
    """

    stage: str

    def execute(
        self, ctx: ExecutionContext, prev: dict[str, Any] | None = None
    ) -> StepOutcome:
        """执行该步骤，返回结果。子类必须实现。

        prev：上游产物（当前有效产物，可能为空 dict 表示无前置产物）。
        """
        raise NotImplementedError
