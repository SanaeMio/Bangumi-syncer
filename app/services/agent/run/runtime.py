"""通用 Agent 运行时：run 编排与恢复续跑状态机。

与业务无关：全部场景差异通过 :class:`~app.services.agent.run.scenario.ScenarioHooks`
注入，运行时只透传 ``ctx``（场景上下文）给场景钩子，不感知其内部结构。

对外入口：

- :func:`run`：原子抢占 → 工具注册 → 构建 seed → 通用循环 → 终局处理。
- :func:`continue_run`：崩溃恢复续跑单一入口（replay → 分派 → 补执行 →
  续跑 → 终局处理）；异常在函数内部消化，不向调用方抛出。

失败语义（与既有行为一致）：

- ``LLMCallError(retryable=False)`` → ``mark_failed(stop_reason='llm_error')``
- ``LLMCallError(retryable=True)`` → ``increment_attempts``（达上限由仓储单点置 failed）
- 其他异常 → error 日志 + ``increment_attempts``
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence

from app.core.database import get_database_manager
from app.core.database.agent_runs import MAX_RUN_ATTEMPTS
from app.core.logging import logger
from app.services.agent.execution.streaming import StreamingToolExecutor
from app.services.agent.execution.tools import (
    ToolRegistry,
)
from app.services.agent.loop import (
    RunResult,
    StreamFn,
    normalize_stop_reason,
    run_loop,
)
from app.services.agent.observe import trace
from app.services.agent.observe.recorder import TraceRecorder
from app.services.agent.protocols import TerminalCapture, ToolSchema, ToolSpanRecorder
from app.services.agent.run.scenario import CtxT, RunFailure, ScenarioHooks
from app.services.llm.client import LLMCallError
from app.services.llm.models import ChatResponse, Message, ToolResultBlock, ToolUseBlock
from app.services.notification_service import NotificationService


def _emit_run_failed(
    hooks: ScenarioHooks,
    *,
    run_id: str,
    stop_reason: str,
    last_error: str,
    attempts: int = 0,
) -> None:
    """把 run 终态失败交给场景（``hooks.on_run_failed``；未注册则忽略）。

    通知等副作用属场景职责（docs/development/agent-runtime.md）；通用层只在
    失败判定点发射事件。回调异常不影响终态落库（兜底捕获记 warning）。
    """
    callback = hooks.on_run_failed
    if callback is None:
        return
    try:
        callback(
            RunFailure(
                run_id=run_id,
                task_type=hooks.task_type,
                stop_reason=stop_reason,
                last_error=last_error,
                attempts=attempts,
            )
        )
    except Exception as e:
        logger.warning(f"run {run_id} 失败事件回调异常（已忽略）: {e}")


# 非只读（write/terminal/未注册）缺失工具的占位 tool_result 文案
# ——不重放副作用，仅闭合会话协议，真实调用由续跑 loop 触发
_SKIP_PLACEHOLDER_CONTENT = "skipped: will be re-invoked in continuation"


async def run(
    run_id: str,
    *,
    hooks: ScenarioHooks[CtxT],
    ctx: CtxT,
    thinking_level: str,
    stream_fn: StreamFn | None = None,
    notification_service: NotificationService | None = None,
    span_recorder: TraceRecorder | None = None,
) -> str:
    """执行一次 Agent 场景任务（通用编排），返回终态 status 字符串。

    status 取值：``succeeded``（含「无建议」这类成功结束）/ ``failed`` / ``processing``
    （调度轮次重试中）/ ``skipped``（并发抢占失败，由调用方忽略）。

    LLM 调用注入：``stream_fn`` 为唯一入口（流式，缺省由 ``hooks.build_stream_fn``
    构造）。
    """
    dbm = get_database_manager()

    # 原子抢占：失败表示已被其它调度器处理
    if not dbm.agent_runs.atomic_claim(run_id):
        return "skipped"

    # per-run ToolRegistry：每个 run 独立实例，handler 闭包只绑定本次
    # ctx（含访问凭据），避免并发入口下互相覆盖导致串账号。
    registry = ToolRegistry()
    defns = hooks.register_tools(registry, ctx)
    tools_schemas = [d.to_schema() for d in defns]
    _assert_terminals_unique(tools_schemas)

    # seed 是本次 run 的**数据**（由场景在 new_ctx 初始化时备好），非行为钩子：
    # 通用层只读取，不在执行期回调场景派生。
    seed = ctx.seed

    if span_recorder is None:
        span_recorder = TraceRecorder(run_id, start_iteration=0)

    # 轮次预算：场景侧读取集中配置（显式覆盖 > thinking_level 策略 > 默认兜底），
    # 由场景保证单一来源。
    max_iterations = hooks.resolve_max_iterations(thinking_level)

    if stream_fn is None:
        stream_fn = hooks.build_stream_fn(thinking_level)

    # 写 seed 行（供 replay 显式提取种子消息）
    span_recorder.write_seed_row(seed)

    # LLM 调用异常（stream_fn 抛错）→ 按可重试性分流
    try:
        result = await _invoke_loop(
            stream_fn=stream_fn,
            registry=registry,
            span_recorder=span_recorder,
            tools_schemas=tools_schemas,
            max_iterations=max_iterations,
            hooks=hooks,
            seed_messages=seed,
        )
    except LLMCallError as e:
        # LLMCallError 携带 retryable 标志区分可重试/确定性失败
        if not e.retryable:
            # 确定性失败（401/403/400/refusal）→ 立即标记 failed，不浪费重试次数
            logger.error(f"[{hooks.task_type}] run {run_id} 确定性 LLM 失败: {e}")
            dbm.agent_runs.mark_failed(
                run_id,
                stop_reason="llm_error",
                last_error=str(e),
                total_tokens=span_recorder.total_tokens,
            )
            _emit_run_failed(
                hooks, run_id=run_id, stop_reason="llm_error", last_error=str(e)
            )
            return "failed"
        # 可重试（429/5xx/超时）→ 累加 attempts；达上限时由仓储在**同一次调用事务内**
        # 单点置终态（status='failed' + last_error），此处不得再 mark_failed（避免双写）。
        logger.error(f"[{hooks.task_type}] run {run_id} 可重试 LLM 失败: {e}")
        attempts = dbm.agent_runs.increment_attempts(run_id, last_error=str(e))
        if attempts >= MAX_RUN_ATTEMPTS:
            _emit_run_failed(
                hooks,
                run_id=run_id,
                stop_reason="llm_error",
                last_error=str(e),
                attempts=attempts,
            )
        return "failed" if attempts >= MAX_RUN_ATTEMPTS else "processing"
    except Exception as e:
        logger.error(f"[{hooks.task_type}] run {run_id} LLM 调用异常: {e}")
        attempts = dbm.agent_runs.increment_attempts(run_id, last_error=str(e))
        return "failed" if attempts >= MAX_RUN_ATTEMPTS else "processing"

    return await hooks.handle_terminal(
        run_id,
        result,
        ctx,
        total_tokens=span_recorder.total_tokens,
        notification_service=notification_service,
    )


def _assert_terminals_unique(tools_schemas: Sequence[ToolSchema]) -> None:
    """终止工具（``access="terminal"``）唯一性 fail-fast（0 个允许：自然收尾）。

    >1 个时末轮"强制定向"无法表达（provider 只支持单一具名），属场景配置错误，
    入口立即报错，避免静默走错分支。
    """
    terminals = [s.name for s in tools_schemas if s.access == "terminal"]
    if len(terminals) > 1:
        raise ValueError(
            f"场景注册了 {len(terminals)} 个终止工具（{', '.join(terminals)}）；"
            '框架要求至多一个（access="terminal"）'
        )


def _make_executor_factory(
    registry: ToolRegistry, span_recorder: ToolSpanRecorder
) -> Callable[[], StreamingToolExecutor]:
    """构造按轮新建 ``StreamingToolExecutor`` 的工厂（流式主路径）。

    - ``run_tool``：统一执行入口（``registry`` 实现 ``ToolRunner``；包装/span/错误
      文本由入口收口）
    - ``batch_execute_fn``：延迟执行复用 ``execute_batch``（分段并行/保序 + span 包裹）
    - ``is_idempotent``：停点是否提前启动的判据
    - ``is_terminal``：access=terminal 判定（terminal 不执行、且抑制后续停点启动）
    - ``on_recorder``：为提前执行的工具落 tool span（延迟执行由 execute_batch 自带）
    """

    def factory() -> StreamingToolExecutor:
        return StreamingToolExecutor(
            run_tool=registry,
            is_idempotent=registry.is_idempotent,
            is_terminal=registry.is_terminal,
            on_recorder=span_recorder,
            batch_execute_fn=functools.partial(
                registry.execute_batch, recorder=span_recorder
            ),
        )

    return factory


async def _invoke_loop(
    *,
    stream_fn: StreamFn,
    registry: ToolRegistry,
    span_recorder: TraceRecorder,
    tools_schemas: list[ToolSchema],
    max_iterations: int,
    hooks: ScenarioHooks[CtxT],
    seed_messages: Sequence[Message],
) -> RunResult:
    """以流式主路径运行通用循环（chat span 由 recorder 提供 + 按轮执行器工厂）。

    ``stream_fn`` 保持**原始**流式入口：聚合与落库在 loop 内经
    ``span_recorder.chat_span()`` 单点完成（不再另有包装层聚合）。
    """
    return await run_loop(
        stream_fn=stream_fn,
        tools_schemas=tools_schemas,
        max_iterations=max_iterations,
        seed_messages=seed_messages,
        recorder=span_recorder,
        # 场景可选软护栏（旧 hooks 无该字段时兼容 None）
        veto_terminal=getattr(hooks, "veto_terminal", None),
        executor_factory=_make_executor_factory(registry, span_recorder),
    )


async def continue_run(
    run_id: str,
    *,
    hooks: ScenarioHooks[CtxT],
    ctx: CtxT,
    notification_service: NotificationService | None = None,
) -> None:
    """恢复续跑单一入口（通用状态机）：replay → 补执行 → 续跑 → 终局处理。

    流程：
    1. 场景钩子解析 thinking_level / max_iterations（集中配置单一来源）
    2. ``trace.replay`` 重建可续跑消息列表与终局响应
    3. ``last_response`` 终局优先分派（**先于**预算耗尽判定）：
       - 收尾轮（``remaining <= 0``，即预算耗尽后 ``loop._final_recovery`` 的
         响应）：含终止工具调用 → 走场景终局处理；否则一律 ``exhausted``
         （响应文本交场景 ``parse_suggestion`` 兜底解析）——**不**按 provider
         原始 stop_reason 归一化，与 ``_final_recovery`` 的二分判据对齐
       - 匹配的终止工具调用（注册表 ``access="terminal"`` 派生）→ 走场景终局处理
       - ``stop_reason`` 为终止工具名但无匹配调用 → 防御降级 exhausted
       - 其余按 ``normalize_stop_reason`` 归一化（与 ``loop.run_loop`` 共用判据）：
         ``end_turn`` → mark_succeeded；``max_tokens`` / 空壳 ``llm_error``
         → 交场景终态处理（llm_assist 落 failed）；``None``（含非终止工具调用）
         → 补执行缺失只读工具 + 回填结果后续跑 loop
       - ``None``（全部轮次已完整记录）→ 若预算耗尽则落终态，否则续跑 loop
    4. ``remaining <= 0`` 且非终局 → 落终态 succeeded(stop_reason='exhausted')
       （否则 run 永久滞留 processing）

    顺序说明：末轮可能已产出 submit 但 run 中断，若先判预算耗尽会误判 exhausted
    丢失提交，故终局语义必须先消费。

    失败分流（与既有语义一致）：
    - ``LLMCallError(retryable=False)`` → ``mark_failed(stop_reason='llm_error')``
    - ``LLMCallError(retryable=True)`` → ``increment_attempts(last_error=...)``
    - 其他异常 → error 日志 + ``increment_attempts(last_error=...)``

    异常在本函数内部消化，不向调用方抛出（调用方仅负责执行权释放与兜底日志）。
    """
    dbm = get_database_manager()
    repo = dbm.agent_runs

    try:
        # 思考强度与轮次预算统一由场景从集中配置读取（非法/非正数覆盖值
        # 由场景配置解析统一告警并回退）
        thinking_level = hooks.resolve_thinking_level()
        max_iterations = hooks.resolve_max_iterations(thinking_level)

        # per-run ToolRegistry 在入口构建一次（注册无副作用）：终止集合的唯一来源
        # （replay 分派与续跑共用），并复用给 _execute_continuation。
        registry = ToolRegistry()
        defns = hooks.register_tools(registry, ctx)
        tools_schemas = [d.to_schema() for d in defns]
        _assert_terminals_unique(tools_schemas)
        terminal_names = {s.name for s in tools_schemas if s.access == "terminal"}

        # seed 由 replay 从 agent_steps 的 seed 行提取，无需此处重建
        replay_result = trace.replay(run_id)
        remaining = max_iterations - replay_result.executed_iterations

        last_response = replay_result.last_response
        if last_response is None:
            # 全部轮次已完整记录 → 预算耗尽则落终态，否则以剩余轮次续跑通用循环
            if remaining <= 0:
                _mark_exhausted(
                    repo,
                    run_id,
                    note="replay 前",
                    total_tokens=replay_result.total_tokens,
                )
                return
            # 中断轮次（replay 在该轮 break）的 llm_chat 行可能仍为 running：
            # 恢复时复用同一 span（不新增同轮重复行），找不到则正常新建。
            reuse_chat_span_id = trace.reopen_interrupted_chat_span(
                run_id, iteration=replay_result.executed_iterations
            )
            await _execute_continuation(
                dbm,
                run_id,
                replay_result,
                thinking_level,
                remaining=remaining,
                hooks=hooks,
                ctx=ctx,
                notification_service=notification_service,
                anchor_replayed_round=bool(replay_result.missing_tool_calls),
                registry=registry,
                tools_schemas=tools_schemas,
                reuse_chat_span_id=reuse_chat_span_id,
            )
            return

        # 终局响应直接分派（**先于**预算耗尽判定），避免无谓重调 LLM 与
        # 末轮已 submit 却被误判 exhausted 丢失提交。
        stop: str = last_response.stop_reason or ""
        tcs = last_response.tool_calls
        submit_tc = next((tc for tc in tcs if tc.name in terminal_names), None)

        # 收尾轮判据：``remaining <= 0`` 表示 max_iterations 个预算轮已全部执行完
        # （见上），此后的响应只能来自 ``loop._final_recovery`` 收尾轮。
        is_final_recovery = remaining <= 0

        # 收尾轮语义还原：live 对该轮只做二分——含终止工具调用 → submit_suggestion；
        # 否则 exhausted（响应文本交场景 ``parse_suggestion`` 兜底解析）。恢复路径
        # 不得按 provider 原始 stop_reason 归一化：否则收尾轮文本建议会误落
        # ``end_turn``（兜底解析丢失）、``max_tokens`` / 空壳还会误落 failed，
        # 终态语义与 live 分叉。
        if is_final_recovery and submit_tc is None:
            await hooks.handle_terminal(
                run_id,
                RunResult(
                    stop_reason="exhausted",
                    # 恢复路径无实时响应对象：构造等价响应承载兜底解析所需文本。
                    last_response=ChatResponse(
                        content=last_response.content,
                        stop_reason=last_response.stop_reason,
                    ),
                ),
                ctx,
                total_tokens=replay_result.total_tokens,
                notification_service=notification_service,
            )
            return

        # 常规轮显式 end_turn 优先视为无建议终态；收尾轮不走该判据——其终止工具
        # 调用优先于 stop_reason 归一化（与 loop._final_recovery 一致）。
        if stop == "end_turn" and not is_final_recovery:
            # 无建议：直接标记，不调 LLM（透传 replay 累计 tokens，口径同其它终态）
            repo.mark_succeeded(
                run_id,
                stop_reason="end_turn",
                total_tokens=replay_result.total_tokens,
            )
            return

        # 捕获终止工具调用（终局）→ 走场景校验落库路径
        if submit_tc is not None:
            sug = submit_tc.input or {}
            result = RunResult(
                stop_reason=submit_tc.name,
                terminal_args=sug,
                last_response=None,
            )
            # 恢复路径不重放 veto（软护栏）——**有意**与 live 路径不同：
            # live 会话在 loop.run_loop 命中终止工具时经 veto_terminal 软护栏（remaining>1
            # 且本 run 未拦过才拦一次），防止模型未核对即提交；但 crash 恢复 replay 到
            # 已记录的 submit 终局时直接落库，不重放该护栏。原因：
            # (a) veto 依赖「已拦一次 / 剩余轮次」会话内状态，该状态在崩溃中不可靠，
            #     重放会引入额外的状态机复杂度与歧义（拦或不拦难有确定依据）；
            # (b) 恢复路径 replay + 续跑成本高，而软护栏本身是 best-effort，重放收益边际；
            # (c) 终局仍统一走 normalize_stop_reason + handle_terminal 归一化落库，
            #     与 live 放行路径的落点一致，保证落库口径不因恢复路径而分叉。
            await hooks.handle_terminal(
                run_id,
                result,
                ctx,
                # 恢复路径无实时 recorder：已发生轮次的 tokens 由 replay 从
                # agent_steps 的 llm_chat span 累计回传（口径见 ReplayResult.total_tokens）。
                total_tokens=replay_result.total_tokens,
                notification_service=notification_service,
            )
            return

        if stop in terminal_names:
            # 防御：stop_reason 声称终局，但 tool_calls 中无匹配的终止工具调用，
            # 无真实提交可消费。不得把空 dict 当作有效建议落库（假阳性），
            # 降级为与预算耗尽一致的 exhausted 终态。
            # 必须先于下方 normalize_stop_reason：其「未知有内容 → end_turn」兜底会把
            # stop_reason=终止工具名的场景误判为 end_turn。
            logger.warning(
                f"🤖 恢复续跑 {run_id} stop_reason={stop} 但 tool_calls 无 "
                f"{stop} 调用，无有效终局，降级 exhausted"
            )
            _mark_exhausted(
                repo,
                run_id,
                note="终局工具缺失",
                total_tokens=replay_result.total_tokens,
            )
            return

        # 终局归一化（与 loop.run_loop 共用判据）：max_tokens 超限、空壳 llm_error、
        # 未知 stop_reason 有内容 → end_turn。置于 submit/终止工具分派之后，
        # 保证真实工具语义优先；返回 None 表示含非终止工具调用，需续跑。
        terminal_reason = normalize_stop_reason(
            stop,
            has_tool_calls=bool(tcs),
            blocks=last_response.blocks,
            content=last_response.content or "",
        )

        if terminal_reason in ("llm_error", "max_tokens"):
            # 与 loop.run_loop 一致：显式失败/超限终态，不再当作「待补执行工具轮」反复
            # 续跑。交场景终态处理（llm_assist 按 stop_reason 落 failed）。
            result = RunResult(stop_reason=terminal_reason, last_response=None)
            await hooks.handle_terminal(
                run_id,
                result,
                ctx,
                total_tokens=replay_result.total_tokens,
                notification_service=notification_service,
            )
            return

        if terminal_reason == "end_turn":
            # 未知 stop_reason 但有内容（无工具调用）→ 兼容旧 provider，落 end_turn：
            # 无建议，不调 LLM（透传 replay 累计 tokens，口径同其它终态）。
            repo.mark_succeeded(
                run_id,
                stop_reason="end_turn",
                total_tokens=replay_result.total_tokens,
            )
            return

        # terminal_reason is None → 含 tool_use（非终局，存在缺失工具）
        # → 补执行 + 回填后继续 loop
        # 该轮 LLM 已发生过，计入预算（remaining 已减）
        remaining = max(0, remaining - 1)
        if remaining <= 0:
            _mark_exhausted(
                repo,
                run_id,
                note="补执行后",
                total_tokens=replay_result.total_tokens,
            )
            return

        await _execute_continuation(
            dbm,
            run_id,
            replay_result,
            thinking_level,
            remaining=remaining,
            hooks=hooks,
            ctx=ctx,
            notification_service=notification_service,
            anchor_replayed_round=True,
            registry=registry,
            tools_schemas=tools_schemas,
        )
    except LLMCallError as e:
        # LLM 调用失败：按可重试性分流
        if not e.retryable:
            logger.error(f"🤖 恢复续跑 {run_id} 确定性 LLM 失败: {e}")
            repo.mark_failed(run_id, stop_reason="llm_error", last_error=str(e))
            _emit_run_failed(
                hooks, run_id=run_id, stop_reason="llm_error", last_error=str(e)
            )
        else:
            logger.error(f"🤖 恢复续跑 {run_id} 可重试 LLM 失败: {e}")
            attempts = repo.increment_attempts(run_id, last_error=str(e))
            if attempts >= MAX_RUN_ATTEMPTS:
                _emit_run_failed(
                    hooks,
                    run_id=run_id,
                    stop_reason="llm_error",
                    last_error=str(e),
                    attempts=attempts,
                )
    except Exception as e:
        # 附带当前 run 状态，便于区分「处理中异常」与「已终态后的异常」
        logger.error(
            f"🤖 恢复续跑 {run_id} 异常（当前 run 状态="
            f"{_current_run_status(repo, run_id)}）: {e}"
        )
        repo.increment_attempts(run_id, last_error=str(e))


def _mark_exhausted(repo, run_id: str, *, note: str, total_tokens: int = 0) -> None:
    """预算耗尽且无终局语义 → 落终态 succeeded(stop_reason='exhausted')。

    不能直接 return（否则 run 永久滞留 processing，下一轮恢复扫描又会重复捞起）。
    ``total_tokens`` 为 replay 累计的历史轮次用量，透传至终态记录（口径与其它终态一致）。
    """
    logger.warning(
        f"🤖 恢复(replay)路径预算耗尽，run {run_id} {note}已无剩余轮次，"
        f"标记 succeeded(stop_reason=exhausted)"
    )
    repo.mark_succeeded(run_id, stop_reason="exhausted", total_tokens=total_tokens)


def _current_run_status(repo, run_id: str) -> str:
    """best-effort 读取 run 当前 status，供最外层异常日志定位。

    读取失败/无记录时返回可读占位（``unknown`` / ``missing``），并记 warning，
    绝不因此抛错掩盖原始异常。
    """
    try:
        row = repo.get_run(run_id)
    except Exception as e:  # best-effort：状态读取失败不遮蔽原始异常
        logger.warning(f"🤖 恢复续跑读取 run {run_id} 状态失败（日志降级）: {e}")
        return "unknown"
    if not row:
        return "missing"
    if isinstance(row, dict):
        return str(row.get("status") or "unknown")
    return str(getattr(row, "status", "unknown"))


async def _execute_continuation(
    dbm,
    run_id: str,
    replay_result,
    thinking_level: str,
    *,
    remaining: int,
    hooks: ScenarioHooks[CtxT],
    ctx: CtxT,
    notification_service: NotificationService | None,
    anchor_replayed_round: bool,
    registry: ToolRegistry,
    tools_schemas: list[ToolSchema],
    reuse_chat_span_id: str | None = None,
) -> None:
    """建 recorder → 补执行缺失工具 → 续跑 loop → 终局处理（registry/schemas 由入口复用）。

    ``anchor_replayed_round=True`` 用于 last_response 非 None 的补执行场景：
    recorder 需锚定到已发生的轮次（补执行 tool span 与既有 chat span 同轮），
    并让续跑 chat 从 ``executed_iterations + 1`` 开始。

    ``reuse_chat_span_id`` 由恢复入口在检测到中断轮次的 running llm_chat 时传入：
    recorder 首次进入该轮时复用它（仓储已 reopen），避免同轮出现两条记录。
    """
    span_recorder = TraceRecorder(
        run_id,
        start_iteration=replay_result.executed_iterations,
        reuse_chat_span_id=reuse_chat_span_id,
        reuse_chat_iteration=(
            replay_result.executed_iterations
            if reuse_chat_span_id is not None
            else None
        ),
    )
    if anchor_replayed_round:
        # begin_replayed_round 内部已设 _next_iteration = iteration + 1，
        # 无需再手动推进（避免私有属性赋值封装泄露）
        span_recorder.begin_replayed_round(replay_result.executed_iterations)

    # 补执行缺失工具并落 tool_execute span（二次 replay 不再判缺失）
    seq = 0
    for tc in replay_result.missing_tool_calls:
        await _replay_missing_tool(
            tc,
            registry,
            replay_result.messages,
            span_recorder=span_recorder,
            sequence=seq,
        )
        seq += 1

    result = await _invoke_loop(
        stream_fn=hooks.build_stream_fn(thinking_level),
        registry=registry,
        span_recorder=span_recorder,
        tools_schemas=tools_schemas,
        max_iterations=remaining,
        hooks=hooks,
        seed_messages=replay_result.messages,
    )
    await hooks.handle_terminal(
        run_id,
        result,
        ctx,
        # 全口径累计：replay 历史轮次（agent_steps 的 llm_chat span 累计）+
        # 本次新产生轮次（实时 recorder 累计），避免恢复续跑只记新轮、丢历史。
        total_tokens=replay_result.total_tokens + span_recorder.total_tokens,
        notification_service=notification_service,
    )


async def _replay_missing_tool(
    tool_call: trace.ReplayToolCall,
    registry: ToolRegistry,
    messages: list[Message],
    *,
    span_recorder: ToolSpanRecorder | None = None,
    sequence: int = 0,
) -> None:
    """补执行单条缺失的无副作用工具调用（只读门控）。

    执行结果作为 ``Message(role="user", content=[ToolResultBlock(...)])``
    追加到 ``messages``，保证 assistant(tool_use) 后存在对应的 tool_result，
    符合会话协议（每条 tool_use 有且仅有一条 tool_result）。

    非只读（write/terminal）与未注册工具**不重放副作用**，但仍回填占位
    tool_result 闭合协议（否则 assistant 的 tool_use 悬空，provider 报协议错误）；
    真实调用留给续跑 loop 自然触发。

    门控判据是 ``access == "read"``（无副作用语义），**不是** ``idempotent``：幂等 ≠ 无副作用，
    幂等的 write 工具重复执行仍会留下副作用记录，故仍走占位不补执行。

    当 ``span_recorder`` 不为 None 时，为每条缺失工具写 ``tool_execute`` span
    （iteration 由调用方通过 ``begin_replayed_round`` 锚定），保证二次 replay
    不再判缺失（replay 自包含）。
    """
    name = tool_call.name
    if not name:
        # 防御分支：缺失工具无 name 无法执行/登记，跳过并留日志（不静默）
        logger.info("🤖 恢复补执行：缺失工具无 name，跳过")
        return
    args = tool_call.input or {}
    tool_use_id = tool_call.id
    defn = registry.get(name)
    tool_use_block = ToolUseBlock(id=tool_use_id, name=name, input=args or {})

    if defn is None or defn.access != "read":
        logger.info(f"🤖 恢复补执行：工具 {name} 非只读/未注册，回填占位结果")
        _append_tool_result(
            messages, tool_use_id, _SKIP_PLACEHOLDER_CONTENT, is_error=False
        )
        if span_recorder is not None:
            span_id = span_recorder.start_tool(tool_use_block, sequence=sequence)
            if span_id is not None:
                span_recorder.end_tool(
                    span_id,
                    result=ToolResultBlock(
                        tool_use_id=tool_use_id,
                        content=_SKIP_PLACEHOLDER_CONTENT,
                        is_error=False,
                    ),
                )
        return

    # 统一执行入口（包装 / span / 错误文本单点收口；异常不上抛）
    outcome = await registry.run_tool(
        tool_use_block, recorder=span_recorder, sequence=sequence
    )
    if isinstance(outcome, TerminalCapture):
        # 防御：read 门控保证不会到达；一旦到达说明门控失效——按占位处理，不重放副作用
        logger.warning(
            f"🤖 恢复补执行遇到终止工具 {name}（read 门控失效），回填占位结果"
        )
        outcome = ToolResultBlock(
            tool_use_id=tool_use_id,
            content=_SKIP_PLACEHOLDER_CONTENT,
            is_error=False,
        )
    _append_tool_result(
        messages, tool_use_id, outcome.content, is_error=outcome.is_error
    )


def _append_tool_result(
    messages: list[Message], tool_use_id: str, content: str, *, is_error: bool
) -> None:
    """追加一条 tool_result 消息（闭合 assistant 的 tool_use）。"""
    messages.append(
        Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id=tool_use_id, content=content, is_error=is_error
                )
            ],
        )
    )
