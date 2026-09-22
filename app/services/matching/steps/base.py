"""步骤抽象基类

每个 step 职责单一：接收 ctx，执行匹配操作，返回 outcome。
不做 IO（不写 DB / 不发通知 / 不创建 bgm）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.matching.context import MatchContext
from app.services.sync_service.match_trace import MatchCandidate


@dataclass(frozen=True)
class Gate:
    """声明式执行门：满足条件时跳过该 step。

    为什么单独抽出来：此前「本步要不要执行」散落在各 step 的 ``execute`` 开头，
    以裸 ``if`` + ``return StepOutcome(status="skipped", ...)`` 表达，共 7 处。
    后果是「有哪些门」无法枚举，只能逐个读 execute；门的判定也无法单独测试。

    现在每个 step 用 ``gates`` 类属性声明自己的门，管线在 ``execute`` 前统一评估。
    这**不改变控制流** —— ``status="skipped"`` 在两条管线里都不终止、也未被
    任何逻辑分支消费（仅用于 trace 展示），因此改变门的产生方式不影响匹配行为。

    Attributes:
        name: 稳定标识，写入 trace step 的 ``gate`` 字段，供前端/排查区分「因何跳过」
        describe: 人类可读的跳过原因（写入 ``StepOutcome.reason``）
        check: ``(ctx, prev) -> bool``，返回 True 表示**应跳过**本步。
            接收 ctx 与上游产物 prev（匹配管线无 prev，传 None），
            故既能表达配置开关，也能表达「上游已满足」这类依赖前序结果的判定。
    """

    name: str
    describe: str
    check: Callable[[Any, Any], bool]

    def evaluate(self, ctx: Any, prev: Any = None) -> bool:
        """返回是否应跳过；check 抛异常时视为**不跳过**（保守：宁可执行）。

        保守取向的理由：门是优化/护栏，不是正确性前提。判定函数出错时若误判为
        「跳过」，会静默丢掉一次可能的匹配；若误判为「不跳过」，最坏只是多跑一次
        本来会跳过的逻辑。故异常一律放行。
        """
        try:
            return bool(self.check(ctx, prev))
        except Exception:  # noqa: BLE001 — 门判定失败不阻断主流程
            return False


def evaluate_gates(gates: tuple[Gate, ...], ctx: Any, prev: Any = None) -> Gate | None:
    """按声明顺序评估门，返回首个命中的门（None = 全部未命中，应正常执行）"""
    for gate in gates:
        if gate.evaluate(ctx, prev):
            return gate
    return None


def skipped_outcome(gate: Gate, /, **extra: Any) -> StepOutcome:
    """由门生成标准的 skipped outcome（自动带上 gate 标识，供 trace 区分原因）

    ``gate`` 是**仅位置**参数（``/``），因此 ``skipped_outcome(g, gate=...)``
    不会与之冲突 —— 该关键字会落进 ``extra`` 并被剔除，最终仍以 ``gate.name``
    为准。这样调用方即使多传也不会报错。
    """
    extra.pop("gate", None)
    return StepOutcome(status="skipped", reason=gate.describe, gate=gate.name, **extra)


@dataclass
class StepOutcome:
    """单个 step 的执行结果

    管道据此填充 trace 并决定是否终止。

    ⚠️ 两条管线对 ``status`` 的判定**不完全一致**（这是刻意的，勿"顺手统一"）：

    - ``MatchPipeline``：**只看** ``is_terminal``（``if outcome.is_terminal: break``）
    - ``SyncPipeline``：``is_terminal`` **或** ``status == "error"`` 均终止

    因此 ``status="error"`` 且 ``is_terminal=False`` 的含义是「本步出错但可降级」：
    匹配管线会继续跑后续 step（如 archive 短路异常后降级走 API 搜索），
    而执行管线会立即终止。**执行管线的 step 若返回 error 想继续，必须显式
    设 is_terminal=False 并确认不会被 SyncPipeline 拦截** —— 但注意执行管线
    含不可逆副作用（写 Bangumi），通常不应该继续。

    新增返回 ``status="error"`` 的 step 时，请显式写出 ``is_terminal``，
    不要依赖默认值 —— 默认 False 在两条管线里行为不同。
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
    # 结构化进出产物：本 step 执行时读入的输入 / 产出的输出，
    # 前端按 inputs/outputs 分组以表格展示（"进去了什么，出来了什么"）。
    # 与 processed_payload 的关系：processed_payload 为自由格式载荷
    # （receive 的原始字段 / episode_resolve 的 input_*+output_* 打平键），
    # inputs/outputs 为规范化的输入输出分组，二者可并存。
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    # stage 覆盖：APISearchStep archive 短路命中时，trace.step.stage 应标记为 "archive"
    # 而非 step.stage="api_search"，使归档匹配在同步记录详情中可见。
    # 其他场景为 None，trace.step.stage 取 step.stage。
    stage_override: str | None = None
    # 所属父 step 的 stage（None = 顶层步骤），写入 trace.step.parent。
    # 用于把「子步骤」与顶层步骤区分开：bgm_search 的 4 个子 step 与改选步骤
    # 都挂在 parent="api_search" 下，避免与主步骤平铺混淆。
    parent: str | None = None
    # 触发跳过（status="skipped"）的门标识，写入 trace.step.gate。
    # 仅由管线评估声明式门时填充；step 内联跳过的场景为 None。
    gate: str | None = None


class MatchStepBase:
    """匹配步骤抽象基类

    子类需定义 stage 类属性并实现 execute 方法。

    可选：用 ``gates`` 声明本步的执行门（见 ``Gate``）。匹配管线会在
    ``execute`` 前统一评估，命中即产出标准 skipped outcome，不改变控制流。
    """

    stage: str  # receive / normalize / custom_mapping / ...
    # 执行门（声明顺序即评估顺序；空表示无门）
    gates: tuple[Gate, ...] = ()

    def execute(self, ctx: MatchContext) -> StepOutcome:
        """执行该步骤，返回结果。子类必须实现。"""
        raise NotImplementedError
