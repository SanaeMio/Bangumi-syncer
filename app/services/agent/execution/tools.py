"""通用工具注册表与执行器。

提供：
- ``ToolDefinition``：工具元信息（含 access 枚举与 idempotent 推导）
- ``ToolRegistry``：注册 / 执行 / JSON Schema 轻量校验 / 分段并行批量执行（按幂等分段）

零新增依赖：JSON Schema 校验使用手写轻量实现（必填字段、类型、pattern、maxLength），
不引入 ``jsonschema``。
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services.agent.protocols import (
    TerminalCapture,
    ToolAccess,
    ToolRunner,
    ToolSchema,
    ToolSpanRecorder,
)
from app.services.llm.models import ToolResultBlock, ToolUseBlock

logger = logging.getLogger(__name__)

# 幂等段并行执行的并发上限：避免一次性发起过多工具调用压垮下游。
_MAX_PARALLEL_TOOLS = 10

# JSON Schema type → Python 类型（用于轻量校验）
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


class ToolError(Exception):
    """工具执行 / 校验失败。"""


def serialize_tool_result(result: Any) -> str:
    """工具结果统一序列化：可 JSON 序列化用 ``json.dumps(ensure_ascii=False)``，
    否则 fallback ``str``。

    这是工具结果回填给 LLM 的**协议契约**：所有工具结果（含字符串、dict、list）
    统一走 JSON 编码，保证消费方（含 replay 补执行路径）有一致的解析口径。
    不可 JSON 序列化的对象（自定义类型等）降级为 ``str(result)``，不抛异常。
    """
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        # 非 JSON 可序列化对象：降级 str，记录 debug 以便定位降级来源（非静默）
        logger.warning(
            "serialize_tool_result 无法 JSON 编码 %s，降级为 str",
            type(result).__name__,
        )
        return str(result)


@dataclass
class ToolDefinition:
    """单一工具定义。

    - ``access``：read（只读）/ write（写，执行前审计）/ terminal（终止性，仅捕获参数）。
      表示**副作用语义**，用于 write 审计、terminal 捕获、补执行门控。
    - ``idempotent``：表示**重复执行安全性**，决定 ``execute_batch`` 的并行调度判据；
      与 ``access`` 正交（幂等 write 重复执行仍有副作用记录）。默认由 ``access``
      推导（read→True；write/terminal→False），工具开发者可显式标注覆盖。
    - ``parameters``：OpenAI function calling 标准的 JSON Schema
    """

    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], Any]
    access: ToolAccess
    idempotent: bool | None = None

    def __post_init__(self) -> None:
        # 显式标注优先；未标注时以 access 作便捷推导
        if self.idempotent is None:
            self.idempotent = self.access == "read"

    def to_schema(self) -> ToolSchema:
        """转换为内部 :class:`~app.services.agent.protocols.ToolSchema`。

        ``access`` 供 agent 内部派生终止工具等语义；**调用 provider 前**须经
        ``ToolSchema.to_wire()`` 转换（access 不序列化，LLM 不可见，天然防诱导）。
        """
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            access=self.access,
        )


@dataclass
class BatchResults:
    """批量执行结果：``ordered`` 有序槽位（**唯一视图**）。

    - ``ordered``：``[(tool_use_id, result), ...]``，与传入 ``tool_calls`` 一一对应
      （长度与顺序一致），消费方按槽位回填 tool_result，保证每条 tool_use
      都有且仅有一条对应结果；重复 ``tool_use_id`` 保留全部槽位（首个真实结果、
      后续为 duplicate 错误块）。
    - 结构性收敛（迁移清单）：不再提供 ``dict[tool_use_id, result]`` 视图 /
      first-wins 覆盖语义 / 覆盖 warning；按 id 取值由消费方按槽位自行实现。
    """

    ordered: list[tuple[str, ToolResultBlock | TerminalCapture]]


class ToolRegistry(ToolRunner):
    """工具注册表与执行器。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # -- 注册 / 查询 ---------------------------------------------------------

    def register(self, defn: ToolDefinition, quiet: bool = False) -> None:
        """注册工具；重复注册**始终覆盖**（后注册的 handler 生效）。

        ``quiet=True``：调用方明确以覆盖为语义（如场景层每次 run 重新绑定
        handler 闭包），重复注册降为 debug 日志，不刷 warning。
        """
        if defn.name in self._tools:
            if quiet:
                logger.info("工具 %r 重新注册（覆盖旧定义）", defn.name)
            else:
                logger.warning("工具 %r 重复注册，已覆盖旧定义", defn.name)
        self._tools[defn.name] = defn

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def is_idempotent(self, name: str) -> bool:
        """工具是否幂等（连续幂等段可并行执行的判据）。"""
        defn = self._tools.get(name)
        return defn is not None and bool(defn.idempotent)

    def is_terminal(self, name: str) -> bool:
        """工具是否终止性（``access == "terminal"``）。

        与 :meth:`is_idempotent` 对称的**属性自查询**：调用方（loop / 执行器）
        无需自行解包 ``ToolDefinition``，也无需在通用层另写一份判定函数。

        未注册工具返回 ``False``（不抛错），与 :meth:`is_idempotent` 同口径。
        """
        defn = self._tools.get(name)
        return defn is not None and defn.access == "terminal"

    # -- 校验 ---------------------------------------------------------------

    def _validate(self, defn: ToolDefinition, args: dict) -> None:
        """轻量 JSON Schema 校验；失败抛 ToolError。

        支持（递归；错误消息携带完整字段路径，如 ``filter.kind`` /
        ``subject_types[0]``）：required 字段必填、type 类型检查、enum
        （严格等值 + 类型匹配）、pattern 正则、minLength / maxLength 长度、
        minimum / maximum 数值范围、嵌套 object、array items 元素。
        **宽松性保持**：properties 未声明的字段与 schema 未知关键字一律跳过
        （不收紧，避免拒绝 LLM 输出）。
        """
        schema = defn.parameters or {}
        if not isinstance(schema, dict):
            return
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        required = required if isinstance(required, list) else []

        for field in required:
            if field not in args:
                raise ToolError(f"缺少必填参数: {field}")

        for key, val in args.items():
            prop = properties.get(key)
            if prop is None:
                continue
            self._check_value(key, val, prop)

    def _check_object(self, path: str, value: dict, prop: dict) -> None:
        """递归校验嵌套 object（required + properties 子字段）。"""
        required = prop.get("required")
        required = required if isinstance(required, list) else []
        for field in required:
            if field not in value:
                raise ToolError(f"缺少必填参数: {self._join_path(path, field)}")
        properties = prop.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for key, val in value.items():
            sub = properties.get(key)
            if sub is None:
                continue
            self._check_value(self._join_path(path, key), val, sub)

    def _check_value(self, path: str, val: Any, prop: Any) -> None:
        """单字段校验（顶层字段入口；嵌套 object / array 元素递归调用）。"""
        if not isinstance(prop, dict):
            return
        ptype = prop.get("type")
        if ptype:
            self._check_type(path, val, ptype)
        enum = prop.get("enum")
        if enum is not None and not any(
            type(val) is type(member) and val == member for member in enum
        ):
            raise ToolError(f"参数 {path} 取值不在枚举 {enum!r} 中")
        if isinstance(val, str):
            min_length = prop.get("minLength")
            if min_length is not None and len(val) < min_length:
                raise ToolError(f"参数 {path} 长度小于 minLength {min_length}")
            max_length = prop.get("maxLength")
            if max_length is not None and len(val) > max_length:
                raise ToolError(f"参数 {path} 超过 maxLength {max_length}")
            pattern = prop.get("pattern")
            if pattern is not None and not re.fullmatch(pattern, val):
                raise ToolError(f"参数 {path} 不匹配 pattern {pattern!r}")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            minimum = prop.get("minimum")
            if minimum is not None and val < minimum:
                raise ToolError(f"参数 {path} 小于 minimum {minimum}")
            maximum = prop.get("maximum")
            if maximum is not None and val > maximum:
                raise ToolError(f"参数 {path} 大于 maximum {maximum}")
        if isinstance(val, dict) and ("properties" in prop or "required" in prop):
            self._check_object(path, val, prop)
        items = prop.get("items")
        if isinstance(val, list) and isinstance(items, dict):
            for index, elem in enumerate(val):
                self._check_value(f"{path}[{index}]", elem, items)

    @staticmethod
    def _join_path(path: str, key: str) -> str:
        """拼接嵌套字段路径（``filter`` + ``kind`` → ``filter.kind``）。"""
        return f"{path}.{key}" if path else key

    @staticmethod
    def _check_type(path: str, val: Any, ptype: str) -> None:
        expected = _TYPE_MAP.get(ptype)
        if expected is None:
            return
        # bool 是 int 子类的特殊情况：integer/number 不应接受 bool
        if ptype in ("integer", "number") and isinstance(val, bool):
            raise ToolError(f"参数 {path} 应为 {ptype}，实际为 bool")
        if not isinstance(val, expected):
            raise ToolError(f"参数 {path} 应为 {ptype}，实际为 {type(val).__name__}")

    # -- 单条执行 -----------------------------------------------------------

    async def execute(self, name: str, args: dict, timeout: float = 30) -> Any:
        """执行单条工具。

        - 未注册 → 抛 ToolError
        - JSON Schema 校验失败 → 抛 ToolError（不调 handler）
        - write 级：执行前记录审计日志
        - terminal 级：不执行 handler，返回 ``TerminalCapture``（**正常结束**语义）

        注意：生产调用方应经 :meth:`run_tool`（唯一执行入口）——它负责结果包装、
        span 与错误文本收口；本方法是底层实现（测试可直接调用）。
        - read 级：以 ``asyncio.wait_for`` 包裹 handler（默认 30s 超时，超时抛 ToolError）
        """
        defn = self._tools.get(name)
        if defn is None:
            raise ToolError(f"未注册的工具: {name}")

        self._validate(defn, args)

        if defn.access == "terminal":
            return TerminalCapture(name=defn.name, args=args)

        if defn.access == "write":
            logger.info("审计日志：执行写工具 %r args=%s", defn.name, args)

        try:
            return await self._call_handler(defn, args, timeout)
        except asyncio.TimeoutError as exc:
            raise ToolError(f"工具 {name} 执行超时（>{timeout}s）") from exc

    @staticmethod
    async def _call_handler(defn: ToolDefinition, args: dict, timeout: float) -> Any:
        handler = defn.handler
        if asyncio.iscoroutinefunction(handler):
            return await asyncio.wait_for(handler(args), timeout=timeout)
        # 同步 handler：丢到线程池并以 wait_for 限定超时
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, handler, args), timeout=timeout
        )

    # -- 批量分段并行执行 ---------------------------------------------------

    async def run_tool(
        self,
        tool_use: ToolUseBlock,
        *,
        recorder: ToolSpanRecorder | None = None,
        sequence: int = 0,
    ) -> ToolResultBlock | TerminalCapture:
        """统一工具执行入口（**唯一**；三分类结果 + span + 错误文本单点收口）。

        - 成功 → ``ToolResultBlock``（内容为 ``serialize_tool_result`` 结果）
        - 异常（ToolError / handler 异常 / 超时）→ ``ToolResultBlock(is_error=True)``，
          **不上抛**，并记 warning（工具名 + 异常信息）；块内容携带异常 message
          （``工具执行失败: <类型>: <原始信息>``，限速排队超时等场景可读）
        - terminal → ``TerminalCapture``（正常结束：捕获参数、不执行 handler）

        span：注入 ``recorder`` 时在调用前 ``start_tool``、完成后 ``end_tool``（必达）；
        terminal 捕获不作为 tool_result 落 span（``result=None, error=""``）。
        """
        span_id: str | None = None
        if recorder is not None:
            span_id = recorder.start_tool(tool_use, sequence=sequence)
        outcome: ToolResultBlock | TerminalCapture
        error_name = ""
        result_for_span: ToolResultBlock | None = None
        try:
            raw = await self.execute(tool_use.name, tool_use.input)
            if isinstance(raw, TerminalCapture):
                outcome = raw
            else:
                outcome = ToolResultBlock(
                    tool_use_id=tool_use.id,
                    content=serialize_tool_result(raw),
                    is_error=False,
                )
                result_for_span = outcome
        except Exception as exc:  # ToolError / handler 异常 / 超时 统一转为错误块
            error_name = type(exc).__name__
            logger.warning("工具 %s 执行失败: %s", tool_use.name, exc)
            outcome = ToolResultBlock(
                tool_use_id=tool_use.id,
                content=f"工具执行失败: {error_name}: {exc}",
                is_error=True,
            )
        finally:
            if recorder is not None and span_id is not None:
                recorder.end_tool(span_id, result=result_for_span, error=error_name)
        return outcome

    async def execute_batch(
        self,
        tool_calls: list[ToolUseBlock],
        *,
        recorder: ToolSpanRecorder | None = None,
    ) -> "BatchResults":
        """分段并行批量执行。

        - 按原始顺序扫描：连续**幂等**（idempotent）段 ``asyncio.gather`` 并行（保序返回）
        - 非幂等（含 write / terminal / 显式标注非幂等的 read）单独串行，相对顺序保持
        - 返回 ``BatchResults``：``ordered`` 与 ``tool_calls`` **一一对应的独立槽位**
          （唯一视图）：
           - read/write 成功 → ``ToolResultBlock(tool_use_id, content, is_error=False)``
           - 任意异常（校验/超时/handler 异常/未注册）→ ``ToolResultBlock(is_error=True)``
           - terminal → ``TerminalCapture``（正常结束：不执行 handler，供终止层消费）
           - 重复 ``tool_use_id``：仅执行第一个，**首个槽位保留真实结果**，第二及以后的
             槽位为 ``ToolResultBlock(is_error=True, content="duplicate tool_use_id")``
             （独立槽位避免 dup 错误块覆盖首个真实结果）
        - ``recorder``：工具级 span 记录器（结构满足
          :class:`~app.services.agent.protocols.ToolSpanRecorder`；不要求 budget 能力）。
          每个工具执行前 ``start_tool``、完成后 ``end_tool``（finally 必达）；
          重复 ``tool_use_id`` 的占位错误块同样走包裹（有 span，is_error）；
          ``None`` 时行为与现在完全一致（零回归）。
        """
        n = len(tool_calls)
        # 用列表推导初始化（而非 ``[None] * n``）：旧版类型检查器会把乘法结果
        # 窄化为 ``list[None]`` 并误报后续赋值/取值，推导写法新旧版本均通过。
        slots: list[Any] = [None for _ in range(n)]
        seen_ids: set[str] = set()
        i = 0
        while i < n:
            tc = tool_calls[i]
            # 重复 tool_use_id：不执行 handler，本槽位回填错误块（同样走 recorder 包裹）
            if tc.id in seen_ids:
                slots[i] = self._duplicate_block(tc.id)
                self._record_duplicate(tc, slots[i], recorder=recorder, sequence=i)
                i += 1
                continue
            if self.is_idempotent(tc.name):
                # 收集连续幂等段，段内同样跳过重复 id（不进入 gather）
                j = i
                seg: list[tuple[int, ToolUseBlock]] = []
                while j < n and self.is_idempotent(tool_calls[j].name):
                    cur = tool_calls[j]
                    if cur.id in seen_ids:
                        slots[j] = self._duplicate_block(cur.id)
                        self._record_duplicate(
                            cur, slots[j], recorder=recorder, sequence=j
                        )
                    else:
                        seen_ids.add(cur.id)
                        seg.append((j, cur))
                    j += 1
                # 幂等段并行受 _MAX_PARALLEL_TOOLS 上限约束（保序返回不变）
                sem = asyncio.Semaphore(_MAX_PARALLEL_TOOLS)
                seg_results = await asyncio.gather(
                    *[
                        self._exec_one_limited(idx, t, recorder=recorder, sem=sem)
                        for idx, t in seg
                    ]
                )
                for (idx, _), r in zip(seg, seg_results, strict=True):
                    slots[idx] = r
                i = j
            else:
                seen_ids.add(tc.id)
                slots[i] = await self._exec_one(tc, recorder=recorder, sequence=i)
                i += 1
        return BatchResults([(tc.id, slots[idx]) for idx, tc in enumerate(tool_calls)])

    @staticmethod
    def _record_duplicate(
        tc: ToolUseBlock,
        dup_block: ToolResultBlock,
        *,
        recorder: ToolSpanRecorder | None,
        sequence: int,
    ) -> None:
        """为重复 tool_use_id 的占位错误块记录 span（有 span，is_error）。"""
        if recorder is None:
            return
        span_id = recorder.start_tool(tc, sequence=sequence)
        if span_id is not None:
            recorder.end_tool(span_id, result=dup_block, error="")

    @staticmethod
    def _duplicate_block(tool_use_id: str) -> ToolResultBlock:
        """重复 tool_use_id 的占位错误块（不执行 handler）。"""
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content="duplicate tool_use_id",
            is_error=True,
        )

    async def _exec_one_limited(
        self,
        idx: int,
        t: ToolUseBlock,
        *,
        recorder: ToolSpanRecorder | None,
        sem: asyncio.Semaphore,
    ) -> Any:
        """在并发信号量约束下执行单条工具（幂等段并发上限）。"""
        async with sem:
            return await self._exec_one(t, recorder=recorder, sequence=idx)

    async def _exec_one(
        self,
        tc: ToolUseBlock,
        *,
        recorder: ToolSpanRecorder | None = None,
        sequence: int = 0,
    ) -> ToolResultBlock | TerminalCapture:
        """执行单条（薄委托：包装/span/错误文本统一由 :meth:`run_tool` 收口）。"""
        return await self.run_tool(tc, recorder=recorder, sequence=sequence)
