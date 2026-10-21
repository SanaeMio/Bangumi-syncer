"""Span 记录器与断点重放（otel 概念，自建不引 SDK）。

存储形态（单表双职责）：
- ``agent_steps`` 是唯一存储；trace 是它的全部，replay 是它的一个读取视角。
- **类型化列**（status / model / tokens / latency_ms / tool_name / input_summary /
  error / iteration / sequence）：payload 摘要，供观测与索引。
- **replay_delta**：重放所需全部增量（seed / response / tool_result / budget_message），
  以 ``kind`` 判别式 + ``delta_version`` 的**联合形态**存储（见
  :mod:`app.services.agent.observe.delta`），完整、Fernet 加密（BGS1: 前缀）、
  永不截断、无大小上限、无 error 标记机制。

存储边界（与 ``llm_usage_logs`` 的分工，避免重复/误删）：
- ``llm_usage_logs``：**用量/成本聚合**，跨任务（job）维度，由 LLM client 层在每次
  API 调用后写入，服务于成本统计、配额与供应商观测；**不参与会话重放**。
- ``agent_steps``：**会话观测 + replay 自包含**，run 维度，由本模块记录 span 全生命周期，
  是断点续跑的唯一数据源。
- 两者在 ``model`` / ``tokens`` / ``latency_ms`` 等字段上重叠属**有意设计**：用途不同
  （成本聚合 vs 会话重建），重叠字段**不删除**，以免任一职责失去自包含性。

提供：
- ``start_span`` / ``end_span``：写入 ``agent_steps``（独立 best-effort 事务，失败仅日志），
  承载可重放会话日志。时间列统一 epoch 秒整数。
- ``replay``：从 ``agent_steps`` 按 ``(iteration, sequence)`` 重放会话增量，
  重建可续跑的 ``messages``（断点恢复重建规则）。

replay_delta 写入语义：
- ``llm_chat.end_span``: ``replay_delta = {response: {stop_reason, content, tool_calls,
  blocks}}``（``blocks`` 为本轮完整内容块，含 thinking/text/tool_use，是重建 assistant
  消息的首选来源；``tool_calls`` 为工具调用聚合，仅在 ``blocks`` 缺失/非法时回退使用）。
- ``tool_execute.end_span``: ``replay_delta = {tool_result: {...}}``（仅 tool_result）。
- 预算消息：写**独立** ``budget`` 行（``name="budget"``，
  ``replay_delta = {budget_message: ...}``；一行一形态，排序恒在同轮全部工具行
  之后——由 recorder 的 sequence 分配保证）。

读取/展示侧的截断（含 ``...[shrinked]`` 标记）由 ``app.utils.truncate`` 承担；
写入路径零截断。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.database import get_database_manager
from app.core.logging import logger
from app.services.agent.observe.delta import BudgetMessageDelta, decode_replay_delta
from app.services.llm.models import (
    ContentBlock,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)

# input_summary 上限（≤500 字符，仅参数名与类型，不记录参数值）
MAX_INPUT_SUMMARY_CHARS = 500


def _now() -> int:
    """当前 epoch 秒整数（与 agent_steps/agent_runs 时间列格式一致）。"""
    return int(time.time())


# ----------------------------------------------------------------------
# span 记录（独立 best-effort 事务）
# ----------------------------------------------------------------------


def start_span(
    run_id: str,
    name: str,
    iteration: int,
    sequence: int,
    parent_id: str = "",
    *,
    started_at: int | None = None,
) -> str:
    """开始一条 span，写入 ``agent_steps`` 并返回 span_id（uuid hex）。

    ``started_at`` 为 epoch 秒整数（None → 当前时间）。
    初始 ``status="running"``：**未闭合（进程中断）与正常结束可区分**；
    ``end_span`` 以 ``ok`` / ``error`` 闭合；残留由
    ``AgentRunsRepository.mark_residual_running_spans`` 扫描标记。
    失败（DB 异常）仅记录日志并返回生成的 span_id，不影响主流程。
    """
    span_id = uuid.uuid4().hex
    ts = started_at if started_at is not None else _now()
    try:
        dbm = get_database_manager()
        dbm.agent_runs.add_step(
            {
                "run_id": run_id,
                "span_id": span_id,
                "parent_id": parent_id,
                "name": name,
                "status": "running",
                "iteration": iteration,
                "sequence": sequence,
                "started_at": ts,
            }
        )
    except Exception as e:  # best-effort：失败不影响主流程
        logger.error(f"[trace] start_span 写入失败（已忽略）: {e}")
    return span_id


def _normalize_replay_delta(replay_delta: Any) -> str:
    if isinstance(replay_delta, str):
        return replay_delta
    return json.dumps(replay_delta, ensure_ascii=False)


def end_span(
    span_id: str,
    *,
    status: str = "ok",
    model: str = "",
    tokens: int = 0,
    latency_ms: int = 0,
    tool_name: str = "",
    input_summary: str = "",
    error: str = "",
    replay_delta: Any = "",
    started_at: int | None = None,
    ended_at: int | None = None,
) -> None:
    """结束一条 span，更新 ``agent_steps``（独立 best-effort 事务，失败仅日志）。

    - ``replay_delta``：完整保存，无大小上限、无截断、无 error 标记（加密由仓储层统一处理）。
    - ``input_summary``：截断至 500 字符（仅参数名与类型，不记录参数值）。
    - ``started_at``：epoch 秒整数；**仅显式传入时**才写回，未传时 UPDATE 不触碰
      ``start_span`` 已写入的真实开始时间。
    - ``ended_at``：epoch 秒整数（None → 当前时间）。
    """
    try:
        delta_str = (
            _normalize_replay_delta(replay_delta)
            if replay_delta not in ("", None)
            else ""
        )
        input_summary_str = (input_summary or "")[:MAX_INPUT_SUMMARY_CHARS]
        ts_ended = ended_at if ended_at is not None else _now()

        fields: dict[str, Any] = dict(
            status=status,
            model=model,
            tokens=tokens,
            latency_ms=latency_ms,
            tool_name=tool_name,
            input_summary=input_summary_str,
            error=error,
            replay_delta=delta_str,
            ended_at=ts_ended,
        )
        # 仅显式传入 started_at 时才写回，避免覆盖 start_span 已写入的真实开始时间
        if started_at is not None:
            fields["started_at"] = started_at

        dbm = get_database_manager()
        dbm.agent_runs.update_step(span_id, **fields)
    except Exception as e:  # best-effort：失败不影响主流程
        logger.error(f"[trace] end_span 失败（已忽略）: {e}")


# ----------------------------------------------------------------------
# 断点重放
# ----------------------------------------------------------------------


class ReplayToolCall(BaseModel):
    """``response.tool_calls`` 单项的类型化视图（写侧 schema 见模块 docstring）。

    宽容解析：缺字段取默认值、忽略额外字段（兼容旧/新数据）；单个元素无法模型化
    时由解析层 **丢弃并 warning**（见 :func:`_parse_tool_calls`），不中断重放。
    """

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


class ReplayResponse(BaseModel):
    """``llm_chat.replay_delta.response`` 的类型化视图（写侧 schema 见模块 docstring）。

    - 缺字段取默认值、忽略额外字段（宽容，兼容旧/新数据）。
    - ``blocks`` 保持**原始值**（通常为 list）：内容块恢复/逐元素非法校验统一由
      :func:`_restore_response_blocks` 承担（含非列表回退），此处不重复校验，避免
      损坏的 ``blocks`` 直接触发 ValidationError 而丢掉整轮重放。
    """

    model_config = ConfigDict(extra="ignore")

    stop_reason: str = ""
    content: str = ""
    tool_calls: list[ReplayToolCall] = Field(default_factory=list)
    blocks: Any = None


@dataclass
class ReplayResult:
    """断点重放结果。

    - ``messages``：重建的可续跑消息列表（seed 前缀 + 各轮重建的消息）。
    - ``executed_iterations``：已完整重放的轮数（调用方可据此计算剩余轮次）。
    - ``missing_tool_calls``：最后一轮 llm_chat 声明的工具调用中、尚未记录
      tool_execute 的缺失项（供调用方补执行；只读校验由调用方做）。
    - ``last_response``：最后一条完整 llm_chat 的响应（当其未产生工具/已终止时，
      调用方直接消费分派 end_turn / tool_use / submit；为 None 表示应直接进入下一轮 chat）。
    - ``total_tokens``：本次重放中**所有已出现 llm_chat span** 的 ``tokens`` 列之和。
      口径为「已发生的 LLM 调用都消耗了 token」——无论该轮是否完整执行、是否计入
      ``executed_iterations``、是否被消费（含未完成轮与终局响应所在轮），只要落了
      llm_chat span 就累计。供恢复路径写回 ``total_tokens``（历史轮次已消耗的用量）。
    """

    messages: list[Message] = field(default_factory=list)
    executed_iterations: int = 0
    missing_tool_calls: list[ReplayToolCall] = field(default_factory=list)
    last_response: ReplayResponse | None = None
    total_tokens: int = 0


def reopen_interrupted_chat_span(run_id: str, iteration: int) -> str | None:
    """恢复续跑时复用中断轮次的 ``llm_chat`` span。

    委托仓储同名方法（命中条件 / 重置字段 / 幂等语义见仓储 docstring）。
    恢复入口在 replay 判定「该轮无 response」后调用；找不到可复用行时返回
    ``None``，由 ``TraceRecorder`` 正常新建 span（行为与改造前一致）。
    """
    try:
        return get_database_manager().agent_runs.reopen_interrupted_chat_span(
            run_id, iteration
        )
    except Exception as e:  # best-effort：不阻塞恢复
        logger.warning(f"[trace] 复用中断 chat span 失败（已忽略）: {e}")
        return None


def _parse_json(raw: str | None, default: Any = None) -> Any:
    try:
        return json.loads(raw) if raw else default
    except (ValueError, TypeError):
        return default


def _coerce_str(value: Any) -> str:
    """把任意标量宽容归一为字符串（缺失 → 空串），避免脏类型触发模型校验失败。"""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _parse_tool_calls(raw_tool_calls: Any) -> list[ReplayToolCall]:
    """宽容解析 ``response.tool_calls``：逐元素丢弃非法项并 warning，绝不中断重放。

    - ``None`` → 空列表（字段缺失）。
    - 非列表 → 记 warning 后按空处理（损坏数据，回退 blocks 推导）。
    - 元素无法模型化（非 dict / 字段类型非法）→ 丢弃该元素并记 warning，保留合法项。
    """
    if raw_tool_calls is None:
        return []
    if not isinstance(raw_tool_calls, list):
        logger.warning(
            f"[trace] replay response.tool_calls 非列表"
            f"（type={type(raw_tool_calls).__name__}），按空处理"
        )
        return []
    valid: list[ReplayToolCall] = []
    dropped: list[str] = []
    for tc in raw_tool_calls:
        try:
            valid.append(ReplayToolCall.model_validate(tc))
        except Exception:
            dropped.append(type(tc).__name__)
    if dropped:
        logger.warning(
            f"[trace] replay response.tool_calls 丢弃非法元素 {len(dropped)}/"
            f"{len(raw_tool_calls)}（类型={dropped}），保留合法项"
        )
    return valid


def _parse_response(chat_step: dict) -> ReplayResponse | None:
    """解析 ``llm_chat`` 的 ``replay_delta.response`` 为 :class:`ReplayResponse`。

    返回 ``None`` 与旧实现的「空 response」语义一致：delta 缺失/非 dict、无
    ``response`` 字段、或 ``response`` 为空 dict → 调用方在该轮 break（从该轮重新
    chat）。坏 ``tool_calls`` 元素由 :func:`_parse_tool_calls` 丢弃并 warning。
    """
    obj = _parse_json(chat_step.get("replay_delta"), {})
    if not isinstance(obj, dict):
        return None
    raw = obj.get("response")
    if not isinstance(raw, dict) or not raw:
        return None
    return ReplayResponse(
        stop_reason=_coerce_str(raw.get("stop_reason")),
        content=_coerce_str(raw.get("content")),
        tool_calls=_parse_tool_calls(raw.get("tool_calls")),
        blocks=raw.get("blocks"),
    )


def _parse_tool_result(step: dict) -> dict | None:
    obj = _parse_json(step.get("replay_delta"), {})
    if not isinstance(obj, dict):
        return None
    tr = obj.get("tool_result")
    return tr if isinstance(tr, dict) else None


_CONTENT_BLOCK_ADAPTER: TypeAdapter[ContentBlock] = TypeAdapter(ContentBlock)


def _restore_response_blocks(response: ReplayResponse) -> list[ContentBlock] | None:
    """从 llm_chat ``response.blocks`` 还原内容块列表（保持原顺序）。

    返回 ``None`` 表示应回退 ``tool_calls`` 重建逻辑：

    - ``blocks`` 字段缺失 / 为 None / 空列表（旧数据或本轮无内容块）→ 静默回退（向后兼容）。
    - ``blocks`` 非列表 → 记 warning 后回退（不静默、不抛断）。
    - 元素结构非法 → **逐元素**丢弃非法块、保留合法块（按原顺序），记 warning（含
      丢弃数量与类型）；避免任一坏元素导致整体回退而**丢失 thinking**（续跑被拒）。
    - 全部元素非法 → 无合法块可保留，记 warning 后回退 ``tool_calls`` 重建。
    """
    raw_blocks = response.blocks
    if raw_blocks is None or raw_blocks == []:
        return None
    if not isinstance(raw_blocks, list):
        logger.warning(
            f"[trace] replay response.blocks 非列表"
            f"（type={type(raw_blocks).__name__}），回退 tool_calls 重建"
        )
        return None

    valid: list[ContentBlock] = []
    dropped: list[str] = []
    for b in raw_blocks:
        try:
            valid.append(_CONTENT_BLOCK_ADAPTER.validate_python(b))
        except Exception:
            btype = b.get("type") if isinstance(b, dict) else type(b).__name__
            dropped.append(str(btype))
    if dropped:
        logger.warning(
            f"[trace] replay response.blocks 丢弃非法元素 {len(dropped)}/{len(raw_blocks)}"
            f"（类型={dropped}），保留合法块"
        )
    if not valid:
        logger.warning("[trace] replay response.blocks 全部非法，回退 tool_calls 重建")
        return None
    return valid


def _restore_seed_messages(seed_step: dict, out: list[Message]) -> None:
    """从 seed 行的 ``replay_delta`` 还原种子消息。

    任何异常数据条目都**跳过并记 warning**（不静默）：replay_delta 结构非 dict、
    条目非 dict、条目无法模型化（缺字段/非法结构）。合法条目正常追加。
    """
    obj = _parse_json(seed_step.get("replay_delta"), {})
    if not isinstance(obj, dict):
        logger.warning(
            "[trace] replay seed 行 replay_delta 结构非 dict，已跳过该 seed 行"
        )
        return
    for m in obj.get("seed_messages") or []:
        if not isinstance(m, dict):
            logger.warning(
                f"[trace] replay seed 条目非 dict（type={type(m).__name__}），已跳过"
            )
            continue
        try:
            out.append(Message.model_validate(m))
        except Exception as e:  # 异常数据：跳过该条目，不影响其余 seed 重建
            logger.warning(f"[trace] replay seed 消息反序列化失败，已跳过该条目: {e}")


def replay(run_id: str) -> ReplayResult:
    """按 (iteration, sequence, id) 重放会话增量，重建可续跑 ``list[Message]``。

    排序以 ``(iteration, sequence)`` 为主键、``id`` 为末级 tie-break，确保 seed 行
    与首轮 llm_chat 同 ``(0, 0)`` 时顺序稳定。

    种子消息从 ``name="seed"`` 行的 ``replay_delta.seed_messages`` 还原
    （seed 行由写入方在 run 启动时写入），无需调用方提供额外入参。

    重建规则（与原执行 ``loop.run_loop`` 完全一致）：
    - 每轮从 ``llm_chat.replay_delta`` 重建**一条** assistant 消息：
      ``Message(role="assistant", content=[ToolUseBlock(...) for tc in tool_calls])``
      （content 为 ``list[ToolUseBlock]``，与原执行对齐）。
    - 逐条追加各 ``tool_execute.replay_delta.tool_result`` 重建的
      ``Message(role="user", content=[ToolResultBlock(...)])``（每条工具结果独立成消息，不合并）。
    - 预算消息：读同轮独立 ``budget`` 行（``name="budget"``，一行一形态）；
       缺失时不追加预算消息。
    - 缺失工具识别（S(tool_calls) - R(已记录 tool_execute)）逻辑不变；命中缺失的该轮
       不追加预算消息、不计入 executed_iterations，交回调用方补执行。
    - 行缺失 / 空 delta：在该轮 break（executed_iterations 不含该轮，调用方从该轮重新 chat）。
    """
    dbm = get_database_manager()
    steps = dbm.agent_runs.get_steps(run_id)

    # 累计所有已出现 llm_chat span 的 tokens（口径见 ReplayResult.total_tokens）；
    # 与重建循环的 break 解耦：已发生的 LLM 调用即使所在轮未完整重放也已消耗 token。
    total_tokens = 0
    for s in steps:
        if s["name"] == "llm_chat":
            total_tokens += int(s.get("tokens") or 0)

    steps_by_iter: dict[int, list[dict]] = {}
    seed_messages: list[Message] = []
    for s in steps:
        if s["name"] == "seed":
            # 提取种子消息（异常条目跳过并记 warning，不静默）
            _restore_seed_messages(s, seed_messages)
            continue
        steps_by_iter.setdefault(s["iteration"], []).append(s)

    messages: list[Message] = list(seed_messages)
    executed_iterations = 0
    missing_tool_calls: list[ReplayToolCall] = []
    last_response: ReplayResponse | None = None

    for it in sorted(steps_by_iter.keys()):
        isteps = steps_by_iter[it]
        chat = next((s for s in isteps if s["name"] == "llm_chat"), None)
        if chat is None:
            # 该轮无 llm_chat（异常数据）：在该轮 break，交回调用方从该轮重新 chat
            logger.warning(
                f"[trace] replay iteration={it} 无 llm_chat 行，从该轮 break"
            )
            break

        response = _parse_response(chat)
        if response is None:
            # 空 delta（异常数据）：在该轮 break，交回调用方从该轮重新 chat
            logger.info(
                f"[trace] replay iteration={it} llm_chat replay_delta 为空"
                "（无 response），从该轮 break"
            )
            break

        tool_calls: list[ReplayToolCall] = list(response.tool_calls)
        # 重建 assistant 消息：优先消费全量 blocks（含 thinking，与 live
        # ``list(resp.blocks)`` 逐条一致）；blocks 缺失/非法时回退 tool_calls
        # （旧数据兼容）。thinking 块必须随 tool_use 回传，否则续跑请求会 400。
        blocks = _restore_response_blocks(response)
        # replay_delta 局部损坏：``tool_calls`` 缺失/为空但 blocks 含 tool_use 时，
        # 从 blocks 推导（保持顺序）。否则会被当「终局响应」处理，tool_use 被静默
        # 丢弃、后续恢复误判。
        if not tool_calls and blocks is not None:
            derived = [b for b in blocks if isinstance(b, ToolUseBlock)]
            if derived:
                tool_calls = [
                    ReplayToolCall(id=b.id, name=b.name, input=b.input) for b in derived
                ]
                logger.warning(
                    f"[trace] replay iteration={it} tool_calls 缺失/为空但 blocks 含 "
                    f"{len(derived)} 个 tool_use，已从 blocks 推导"
                )
        # 变量在分支外单点声明：避免分支内注解在更严格的类型检查器下退化为
        # list[ContentBlock] | list[ToolUseBlock] 联合（Message.content 拒绝该联合）。
        assistant_content: list[ContentBlock] = []
        if blocks is not None:
            assistant_content.extend(blocks)
        else:
            assistant_content.extend(
                ToolUseBlock(
                    id=tc.id,
                    name=tc.name,
                    input=tc.input or {},
                )
                for tc in tool_calls
            )
        assistant_msg = Message(role="assistant", content=assistant_content)

        if tool_calls:
            messages.append(assistant_msg)
            # recorded_ids 元素来自 response.tool_calls 的 Any 值（可能含 None），
            # 保持裸 set（set[Any]）以免为纯注解任务引入运行时收窄。
            recorded_ids: set = set()
            budget_message: str | None = None
            for t in sorted(isteps, key=lambda x: (x["sequence"], x["id"])):
                if t["name"] == "budget":
                    # 独立预算行（一行一形态）：按序收集，完整轮末尾统一追加
                    delta = decode_replay_delta(_parse_json(t.get("replay_delta"), {}))
                    if isinstance(delta, BudgetMessageDelta):
                        budget_message = delta.budget_message
                    continue
                if t["name"] != "tool_execute":
                    logger.warning(
                        f"[trace] replay iteration={it} 跳过非 tool_execute 行:"
                        f" name={t['name']}"
                    )
                    continue
                tr = _parse_tool_result(t)
                if tr is None:
                    # 异常数据：无有效 tool_result，跳过该行（该工具由 missing 兜底）
                    logger.warning(
                        f"[trace] replay iteration={it} tool_execute 行无有效"
                        f" tool_result，已跳过: span_id={t.get('span_id', '')}"
                    )
                    continue
                # 逐条 tool_result 独立成 Message（不合并），与原执行一致
                messages.append(
                    Message(
                        role="user",
                        content=[
                            ToolResultBlock(
                                tool_use_id=tr.get("tool_use_id", ""),
                                content=tr.get("content", ""),
                                is_error=bool(tr.get("is_error", False)),
                            )
                        ],
                    )
                )
                recorded_ids.add(tr.get("tool_use_id"))

            missing = [tc for tc in tool_calls if tc.id not in recorded_ids]
            if missing:
                # 最后一轮工具未全部执行完：返回缺失项供调用方补执行；
                # 该轮未完整，不追加预算消息、不计入 executed_iterations
                missing_tool_calls = missing
                last_response = response
                break

            # 本轮完整执行：计入 executed_iterations 并追加预算消息
            executed_iterations += 1
            if budget_message is not None:
                messages.append(Message(role="user", content=budget_message))
            last_response = None
        else:
            # 无工具调用（end_turn / submit_suggestion）：终局响应，直接交回调用方消费
            last_response = response
            break

    return ReplayResult(
        messages=messages,
        executed_iterations=executed_iterations,
        missing_tool_calls=missing_tool_calls,
        last_response=last_response,
        total_tokens=total_tokens,
    )
