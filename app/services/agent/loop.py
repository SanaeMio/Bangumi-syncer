"""轻量 Agent 循环。

通用骨架核心：固定 ``max_iterations`` 上限的 for 循环，每轮经注入的流式 LLM 调用
``stream_fn`` 取响应，按既定流程处理终止、聚合、分段并行、透明预算与 stop_reason。

设计要点：
- **不直接依赖 LLMClient**：LLM 调用经注入的 ``stream_fn(messages, tools=, tool_choice=)``
  （返回 ``AsyncIterator[StreamChunk]``），便于测试 mock 与场景层（llm_assist）注入真实
  客户端。
- **流式解析 + 提交闸门 + 受控执行**（``stream_fn`` 路径）：逐事件喂本轮
  chat span（``recorder.chat_span()``，其 ``__aexit__`` 做**唯一**一次
  ``StreamAggregator.finalize()``；``recorder=None`` 时为 :class:`PlainChatSpan`
  纯聚合），同时喂
  :class:`~app.services.agent.execution.streaming.StreamingToolExecutor`。
  停点时序因协议而异：anthropic/responses **逐工具停点** → 幂等工具在参数停点
  （``tool_use_stop``）到达时**提前启动并与生成重叠**；openai_compat 在
  ``finish_reason`` **流末集中补发**停点 → 仍提前执行但**无重叠收益**；eval replay
  **无停点** → 纯轮级降级。非幂等工具在流结束后按 ``execute_batch`` 语义（连续幂等段
  并行、非幂等串行、保序）执行。执行器由 ``executor_factory`` 按轮构造。
- **终止工具优先**：本轮含终止工具（``ToolSchema.access == "terminal"``）→ 捕获参数即 break，同轮其他工具不执行。
  流式路径下同轮已提前启动的幂等工具可能已执行完成，其结果保留但走终止分支时被忽略
  （无害；terminal 抑制保证后续停点不再启动新任务）。
- **透明预算**：每轮递减后仅当仍有后续轮次（remaining>0）才追加预算消息；
  remaining>1 为朴素 ``[剩余轮次：N]``，remaining==1 为强化文案
  ``FINAL_ROUND_BUDGET_MESSAGE``（明确要求给出结论、禁止再检索）。末轮（remaining==1 起手）
  强制 ``tool_choice=terminal``。末轮不再产生剩余 0 的幻影预算消息。
- **兜底收尾**：for 循环自然结束（即将返回 exhausted）时追加**一次**收尾 LLM 调用
  （``FINAL_RECOVERY_MESSAGE``，tools 仅 terminal schema、tool_choice=terminal），
  best-effort 争取明确结论；不执行任何非终止工具，异常时降级为 exhausted，仅调用一次。
- **预算钩子**：非末轮预算消息与收尾提示生成后调用 ``recorder.record_budget(...)``，
  由 recorder 内部决定并入哪条 span（同轮最后 tool_execute 或回退 llm_chat）。
  ``recorder=None`` 时不落库（观察层缺省），控制流经 :class:`PlainChatSpan` 纯聚合。

不引入 token/wall-time 预算系统，只做轮次上限。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.services.agent.chat_span import ChatSpan, PlainChatSpan
from app.services.agent.protocols import (
    BudgetRecorder,
    StreamFn,
    StreamingExecutor,
    TerminalCapture,
    ToolSchema,
)
from app.services.llm.models import (
    ChatResponse,
    ContentBlock,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


def final_round_budget_message(terminal_name: str) -> str:
    """末轮（remaining==1）强化提示。

    思考模型在强制 ``tool_choice=terminal`` 被供应商降级为 auto 时容易不提交，
    明确要求给出结论（含放弃）并禁止再检索。
    """
    return (
        f"[剩余轮次：1（最后一轮）] 必须调用 {terminal_name} 给出结论："
        "若已确定推荐条目则提交 subject_id 与理由；若确实无法确定，也要调用并说明放弃理由。"
        "不得再调用其他检索工具。"
    )


def final_recovery_message(terminal_name: str) -> str:
    """兜底收尾提示。

    for 循环自然结束（即将 exhausted）时追加一次收尾调用，best-effort 争取一个明确结论；
    该消息须经 recorder 预算通道记录，保证恢复重放一致性。
    """
    return (
        f"[最终收尾] 轮次预算已耗尽。请立即调用 {terminal_name} 给出结论："
        "若已确定则提交 subject_id 与理由；若确实无法确定也请调用并说明放弃理由。"
    )


# veto 暂缓轮：同轮非 terminal 工具的配对 tool_result 占位文案。本轮不执行这些
# 工具（暂缓），仅用于闭合会话协议，避免悬垂 tool_use 触发下一轮端点 400。
VETO_DEFERRED_PLACEHOLDER = "skipped: deferred by veto"


@dataclass
class RunResult:
    """一轮 Agent 会话的终态结果。

    - ``stop_reason``：end_turn / submit_suggestion / exhausted（统一结束原因）
    - ``text``：end_turn 时 LLM 的纯文本输出
    - ``terminal_args``：终止工具捕获的参数 dict（骨架只记录，语义由场景层解释）
    - ``last_response``：末轮 ChatResponse（exhausted 兜底供 output_parser 解析）
    """

    stop_reason: str
    text: str = ""
    terminal_args: dict | None = None
    last_response: ChatResponse | None = None


#: 按轮构造流式工具执行器（返回值需实现 ``StreamingExecutor``）。
#:
#: **必须是工厂而不是单个 executor 实例**：执行器是「一轮一具」的状态机
#: （``_order`` / ``_states`` / ``_tasks`` / ``_terminal_seen`` 每轮必须重置，
#: 且没有 ``reset()``），由 ``_consume_stream`` 在 for 循环内**每轮调用一次**。
#: 复用单实例会导致两类静默错误：
#:
#: 1. ``seq=len(self._order)`` 跨轮累加 → 第 2 轮起始序号不是 0，而
#:    ``agent_steps`` 按 ``(iteration, sequence, id)`` 排序 → span 顺序错乱；
#: 2. ``finalize()`` 返回 ``[result_for(tid) for tid in self._order]``，即**累计**
#:    所有轮次的结果 → loop 向会话重复注入历史 tool_result，违反
#:    「每个 tool_use 恰好一个 tool_result」的会话协议。
#:
#: 零参可调用这一形状本身就是契约的一部分：调用方不得缓存 factory 的返回值。
ExecutorFactory = Callable[[], StreamingExecutor]


async def _consume_stream(
    stream_fn: StreamFn,
    messages: list[Message],
    *,
    tools: list[dict] | None,
    tool_choice: str | None,
    executor_factory: ExecutorFactory | None,
    recorder: BudgetRecorder | None = None,
) -> tuple[ChatResponse, StreamingExecutor | None]:
    """消费一轮流：聚合响应（唯一聚合点），并把工具事件喂给执行器（若注入）。

    返回 ``(resp, executor)``；``executor_factory=None`` 时不构造执行器（纯聚合，
    用于收尾调用等不执行工具的场景）。

    聚合与落库（单聚合契约）：

    - ``recorder`` 非 None → ``recorder.chat_span()``：``__aenter__`` 落本轮 span
      起点、``feed`` 是**唯一**聚合点、``__aexit__`` 做**唯一**一次
      ``StreamAggregator.finalize()`` 并写 span 终态；loop 在块外读 ``span.resp``
      供控制流，与落库内容同源。
    - ``recorder`` 为 None → :class:`PlainChatSpan` 纯聚合（不落库），控制流不变。

    异常安全：执行器**先于消费构造**，流中途异常（httpx 超时/断连等）时先
    ``await asyncio.shield(executor.finalize())`` 收敛已提前启动的任务（避免孤儿后台
    任务与 "exception never retrieved"、执行器引用丢失无法收敛），再向上抛出原异常。
    收敛经 ``shield`` 保护：外层协程被取消（关机/取消竞态）时收敛动作仍在后台完成。
    收敛自身失败（含取消类 ``BaseException``）只记 warning，**绝不掩盖原始异常**。
    """
    executor = executor_factory() if executor_factory is not None else None
    span: ChatSpan = recorder.chat_span() if recorder is not None else PlainChatSpan()
    try:
        async with span:
            async for chunk in stream_fn(
                messages, tools=tools, tool_choice=tool_choice
            ):
                span.feed(chunk)  # 唯一聚合点
                if executor is not None:
                    executor.feed(chunk)
    except BaseException:
        if executor is not None:
            try:
                # finalize 内部 gather(return_exceptions=True) 收敛提前任务。
                # shield：外层被取消时收敛动作仍继续在后台完成（避免任务重新成孤儿）。
                await asyncio.shield(executor.finalize())
            except BaseException as e:
                # 兜住 Exception 与取消类/致命异常；绝不掩盖原始流异常。
                # 打印异常类型名：CancelledError 的 str(e) 为空，否则日志尾部只剩冒号空白，
                # 无法区分「被取消」与「finalize 真失败」。
                logger.warning(
                    "流异常后收敛提前执行任务未完成（可能被取消）: %s",
                    type(e).__name__,
                )
        raise
    resp = span.resp
    if resp is None:
        # 协议防御：正常退出必有响应（异常路径已在上方 re-raise）。实现未 finalize
        # 属协议破坏，按空壳响应降级（由 normalize_stop_reason 判 llm_error），不静默。
        logger.warning("chat span 正常退出却无响应（未 finalize），按空壳响应处理")
        resp = ChatResponse(content="")
    return resp, executor


def _extract_tool_calls(resp: ChatResponse) -> list[ToolUseBlock]:
    """从 ChatResponse.blocks 中提取全部 ToolUseBlock（同轮工具调用顺序固定）。"""
    return [b for b in resp.blocks if isinstance(b, ToolUseBlock)]


def _pad_aligned(
    tool_calls: list[ToolUseBlock],
    aligned: Sequence[ToolResultBlock | TerminalCapture | None],
) -> list[ToolResultBlock | None]:
    """把执行器结果槽位补齐到与 ``tool_calls`` 等长，闭合会话协议。

    防御协议破坏（``finalize`` 返回槽位少于 ``tool_calls`` 或顺序错乱）：按
    ``tool_use_id`` 贪心匹配现有结果，未匹配到的 tool_use 以 ``slot mismatch``
    错误块回填。若直接 ``zip(strict=False)`` 截断，多出的 tool_use 会缺少配对
    tool_result，下一轮真实端点可能 400。
    """
    remaining = [r for r in aligned if isinstance(r, ToolResultBlock)]
    patched: list[ToolResultBlock | None] = []
    for tc in tool_calls:
        match_idx = next(
            (i for i, r in enumerate(remaining) if r.tool_use_id == tc.id), None
        )
        if match_idx is not None:
            patched.append(remaining.pop(match_idx))
            continue
        # 防御兜底：该 tool_use 无对应执行结果（协议破坏）→ 以错误块闭合，不静默
        logger.warning(
            "执行器结果缺少 tool_use_id=%s 的槽位，以 slot mismatch 错误块回填",
            tc.id,
        )
        patched.append(
            ToolResultBlock(tool_use_id=tc.id, content="slot mismatch", is_error=True)
        )
    return patched


def _inject_veto_tool_results(
    messages: list[Message],
    tool_calls: Sequence[ToolUseBlock],
    terminal_tc: ToolUseBlock,
    hint: str,
) -> None:
    """veto 暂缓：为同轮**全部** tool_use 注入配对 tool_result（闭合会话协议）。

    OpenAI/Anthropic 协议要求 assistant 的每条 tool_use 后必须跟配对 tool_result，
    不能只注入 user 文本，**也不能只注入 terminal 的**——同轮其余 tool_use 若无结果
    即悬垂，下一轮真实端点会报 400。故：

    - terminal 工具 → hint 文案（场景护栏提示）
    - 其余工具 → 占位文案（本轮暂缓未执行；不重放副作用）

    逐条独立成 ``Message``，与 ``execute_batch`` 的回填格式一致。
    """
    for tc in tool_calls:
        content = hint if tc.id == terminal_tc.id else VETO_DEFERRED_PLACEHOLDER
        messages.append(
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id=tc.id, content=content, is_error=False)
                ],
            )
        )


def _record_veto_tool(
    recorder: BudgetRecorder | None,
    terminal_tc: ToolUseBlock,
    hint: str,
    *,
    sequence: int,
) -> None:
    """把 veto 伪执行落 tool_execute span，保证 replay 重建的消息与 live 一致。

    ``sequence`` 与 ``execute_batch`` 的约定一致：工具在 ``tool_calls`` 中的下标。
    ``recorder`` 为 None 时跳过（可空实现）。
    """
    if recorder is None:
        return
    span_id = recorder.start_tool(terminal_tc, sequence=sequence)
    if span_id is None:
        # 记录器选择不落 span（如可空实现）：仍需可观测，避免静默
        logger.warning(
            "veto terminal 工具 %s 未获得 span_id，跳过 replay 落 span",
            terminal_tc.name,
        )
        return
    recorder.end_tool(
        span_id,
        result=ToolResultBlock(
            tool_use_id=terminal_tc.id, content=hint, is_error=False
        ),
    )


def normalize_stop_reason(
    stop_reason: str,
    *,
    has_tool_calls: bool,
    blocks: Sequence[ContentBlock] | None,
    content: str,
) -> str | None:
    """按 loop 终局判据归一化响应 ``stop_reason``（``run_loop`` 与恢复续跑共用）。

    返回 ``None`` 表示**非终局**（存在工具调用，应进入工具执行/续跑流程）；否则返回
    归一化后的终局原因：

    - ``end_turn``：显式 end_turn，或未知 stop_reason 但有内容/内容块（兼容旧 provider）
    - ``max_tokens``：生成长度超限（特殊终态，非故障）
    - ``llm_error``：空壳响应（``stop_reason == ""`` 且无内容块、无内容）——LLM 调用
      失败被错误传递，或 provider 异常；不得伪装 end_turn，也不得反复重调 LLM

    判据顺序与 ``run_loop`` 内联逻辑一致：显式 end_turn 优先（即便同轮带工具也视为终态），
    其次存在工具调用则非终局，最后按 stop_reason 细分。
    """
    if stop_reason == "end_turn":
        return "end_turn"
    if has_tool_calls:
        return None
    if stop_reason == "max_tokens":
        return "max_tokens"
    if stop_reason == "" and not blocks and not content:
        return "llm_error"
    return "end_turn"


async def run_loop(
    *,
    tools_schemas: list[ToolSchema],
    max_iterations: int,
    seed_messages: Sequence[Message],
    stream_fn: StreamFn | None = None,
    executor_factory: ExecutorFactory | None = None,
    recorder: BudgetRecorder | None = None,
    veto_terminal: Callable[[dict], str | None] | None = None,
) -> RunResult:
    """运行轻量 Agent 循环，返回 ``RunResult``。

    参数（除 spec 约定的 seed_messages 外，全部经注入解耦，零 LLMClient 依赖）：

    - ``stream_fn``：**唯一 LLM 调用入口**，``(messages, tools=, tool_choice=) ->
      AsyncIterator[StreamChunk]``。流式解析 + 提交闸门 + 受控执行（幂等工具停点提前执行）。
      必须提供；否则抛 ``ValueError``。
    - ``executor_factory``：按轮构造 ``StreamingToolExecutor`` 的工厂（零参可调用）。
      **必须是工厂**：执行器是一轮一具的状态机，由 ``_consume_stream`` 每轮调用一次；
      传单个实例会跨轮串状态（seq 不归零 → span 排序错乱；finalize 重复返回历史结果
      → 重复注入 tool_result 破坏协议）。详见 ``ExecutorFactory`` 注释。
      为 ``None`` 时退化为纯聚合（不执行工具，供不使用工具的调用方）。
    - ``tools_schemas``：工具内部 schema 列表（``ToolSchema``）；调用 provider 前由
      本模块经 ``to_wire()`` 转 wire 形态（``access`` 不外泄）
    - ``max_iterations``：轮次上限（由 budget 策略计算后传入，循环无感知映射来源）
    - ``seed_messages``：调用方构建的种子消息（system + user）。入口立即 ``list()``
      做快照：不改写调用方对象、调用方仍可复用；用 ``Sequence`` 而非 ``Iterable``
      ——seed 会被 runtime 的 ``write_seed_row`` 与 loop **多次消费**，生成器必须被
      类型排除
    - ``recorder``：可选记录器（满足本地 :class:`BudgetRecorder` 协议；不导入具体
      实现以避免与 recorder.py 循环依赖）。每轮经 ``recorder.chat_span()`` 进入本轮
      chat span（唯一聚合点 + 唯一 finalize + 轮末落库）；None 时用
      :class:`PlainChatSpan` 纯聚合（不落库，控制流不变）。
    - ``veto_terminal``：可选终止提交软护栏。非 None 时，命中终止工具的轮次会先询问
      该回调（入参=terminal input）；返回提示文案则**不终止**、注入配对 tool_result 后
      继续一轮（仅拦一次，且末轮不拦）。返回 None / 回调为 None 时行为与现状一致。

    """
    if stream_fn is None:
        # 显式契约：stream_fn 为唯一 LLM 调用入口，必须提供
        raise ValueError("run 需要 stream_fn")

    # provider wire 形态在边界一次性转换（ToolSchema.access 仅供 agent 内部使用，
    # 绝不进入请求体；键序与历史 flat 形态逐字一致）
    tools_wire = [s.to_wire() for s in tools_schemas]

    # 终止工具由注册表语义（access="terminal"）**单点派生**：末轮强制定向、终止判定、
    # 收尾 tools 过滤都只用它；>1 个由 runtime 入口 fail-fast（loop 防御式取首个）。
    terminals = [s for s in tools_schemas if s.access == "terminal"]
    terminal_name = terminals[0].name if terminals else None
    if len(terminals) > 1:
        logger.warning(
            "收到 %d 个终止工具（%s），仅首个 %r 生效（入口应已 fail-fast）",
            len(terminals),
            ", ".join(s.name for s in terminals),
            terminal_name,
        )

    # 物化快照：后续每轮原地 append（assistant/tool_result/预算消息），需可追加容器；
    # list() 浅拷贝保证不改写调用方的 seed 对象（Message 视为不可变，只追加新对象）。
    messages: list[Message] = list(seed_messages)
    remaining = max_iterations
    resp: ChatResponse | None = None
    vetoed = False

    for _iteration in range(max_iterations):
        # 末轮（remaining==1 起手）强制 terminal 收尾；其余轮不指定 tool_choice
        tool_choice = terminal_name if remaining == 1 else None

        resp, executor = await _consume_stream(
            stream_fn,
            messages,
            tools=tools_wire,
            tool_choice=tool_choice,
            executor_factory=executor_factory,
            recorder=recorder,
        )

        # ① 终局归一化（与恢复续跑 runtime.continue_run 共用同一判据）：
        #    end_turn / max_tokens / 空壳 llm_error → 直接终止；含工具调用则继续执行。
        tool_calls = _extract_tool_calls(resp)
        terminal_reason = normalize_stop_reason(
            resp.stop_reason,
            has_tool_calls=bool(tool_calls),
            blocks=resp.blocks,
            content=resp.content,
        )
        if terminal_reason is not None:
            if terminal_reason == "llm_error":
                # 空壳响应（LLM 调用失败后被错误传递到 loop，或 provider 异常）
                # → 显式标记 llm_error，不再伪装 end_turn
                return RunResult(stop_reason="llm_error", last_response=resp)
            return RunResult(
                stop_reason=terminal_reason, text=resp.content, last_response=resp
            )

        # ② 先将本轮全部 tool_use blocks 聚合为【一条】assistant 消息追加。
        # 同时保留响应中的非工具块（Text/Thinking）：思考模型的 thinking 块必须随
        # tool_use 一并回传（Anthropic/DeepSeek 约束，否则真实端点 400：
        # "content[].thinking ... must be passed back"）；OpenAI 兼容层在 provider
        # 侧按各自协议处理（thinking 块跳过）。
        # 无需 ``or [ToolUseBlock(...)]`` 兜底：上方 ``terminal_reason is None`` 仅在
        # ``has_tool_calls=True`` 时成立，即本轮存在 ToolUseBlock（由 ``resp.blocks``
        # 提取），故 ``resp.blocks`` 必非空，兜底分支不可达。
        assistant_blocks: list[ContentBlock] = list(resp.blocks)
        messages.append(Message(role="assistant", content=assistant_blocks))

        # ③ 终止工具优先：含终止工具（access="terminal"）→ 捕获即 break（其他工具不执行）
        terminal_tc: ToolUseBlock | None = None
        terminal_idx = -1
        for idx, tc in enumerate(tool_calls):
            if terminal_name is not None and tc.name == terminal_name:
                terminal_tc = tc
                terminal_idx = idx
                break
        if terminal_tc is not None:
            # 收尾以 await 提前任务（terminal 抑制已保证不再启动新任务、且本轮
            # 非终止工具不执行）；执行层**正常捕获**的 capture 在此消费（Option ②：
            # capture 的唯一消费者 = 终止层），无 capture 时回退 tool_call 参数。
            captures: list[TerminalCapture] = []
            if executor is not None:
                captures = [
                    r
                    for r in await executor.finalize()
                    if isinstance(r, TerminalCapture)
                ]
            terminal_args = captures[0].args if captures else (terminal_tc.input or {})
            # 软护栏（veto）：非末轮 + 本 run 尚未拦过时，先询问一次场景回调。
            # 末轮强收尾优先（remaining==1 不拦）；只拦一次避免无限暂缓。
            hint = ""
            if veto_terminal is not None and remaining > 1 and not vetoed:
                hint = veto_terminal(terminal_args) or ""
            if not hint:
                assert terminal_name is not None  # terminal_tc 非空即派生名存在
                return RunResult(
                    stop_reason=terminal_name,
                    terminal_args=terminal_args,
                    last_response=resp,
                )
            # 暂缓：不终止、不执行其他工具；为同轮**全部** tool_use 注入配对
            # tool_result（terminal=hint、其余=占位）后消耗一轮预算继续。
            vetoed = True
            _inject_veto_tool_results(messages, tool_calls, terminal_tc, hint)
            _record_veto_tool(recorder, terminal_tc, hint, sequence=terminal_idx)
            remaining -= 1
            continue

        # ⑤ 执行本轮工具：finalize 保序返回与 tool_calls 对齐的 ToolResultBlock 列表。
        # aligned 元素类型含 None（_pad_aligned 补齐结果的联合类型），故用联合元素类型；
        # 用 Sequence 承接不同分支的具体列表（list 不变性下无法统一到同一 list 注解）。
        aligned: Sequence[ToolResultBlock | TerminalCapture | None]
        if executor is not None:
            finalized = await executor.finalize()
            if any(isinstance(r, TerminalCapture) for r in finalized):
                # 防御：终止判定在前，非终止轮不应出现 capture
                logger.warning(
                    "loop 非终止轮收到终止捕获（capture），已忽略（终止判定应先于工具执行）"
                )
            aligned = list(finalized)
            if len(aligned) != len(tool_calls):
                # 防御：执行器结果槽位数与 tool_calls 不一致（协议破坏）→ 补齐，
                # 保证每条 tool_use 都有配对 tool_result（否则 zip 截断留下悬垂 tool_use）
                logger.warning(
                    "流式执行器结果槽位数（%d）与 tool_calls（%d）不一致",
                    len(aligned),
                    len(tool_calls),
                )
                aligned = _pad_aligned(tool_calls, aligned)
        else:
            # 防御兜底：未配 executor_factory（纯聚合调用方误传工具轮）→ 闭合协议
            logger.warning(
                "loop 无执行器可用（stream_fn 未配 executor_factory），"
                "本轮 %d 个工具以错误块回填",
                len(tool_calls),
            )
            aligned = [
                ToolResultBlock(
                    tool_use_id=tc.id,
                    content="skipped: no tool executor configured",
                    is_error=True,
                )
                for tc in tool_calls
            ]

        # ⑥ 逐条：追加 tool_result
        for tc, result in zip(tool_calls, aligned, strict=False):
            if result is None or not isinstance(result, ToolResultBlock):
                # 防御：缺失结果或非 ToolResultBlock（如极少数 TerminalCapture 泄漏）跳过
                logger.warning(
                    "tool result 缺失或非 ToolResultBlock（tool=%s, type=%s），跳过",
                    tc.name,
                    type(result).__name__,
                )
                continue
            messages.append(Message(role="user", content=[result]))

        # ⑦ 透明预算：递减后仅当仍有后续轮次（remaining>0）才注入剩余轮次。
        #    末轮递减到 0 时循环随即结束，若仍构造预算消息会写入 trace 参与 replay，
        #    但该消息从未发给 LLM —— 形成「幻影预算消息」，故不注入也不记录。
        remaining -= 1
        if remaining > 0:
            budget_message = (
                final_round_budget_message(terminal_name)
                if (remaining == 1 and terminal_name)
                else f"[剩余轮次：{remaining}]"
            )
            messages.append(Message(role="user", content=budget_message))
            if recorder is not None:
                recorder.record_budget(budget_message)

    # 预算刚性耗尽：追加一次收尾 LLM 调用（best-effort，仅一次，不执行非终止工具），
    # 尽量在返回 exhausted 前拿到明确结论。
    return await _final_recovery(
        stream_fn=stream_fn,
        tools_schemas=tools_schemas,
        terminal_name=terminal_name,
        messages=messages,
        recorder=recorder,
        last_response=resp,
    )


async def _final_recovery(
    *,
    stream_fn: StreamFn,
    tools_schemas: list[ToolSchema],
    terminal_name: str | None,
    messages: list[Message],
    recorder: BudgetRecorder | None,
    last_response: ChatResponse | None,
) -> RunResult:
    """耗尽后的兜底收尾调用（最多一次）。

    - 追加 ``FINAL_RECOVERY_MESSAGE`` 并经 recorder 预算通道记录（重放一致性）
    - tools 过滤为**仅** terminal schema，``tool_choice=terminal``
    - 含 terminal tool_call → 以终止工具名作为 stop_reason；否则 exhausted（last_response=收尾响应）
    - LLM 调用抛异常 → best-effort 捕获返回 exhausted（保留循环内最后一次响应），不重试
    - 收尾不执行任何工具（``executor_factory=None``：纯聚合流，不构造执行器）
    - **无终止工具**（``terminal_name is None``）→ 直接 exhausted，不发起收尾调用
    """
    if terminal_name is None:
        return RunResult(stop_reason="exhausted", last_response=last_response)
    recovery_msg = final_recovery_message(terminal_name)
    messages.append(Message(role="user", content=recovery_msg))
    if recorder is not None:
        recorder.record_budget(recovery_msg)

    terminal_schemas = [s for s in tools_schemas if s.name == terminal_name]
    try:
        recovery_resp, _ = await _consume_stream(
            stream_fn,
            messages,
            tools=[s.to_wire() for s in terminal_schemas],
            tool_choice=terminal_name,
            executor_factory=None,
            recorder=recorder,
        )
    except Exception as e:
        # best-effort：收尾失败不应让 run 更糟，保留循环内最后一次可用响应
        logger.warning("收尾调用失败（best-effort 降级为 exhausted）: %s", e)
        return RunResult(stop_reason="exhausted", last_response=last_response)

    terminal_tc = next(
        (tc for tc in _extract_tool_calls(recovery_resp) if tc.name == terminal_name),
        None,
    )
    if terminal_tc is not None:
        return RunResult(
            stop_reason=terminal_name,
            terminal_args=terminal_tc.input or {},
            last_response=recovery_resp,
        )
    return RunResult(stop_reason="exhausted", last_response=recovery_resp)
