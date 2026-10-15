"""工具注册表与执行器单元测试。

覆盖：
- ToolDefinition 的 to_schema 序列化
- ToolRegistry.register / get / execute 的注册、审计、terminal 捕获、超时、JSON Schema 校验
- execute_batch 的分段并行（连续幂等段 gather 并行、非幂等串行、保序）与异常统一包装
- execute_batch + ToolSpanRecorder：工具级包裹（真实时序 + 逐工具即刻落库）
"""

import asyncio
import json
import logging

import pytest

from app.services.agent.execution.tools import (
    ToolDefinition,
    ToolError,
    ToolRegistry,
    serialize_tool_result,
)
from app.services.agent.protocols import TerminalCapture
from app.services.llm.models import ToolResultBlock, ToolUseBlock


def _noop_handler(args):
    return "ok"


async def _async_noop(args):
    return "ok"


def _result_of(results, tool_use_id):
    """按 ordered 槽位取首个匹配结果（BatchResults 唯一视图；重复 id 取首槽）。"""
    for oid, item in results.ordered:
        if oid == tool_use_id:
            return item
    raise KeyError(tool_use_id)


# ---------------------------------------------------------------------------
# ToolDefinition：to_schema 不序列化 access / idempotent
# ---------------------------------------------------------------------------


def test_tool_definition_to_schema_excludes_access_and_idempotent():
    d = ToolDefinition(
        name="search",
        description="search bangumi",
        parameters={
            "type": "object",
            "properties": {"title": {"type": "string", "maxLength": 200}},
            "required": ["title"],
        },
        handler=_noop_handler,
        access="read",
    )
    schema = d.to_schema()
    # 内部形态：带 access 的 ToolSchema（属性访问）
    assert schema.name == "search"
    assert schema.description == "search bangumi"
    assert schema.access == "read"
    assert schema.parameters["properties"]["title"]["maxLength"] == 200
    # wire 形态：仅三字段，access 绝不外泄
    wire = schema.to_wire()
    assert set(wire.keys()) == {"name", "description", "parameters"}
    assert wire["name"] == "search"
    # access / idempotent 均不外泄到 wire（LLM 不可见，天然防诱导）
    assert "access" not in wire and "idempotent" not in wire


# ---------------------------------------------------------------------------
# ToolRegistry：register / get
# ---------------------------------------------------------------------------


def test_register_duplicate_warns_and_overwrites(caplog):
    reg = ToolRegistry()
    caplog.set_level(logging.WARNING)
    first = ToolDefinition(
        name="dup", description="a", parameters={}, handler=_noop_handler, access="read"
    )
    second = ToolDefinition(
        name="dup", description="b", parameters={}, handler=_noop_handler, access="read"
    )
    reg.register(first)
    reg.register(second)
    # 告警覆盖
    assert any("dup" in r.message and "覆盖" in r.message for r in caplog.records)
    # 取回的是后者（覆盖生效）
    got = reg.get("dup")
    assert got is not None
    assert got.description == "b"
    assert len(reg._tools) == 1


def test_get_unregistered_returns_none():
    reg = ToolRegistry()
    assert reg.get("nope") is None


def test_register_quiet_overwrite_logs_info_not_warning(caplog):
    """quiet 覆盖注册是正常的重绑定路径，记 info（非 warning），仍完成覆盖。"""
    reg = ToolRegistry()
    old = ToolDefinition(
        name="q", description="old", parameters={}, handler=_noop_handler, access="read"
    )
    new = ToolDefinition(
        name="q", description="new", parameters={}, handler=_noop_handler, access="read"
    )
    reg.register(old)
    with caplog.at_level(logging.INFO, logger="app.services.llm.tools"):
        reg.register(new, quiet=True)
    # 覆盖路径记 info
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("q" in r.getMessage() and "覆盖" in r.getMessage() for r in infos)
    # 不再刷 warning
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    # 覆盖仍然生效
    got = reg.get("q")
    assert got is not None
    assert got.description == "new"


# ---------------------------------------------------------------------------
# ToolRegistry：execute 未注册抛错
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_unregistered_raises_tool_error():
    reg = ToolRegistry()
    with pytest.raises(ToolError):
        await reg.execute("ghost", {})


# ---------------------------------------------------------------------------
# JSON Schema 校验：缺必填 / pattern / maxLength
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_missing_required_does_not_call_handler():
    called = []
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }

    def handler(args):
        called.append(1)
        return "ran"

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="need",
            description="d",
            parameters=schema,
            handler=handler,
            access="read",
        )
    )
    with pytest.raises(ToolError):
        await reg.execute("need", {})
    assert called == []


@pytest.mark.asyncio
async def test_execute_pattern_mismatch_does_not_call_handler():
    called = []
    schema = {
        "type": "object",
        "properties": {"subject_id": {"type": "string", "pattern": r"^\d+$"}},
        "required": ["subject_id"],
    }

    def handler(args):
        called.append(1)
        return "ran"

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="chk",
            description="d",
            parameters=schema,
            handler=handler,
            access="read",
        )
    )
    with pytest.raises(ToolError):
        await reg.execute("chk", {"subject_id": "abc"})
    assert called == []


@pytest.mark.asyncio
async def test_execute_maxlength_exceeded_does_not_call_handler():
    called = []
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "maxLength": 5}},
        "required": ["title"],
    }

    def handler(args):
        called.append(1)
        return "ran"

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="len",
            description="d",
            parameters=schema,
            handler=handler,
            access="read",
        )
    )
    with pytest.raises(ToolError):
        await reg.execute("len", {"title": "toolong"})
    assert called == []


# ---------------------------------------------------------------------------
# JSON Schema 校验（扩展）：minLength / enum / minimum / maximum / 嵌套 / items
# ---------------------------------------------------------------------------


def _tool_registry(name: str, schema: dict, handler) -> ToolRegistry:
    """构造注册了单工具（read）的 registry；供扩展校验用例复用。"""
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name=name,
            description="d",
            parameters=schema,
            handler=handler,
            access="read",
        )
    )
    return reg


@pytest.mark.asyncio
async def test_execute_minlength_empty_string_rejected_not_calling_handler():
    called = []

    def handler(args):
        called.append(1)
        return "ran"

    reg = _tool_registry(
        "minlen",
        {
            "type": "object",
            "properties": {"subject_id": {"type": "string", "minLength": 1}},
            "required": ["subject_id"],
        },
        handler,
    )
    with pytest.raises(ToolError, match="minLength"):
        await reg.execute("minlen", {"subject_id": ""})
    assert called == []


@pytest.mark.asyncio
async def test_execute_minlength_boundary_single_char_passes():
    reg = _tool_registry(
        "minlen2",
        {
            "type": "object",
            "properties": {"subject_id": {"type": "string", "minLength": 1}},
        },
        lambda args: "ran",
    )
    assert await reg.execute("minlen2", {"subject_id": "1"}) == "ran"


@pytest.mark.asyncio
async def test_execute_minlength_ignored_for_non_string_value():
    """minLength 只约束字符串；非字符串（类型由 type 关键字管）不因它报错。"""
    reg = _tool_registry(
        "minlen3",
        {
            "type": "object",
            "properties": {"count": {"type": "integer", "minLength": 3}},
        },
        lambda args: "ran",
    )
    assert await reg.execute("minlen3", {"count": 5}) == "ran"


@pytest.mark.asyncio
async def test_execute_enum_miss_rejected_not_calling_handler():
    called = []

    def handler(args):
        called.append(1)
        return "ran"

    reg = _tool_registry(
        "enum1",
        {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["tv", "movie"]}},
        },
        handler,
    )
    with pytest.raises(ToolError, match="枚举"):
        await reg.execute("enum1", {"kind": "book"})
    assert called == []


@pytest.mark.asyncio
async def test_execute_enum_hit_passes():
    reg = _tool_registry(
        "enum2",
        {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["tv", "movie"]}},
        },
        lambda args: "ran",
    )
    assert await reg.execute("enum2", {"kind": "movie"}) == "ran"


@pytest.mark.asyncio
async def test_execute_enum_strict_type_rejects_bool_and_string_for_int_enum():
    """enum 严格等值 + 类型匹配：True 不等于 1、"1" 不等于 1。"""
    reg = _tool_registry(
        "enum3",
        {
            "type": "object",
            "properties": {"level": {"type": "integer", "enum": [1, 2]}},
        },
        lambda args: "ran",
    )
    with pytest.raises(ToolError):
        await reg.execute("enum3", {"level": True})
    with pytest.raises(ToolError):
        await reg.execute("enum3", {"level": "1"})
    assert await reg.execute("enum3", {"level": 1}) == "ran"


@pytest.mark.asyncio
async def test_execute_minimum_maximum_boundaries_and_out_of_range():
    reg = _tool_registry(
        "range1",
        {
            "type": "object",
            "properties": {"score": {"type": "number", "minimum": 0.5, "maximum": 1.0}},
        },
        lambda args: "ran",
    )
    assert await reg.execute("range1", {"score": 0.5}) == "ran"
    assert await reg.execute("range1", {"score": 1.0}) == "ran"
    with pytest.raises(ToolError, match="minimum"):
        await reg.execute("range1", {"score": 0.49})
    with pytest.raises(ToolError, match="maximum"):
        await reg.execute("range1", {"score": 1.01})


@pytest.mark.asyncio
async def test_execute_range_skips_bool_when_type_not_declared():
    """未声明 type 时，数值范围也不得把 bool 当数字比较（True 不应触发 minimum）。"""
    reg = _tool_registry(
        "range2",
        {"type": "object", "properties": {"flag": {"minimum": 2}}},
        lambda args: "ran",
    )
    assert await reg.execute("range2", {"flag": True}) == "ran"


@pytest.mark.asyncio
async def test_execute_nested_object_missing_required_reports_path():
    reg = _tool_registry(
        "nested1",
        {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {"kind": {"type": "string"}},
                    "required": ["kind"],
                }
            },
        },
        lambda args: "ran",
    )
    with pytest.raises(ToolError, match=r"filter\.kind"):
        await reg.execute("nested1", {"filter": {}})


@pytest.mark.asyncio
async def test_execute_nested_object_type_error_reports_path():
    reg = _tool_registry(
        "nested2",
        {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                }
            },
        },
        lambda args: "ran",
    )
    with pytest.raises(ToolError, match=r"filter\.limit"):
        await reg.execute("nested2", {"filter": {"limit": "x"}})


@pytest.mark.asyncio
async def test_execute_nested_object_three_levels_passes():
    reg = _tool_registry(
        "nested3",
        {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {
                        "b": {
                            "type": "object",
                            "properties": {"id": {"type": "integer", "minimum": 1}},
                            "required": ["id"],
                        }
                    },
                    "required": ["b"],
                }
            },
            "required": ["a"],
        },
        lambda args: "ran",
    )
    assert await reg.execute("nested3", {"a": {"b": {"id": 7}}}) == "ran"
    with pytest.raises(ToolError, match=r"a\.b\.id"):
        await reg.execute("nested3", {"a": {"b": {"id": 0}}})


@pytest.mark.asyncio
async def test_execute_array_items_element_type_rejected_reports_index():
    reg = _tool_registry(
        "items1",
        {
            "type": "object",
            "properties": {
                "subject_types": {"type": "array", "items": {"type": "integer"}}
            },
        },
        lambda args: "ran",
    )
    with pytest.raises(ToolError, match=r"subject_types\[1\]"):
        await reg.execute("items1", {"subject_types": [1, "x"]})


@pytest.mark.asyncio
async def test_execute_array_items_empty_and_valid_passes():
    reg = _tool_registry(
        "items2",
        {
            "type": "object",
            "properties": {
                "subject_types": {"type": "array", "items": {"type": "integer"}}
            },
        },
        lambda args: "ran",
    )
    assert await reg.execute("items2", {"subject_types": []}) == "ran"
    assert await reg.execute("items2", {"subject_types": [2, 4]}) == "ran"


@pytest.mark.asyncio
async def test_execute_unknown_field_and_unknown_keyword_still_lenient():
    """宽松性保持：未声明字段跳过、schema 未知关键字忽略。"""
    reg = _tool_registry(
        "lenient",
        {
            "type": "object",
            "properties": {"title": {"type": "string", "format": "free-text"}},
        },
        lambda args: "ran",
    )
    assert await reg.execute("lenient", {"title": "x", "extra_field": 1}) == "ran"


@pytest.mark.asyncio
async def test_run_tool_validation_failure_returns_error_block():
    """校验失败经唯一执行入口 → is_error 结果块（不触 handler）。"""
    called = []

    def handler(args):
        called.append(1)
        return "ran"

    reg = _tool_registry(
        "via_run",
        {
            "type": "object",
            "properties": {"subject_id": {"type": "string", "minLength": 1}},
            "required": ["subject_id"],
        },
        handler,
    )
    outcome = await reg.run_tool(
        ToolUseBlock(id="t1", name="via_run", input={"subject_id": ""})
    )
    assert isinstance(outcome, ToolResultBlock)
    assert outcome.is_error is True
    assert "ToolError" in outcome.content
    assert called == []


@pytest.mark.asyncio
async def test_execute_valid_args_calls_handler_with_args():
    received = {}

    def handler(args):
        received.update(args)
        return "value"

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="ok",
            description="d",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
            handler=handler,
            access="read",
        )
    )
    result = await reg.execute("ok", {"x": "hello"})
    assert result == "value"
    assert received == {"x": "hello"}


# ---------------------------------------------------------------------------
# write 级审计日志
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_access_audit_log_before_execution(caplog):
    caplog.set_level(logging.INFO)
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="write_tool",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=_noop_handler,
            access="write",
        )
    )
    await reg.execute("write_tool", {})
    assert any(
        r.levelno == logging.INFO and "write_tool" in r.message and "审计" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# terminal 级不执行 handler，仅捕获参数
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_access_does_not_run_handler_returns_capture():
    called = []
    reg = ToolRegistry()

    def handler(args):
        called.append(1)
        return "never"

    reg.register(
        ToolDefinition(
            name="submit",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="terminal",
        )
    )
    result = await reg.execute("submit", {"subject_id": "123"})
    assert isinstance(result, TerminalCapture)
    assert result.name == "submit"
    assert result.args == {"subject_id": "123"}
    assert called == []


# ---------------------------------------------------------------------------
# 超时：read 级 handler 超时抛 ToolError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_handler_timeout_raises_tool_error():
    reg = ToolRegistry()

    async def slow(args):
        await asyncio.sleep(0.1)
        return "late"

    reg.register(
        ToolDefinition(
            name="slow",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=slow,
            access="read",
        )
    )
    with pytest.raises(ToolError):
        await reg.execute("slow", {}, timeout=0.01)


# ---------------------------------------------------------------------------
# serialize_tool_result：工具结果统一序列化
# ---------------------------------------------------------------------------


def test_serialize_tool_result_dict_keeps_unicode_unescaped():
    """dict 结果 JSON 编码；中文 ensure_ascii=False 不转义。"""
    result = {"标题": "进击的巨人", "score": 9}
    encoded = serialize_tool_result(result)
    assert json.loads(encoded) == result
    assert "进击的巨人" in encoded
    assert "\\u" not in encoded


def test_serialize_tool_result_list_uses_json():
    """list 结果 JSON 编码（非 Python repr）。"""
    result = ["a", 1, {"b": True}]
    assert serialize_tool_result(result) == json.dumps(result, ensure_ascii=False)


def test_serialize_tool_result_string_is_json_quoted():
    """字符串结果同样 JSON 编码（带引号），保证契约统一。"""
    assert serialize_tool_result("ran") == '"ran"'


def test_serialize_tool_result_unserializable_falls_back_to_str(caplog):
    """不可 JSON 序列化对象 fallback str(result)，不抛异常；降级记 warning。

    ``tools.py`` 用 stdlib logging（非项目自定义 Logger），故用 ``caplog`` 捕获。
    """
    obj = {1, 2}  # set 无法 JSON 序列化
    with pytest.raises(TypeError):
        json.dumps(obj, ensure_ascii=False)
    with caplog.at_level(logging.WARNING, logger="app.services.agent.execution.tools"):
        assert serialize_tool_result(obj) == str(obj)
    messages = [r.getMessage() for r in caplog.records]
    assert any("降级为 str" in m and "set" in m for m in messages), (
        f"降级应记 warning（默认级别可见），实际 {messages}"
    )


# ---------------------------------------------------------------------------
# execute_batch：分段并行（连续 readonly 段 gather 并行）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_readonly_segment_runs_in_parallel():
    reg = ToolRegistry()
    starts = {}
    ends = {}

    async def make_handler(key):
        async def handler(args):
            starts[key] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.05)
            ends[key] = asyncio.get_event_loop().time()
            return key

        return handler

    for k in ("a", "b", "c"):
        reg.register(
            ToolDefinition(
                name=k,
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=await make_handler(k),
                access="read",
            )
        )
    calls = [ToolUseBlock(id=k, name=k, input={}) for k in ("a", "b", "c")]
    t0 = asyncio.get_event_loop().time()
    results = await reg.execute_batch(calls)
    elapsed = asyncio.get_event_loop().time() - t0
    # 三条 read 并行，总耗时约等于单条（< 3 倍）
    assert elapsed < 0.13
    # 起始时间彼此接近（并发启动）
    min_start = min(starts.values())
    max_start = max(starts.values())
    assert max_start - min_start < 0.04
    # 返回结构：每条都是 ToolResultBlock，保序
    assert [oid for oid, _ in results.ordered] == ["a", "b", "c"]
    for k in ("a", "b", "c"):
        blk = _result_of(results, k)
        assert isinstance(blk, ToolResultBlock)
        assert blk.tool_use_id == k
        assert blk.is_error is False
        # 新契约：字符串结果也走 JSON 编码（带引号）
        assert blk.content == json.dumps(k, ensure_ascii=False)


# ---------------------------------------------------------------------------
# execute_batch：顺序（read, read, write, read）——非只读串行且相对顺序保持
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_preserves_order_with_write_in_middle():
    reg = ToolRegistry()
    order = []

    async def make_handler(key):
        async def handler(args):
            await asyncio.sleep(0.01)
            order.append(key)
            return key

        return handler

    for k in ("ra", "rb", "wc", "rd"):
        access = "write" if k == "wc" else "read"
        reg.register(
            ToolDefinition(
                name=k,
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=await make_handler(k),
                access=access,  # type: ignore[arg-type]
            )
        )
    calls = [ToolUseBlock(id=k, name=k, input={}) for k in ("ra", "rb", "wc", "rd")]
    results = await reg.execute_batch(calls)
    # 结果 dict 顺序与输入一致
    assert [oid for oid, _ in results.ordered] == ["ra", "rb", "wc", "rd"]
    # 执行顺序：两条 read 并行（先后不确定，但都在 wc 之前），wc 在 rd 之前
    assert order.index("wc") == 2
    assert order.index("rd") == 3
    assert set(order[:2]) == {"ra", "rb"}


# ---------------------------------------------------------------------------
# execute_batch：工具异常 → is_error=True 的 ToolResultBlock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_handler_exception_wrapped_as_error_block():
    reg = ToolRegistry()

    async def boom(args):
        raise ValueError("kaboom")

    reg.register(
        ToolDefinition(
            name="boom",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=boom,
            access="read",
        )
    )
    calls = [ToolUseBlock(id="x1", name="boom", input={})]
    results = await reg.execute_batch(calls)
    blk = _result_of(results, "x1")
    assert isinstance(blk, ToolResultBlock)
    assert blk.is_error is True
    assert "ValueError" in blk.content


# ---------------------------------------------------------------------------
# execute_batch：未注册工具 → is_error=True 的 ToolResultBlock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_unknown_tool_wrapped_as_error_block():
    reg = ToolRegistry()
    calls = [ToolUseBlock(id="u1", name="ghost", input={})]
    results = await reg.execute_batch(calls)
    blk = _result_of(results, "u1")
    assert isinstance(blk, ToolResultBlock)
    assert blk.is_error is True


# ---------------------------------------------------------------------------
# execute_batch：重复 tool_use_id
#   重复 id 仅执行第一个；每个 tool_call 有独立结果槽位（ordered）：
#   首个保留真实结果，第二及以后为 duplicate 错误块（不覆盖首个）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_duplicate_tool_use_id_executes_only_first():
    reg = ToolRegistry()
    called = []

    def handler(args):
        called.append(1)
        return "ran"

    reg.register(
        ToolDefinition(
            name="dup",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="read",
        )
    )
    calls = [
        ToolUseBlock(id="same", name="dup", input={}),
        ToolUseBlock(id="same", name="dup", input={}),
    ]
    results = await reg.execute_batch(calls)
    # handler 仅被调用一次（第一个执行，第二个不执行）
    assert called == [1]
    # 独立槽位与 tool_calls 一一对应（顺序一致）
    assert len(results.ordered) == len(calls)
    first_id, first = results.ordered[0]
    second_id, second = results.ordered[1]
    assert first_id == second_id == "same"
    # 首个槽位保留真实执行结果（不被 dup 错误块覆盖）
    assert isinstance(first, ToolResultBlock)
    assert first.is_error is False
    assert first.content == '"ran"'
    # 第二个槽位为 duplicate 错误块
    assert isinstance(second, ToolResultBlock)
    assert second.is_error is True
    assert second.content == "duplicate tool_use_id"


@pytest.mark.asyncio
async def test_execute_batch_duplicate_write_tool_keeps_first_result():
    """非 readonly（write）串行分支同样保留首个真实结果，第二个为 dup 错误块。"""
    reg = ToolRegistry()
    called = []

    def handler(args):
        called.append(args.get("k"))
        return "written"

    reg.register(
        ToolDefinition(
            name="w",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="write",
        )
    )
    calls = [
        ToolUseBlock(id="w1", name="w", input={"k": "first"}),
        ToolUseBlock(id="w1", name="w", input={"k": "second"}),
    ]
    results = await reg.execute_batch(calls)
    # 仅首个执行（写工具不重复副作用）
    assert called == ["first"]
    assert [i for i, _ in results.ordered] == ["w1", "w1"]
    assert results.ordered[0][1].is_error is False
    assert results.ordered[0][1].content == '"written"'
    assert results.ordered[1][1].is_error is True
    assert results.ordered[1][1].content == "duplicate tool_use_id"
    assert _result_of(results, "w1").content == '"written"'


@pytest.mark.asyncio
async def test_execute_batch_duplicate_does_not_affect_unique_ids():
    reg = ToolRegistry()
    called = []

    def handler(args):
        called.append(args.get("k"))
        return "ran"

    reg.register(
        ToolDefinition(
            name="t",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="read",
        )
    )
    calls = [
        ToolUseBlock(id="a", name="t", input={"k": "a"}),  # 唯一，正常
        ToolUseBlock(id="dup", name="t", input={"k": "dup"}),  # 重复对的首个，执行
        ToolUseBlock(id="dup", name="t", input={"k": "dup2"}),  # 重复，不执行
        ToolUseBlock(id="b", name="t", input={"k": "b"}),  # 唯一，正常
    ]
    results = await reg.execute_batch(calls)
    # 唯一 id 各自执行一次；重复的那个不执行 → 共 3 次
    assert called == ["a", "dup", "b"]
    # 唯一 id 结果不受影响
    assert _result_of(results, "a").is_error is False
    assert _result_of(results, "a").content == '"ran"'
    assert _result_of(results, "b").is_error is False
    assert _result_of(results, "b").content == '"ran"'
    # 重复 id 的首个槽位保留真实结果，第二个槽位为错误块
    slots = results.ordered
    assert [i for i, _ in slots] == ["a", "dup", "dup", "b"]
    assert slots[1][1].is_error is False
    assert slots[1][1].content == '"ran"'
    assert isinstance(slots[2][1], ToolResultBlock)
    assert slots[2][1].is_error is True
    assert slots[2][1].content == "duplicate tool_use_id"
    # ordered 首槽保留真实结果（后续槽为 dup 错误块，见上）
    assert _result_of(results, "dup").is_error is False


# ---------------------------------------------------------------------------
# BatchResults：ordered 单视图（结构收敛；不再是 Mapping）
# ---------------------------------------------------------------------------


def test_batch_results_ordered_slots_single_view():
    """ordered 是唯一视图：槽位读、不可下标、无 get、非 Mapping。"""
    from collections.abc import Mapping

    from app.services.agent.execution.tools import BatchResults

    r1 = ToolResultBlock(tool_use_id="dup", content="real", is_error=False)
    r2 = ToolResultBlock(tool_use_id="dup", content="dup", is_error=True)
    results = BatchResults([("dup", r1), ("dup", r2)])

    # ordered 保留全部槽位（含重复 id），顺序与构造一致
    assert [oid for oid, _ in results.ordered] == ["dup", "dup"]
    assert results.ordered[0][1] is r1
    assert results.ordered[1][1] is r2
    # 结构收敛：不再是 Mapping（无 dict 视图 / first-wins / 覆盖路径）
    assert not isinstance(results, Mapping)
    assert not hasattr(results, "get")
    with pytest.raises(TypeError):
        results["dup"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# execute_batch：含 terminal → 返回 TerminalCapture（不执行 handler）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_terminal_returns_capture_without_handler():
    reg = ToolRegistry()
    called = []

    def handler(args):
        called.append(1)
        return "never"

    reg.register(
        ToolDefinition(
            name="submit",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="terminal",
        )
    )
    calls = [ToolUseBlock(id="s1", name="submit", input={"subject_id": "9"})]
    results = await reg.execute_batch(calls)
    cap = _result_of(results, "s1")
    assert isinstance(cap, TerminalCapture)
    assert cap.name == "submit"
    assert cap.args == {"subject_id": "9"}
    assert called == []


# ---------------------------------------------------------------------------
# execute_batch + ToolSpanRecorder：工具级包裹（真实时序 + 逐工具即刻落库）
# ---------------------------------------------------------------------------


class FakeClock:
    """可注入的假时钟。

    - 手动模式：time() 返回当前 _t，由测试通过 advance() 推进。
    - 自动模式（auto_increment=True）：每次 time() 调用后 _t 自增 1，
      适合仅需保序、无需精确值的场景（如并行/串行时序断言）。
    """

    def __init__(self, auto_increment: bool = False) -> None:
        self._t: float = 0.0
        self._auto = auto_increment

    def time(self) -> float:
        t = self._t
        if self._auto:
            self._t += 1.0
        return t

    def advance(self, delta: float) -> None:
        self._t += delta


class FakeRecorder:
    """记录 start/end 调用顺序与 span 区间的假 recorder。"""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self.events: list[dict] = []
        self._counter: int = 0

    def start_tool(self, tool_use: ToolUseBlock, *, sequence: int) -> str:
        span_id = f"span_{self._counter}"
        self._counter += 1
        self.events.append(
            {
                "type": "start",
                "span_id": span_id,
                "time": self.clock.time(),
                "tool_use_id": tool_use.id,
                "tool_name": tool_use.name,
                "sequence": sequence,
            }
        )
        return span_id

    def end_tool(
        self,
        span_id: str,
        *,
        result: ToolResultBlock | None = None,
        error: str = "",
    ) -> None:
        self.events.append(
            {
                "type": "end",
                "span_id": span_id,
                "time": self.clock.time(),
                "result": result,
                "error": error,
            }
        )


def _spans_by_sequence(recorder: FakeRecorder) -> dict[int, dict]:
    """按 sequence 聚合 start/end 区间，返回 {sequence: {"start": ..., "end": ...}}。

    通过 span_id 关联 start 与 end 事件（end 事件不直接携带 sequence）。
    """
    # 先按 span_id 聚合
    by_id: dict[str, dict] = {}
    for e in recorder.events:
        by_id.setdefault(e["span_id"], {})[e["type"]] = e
    # 再通过 start 事件的 sequence 建立索引
    spans: dict[int, dict] = {}
    for events in by_id.values():
        if "start" in events:
            seq = events["start"]["sequence"]
            spans[seq] = events
    return spans


# 场景1：真实时序 —— start 在执行前、end 在完成后（fake clock，区间覆盖执行体）


@pytest.mark.asyncio
async def test_recorder_真实时序_start在handler前_end在handler后():
    clock = FakeClock()
    recorder = FakeRecorder(clock=clock)
    reg = ToolRegistry()

    async def handler(args):
        clock.advance(10)  # 模拟执行体耗时
        return "ok"

    reg.register(
        ToolDefinition(
            name="t",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="read",
        )
    )
    await reg.execute_batch(
        [ToolUseBlock(id="x", name="t", input={})], recorder=recorder
    )

    spans = _spans_by_sequence(recorder)
    assert len(spans) == 1
    s = spans[0]
    assert "start" in s
    assert "end" in s
    # start 在 handler advance 之前（t=0），end 在 advance 之后（t=10）
    assert s["start"]["time"] == 0.0
    assert s["end"]["time"] == 10.0


# 场景2：逐工具即刻落库 —— 批内某工具执行中断，已完成工具已被记录


@pytest.mark.asyncio
async def test_recorder_逐工具即刻落库_前序工具end在异常工具之前落库():
    """3 工具（2 readonly 并行 + 1 write 串行），write 抛异常时，
    前 2 个 readonly 工具的 end_tool 已被调用。"""
    recorder = FakeRecorder(FakeClock(auto_increment=True))
    reg = ToolRegistry()

    async def fast(args):
        return "fast"

    async def boom(args):
        raise ValueError("kaboom")

    reg.register(
        ToolDefinition(
            name="ra",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=fast,
            access="read",
        )
    )
    reg.register(
        ToolDefinition(
            name="rb",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=fast,
            access="read",
        )
    )
    reg.register(
        ToolDefinition(
            name="wc",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=boom,
            access="write",
        )
    )
    calls = [
        ToolUseBlock(id="a", name="ra", input={}),
        ToolUseBlock(id="b", name="rb", input={}),
        ToolUseBlock(id="c", name="wc", input={}),
    ]
    results = await reg.execute_batch(calls, recorder=recorder)

    # 三个工具都有 start + end
    spans = _spans_by_sequence(recorder)
    assert len(spans) == 3
    for seq in (0, 1, 2):
        assert "start" in spans[seq]
        assert "end" in spans[seq]

    # 前 2 个 readonly 的 end 发生在第 3 个 write 的 start 之前
    assert spans[0]["end"]["time"] < spans[2]["start"]["time"]
    assert spans[1]["end"]["time"] < spans[2]["start"]["time"]

    # write 工具异常被正确包装为 error block
    assert _result_of(results, "c").is_error is True


# 场景3：并行/串行时序 —— 并行段区间重叠、串行段有序不重叠


@pytest.mark.asyncio
async def test_recorder_并行串行时序_并行重叠_串行有序():
    recorder = FakeRecorder(FakeClock(auto_increment=True))
    reg = ToolRegistry()

    async def make_handler():
        async def handler(args):
            return "ok"

        return handler

    for k in ("ra", "rb", "wc"):
        access = "write" if k == "wc" else "read"
        reg.register(
            ToolDefinition(
                name=k,
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=await make_handler(),
                access=access,  # type: ignore[arg-type]
            )
        )
    calls = [ToolUseBlock(id=k, name=k, input={}) for k in ("ra", "rb", "wc")]
    await reg.execute_batch(calls, recorder=recorder)

    spans = _spans_by_sequence(recorder)
    ra = spans[0]
    rb = spans[1]
    wc = spans[2]

    # 并行段：ra 和 rb 区间重叠（max(start) < min(end)）
    parallel_max_start = max(ra["start"]["time"], rb["start"]["time"])
    parallel_min_end = min(ra["end"]["time"], rb["end"]["time"])
    assert parallel_max_start < parallel_min_end

    # 串行段：ra/rb 的 end 都在 wc start 之前
    assert ra["end"]["time"] < wc["start"]["time"]
    assert rb["end"]["time"] < wc["start"]["time"]


# 场景4：异常必达 —— 工具抛异常 → end_tool 仍被调用，error 参数非空


@pytest.mark.asyncio
async def test_recorder_异常必达_工具抛异常时end仍调用且error非空():
    recorder = FakeRecorder(FakeClock(auto_increment=True))
    reg = ToolRegistry()

    async def boom(args):
        raise RuntimeError("crash")

    reg.register(
        ToolDefinition(
            name="boom",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=boom,
            access="read",
        )
    )
    calls = [ToolUseBlock(id="x1", name="boom", input={})]
    results = await reg.execute_batch(calls, recorder=recorder)

    spans = _spans_by_sequence(recorder)
    assert len(spans) == 1
    s = spans[0]
    assert "start" in s
    assert "end" in s
    # error 参数携带异常类型名
    assert s["end"]["error"] == "RuntimeError"
    # result=None（执行异常不产生 ToolResultBlock）
    assert s["end"]["result"] is None
    # 异常被包装为 error block
    assert _result_of(results, "x1").is_error is True


# 场景5：重复 id —— 占位错误块同样有 span（is_error）


@pytest.mark.asyncio
async def test_recorder_重复id_占位错误块同样有span且is_error():
    recorder = FakeRecorder(FakeClock(auto_increment=True))
    reg = ToolRegistry()

    def handler(args):
        return "ran"

    reg.register(
        ToolDefinition(
            name="dup",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="read",
        )
    )
    calls = [
        ToolUseBlock(id="same", name="dup", input={}),
        ToolUseBlock(id="same", name="dup", input={}),
    ]
    results = await reg.execute_batch(calls, recorder=recorder)

    spans = _spans_by_sequence(recorder)
    assert len(spans) == 2  # 两个 span（首个真实 + 重复占位）
    for seq in (0, 1):
        assert "start" in spans[seq]
        assert "end" in spans[seq]
        # 两个 span 的 result 都是 ToolResultBlock
        assert isinstance(spans[seq]["end"]["result"], ToolResultBlock)

    # 其中一个是真实结果（is_error=False），一个是占位错误块（is_error=True）
    end_results = [spans[seq]["end"]["result"] for seq in (0, 1)]
    assert any(r.is_error is False for r in end_results)
    assert any(r.is_error is True for r in end_results)

    # 首个保留真实结果
    assert _result_of(results, "same").is_error is False
    assert _result_of(results, "same").content == '"ran"'


# ---------------------------------------------------------------------------
# 幂等（idempotent）维度 —— 默认推导 + 显式覆盖
#   幂等 ≠ 无副作用：access 是副作用语义，idempotent 是重复执行安全性，二者正交
# ---------------------------------------------------------------------------


def test_tool_definition_idempotent_derives_true_for_read_access():
    """默认推导：access=read → idempotent=True。"""
    d = ToolDefinition(
        name="t", description="d", parameters={}, handler=_noop_handler, access="read"
    )
    assert d.idempotent is True


def test_tool_definition_idempotent_derives_false_for_write_access():
    """默认推导：access=write → idempotent=False。"""
    d = ToolDefinition(
        name="t", description="d", parameters={}, handler=_noop_handler, access="write"
    )
    assert d.idempotent is False


def test_tool_definition_idempotent_derives_false_for_terminal_access():
    """默认推导：access=terminal → idempotent=False。"""
    d = ToolDefinition(
        name="t",
        description="d",
        parameters={},
        handler=_noop_handler,
        access="terminal",
    )
    assert d.idempotent is False


def test_tool_definition_idempotent_explicit_override_write_true():
    """显式覆盖：access=write 但幂等（如幂等写接口）→ idempotent=True。"""
    d = ToolDefinition(
        name="t",
        description="d",
        parameters={},
        handler=_noop_handler,
        access="write",
        idempotent=True,
    )
    assert d.idempotent is True
    # access 副作用语义不受 idempotent 影响（正交）
    assert d.access == "write"


def test_tool_definition_idempotent_explicit_override_read_false():
    """显式覆盖：access=read 但非幂等（读操作含非幂等副作用）→ idempotent=False。"""
    d = ToolDefinition(
        name="t",
        description="d",
        parameters={},
        handler=_noop_handler,
        access="read",
        idempotent=False,
    )
    assert d.idempotent is False


# ---------------------------------------------------------------------------
# ToolRegistry.is_idempotent 查询
# ---------------------------------------------------------------------------


def test_is_idempotent_true_for_read_default_and_explicit_write():
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="r",
            description="d",
            parameters={},
            handler=_noop_handler,
            access="read",
        )
    )
    reg.register(
        ToolDefinition(
            name="w",
            description="d",
            parameters={},
            handler=_noop_handler,
            access="write",
            idempotent=True,
        )
    )
    assert reg.is_idempotent("r") is True
    assert reg.is_idempotent("w") is True


def test_is_idempotent_false_for_write_default_and_explicit_read():
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="w",
            description="d",
            parameters={},
            handler=_noop_handler,
            access="write",
        )
    )
    reg.register(
        ToolDefinition(
            name="r",
            description="d",
            parameters={},
            handler=_noop_handler,
            access="read",
            idempotent=False,
        )
    )
    assert reg.is_idempotent("w") is False
    assert reg.is_idempotent("r") is False


def test_is_idempotent_unregistered_returns_false():
    """未注册工具返回 False（不抛错）。"""
    reg = ToolRegistry()
    assert reg.is_idempotent("ghost") is False


# ---------------------------------------------------------------------------
# ToolRegistry.is_terminal 查询（与 is_idempotent 对称：access 属性的自查询）
# ---------------------------------------------------------------------------


def test_is_terminal_true_only_for_terminal_access():
    reg = ToolRegistry()
    for name, access in (("r", "read"), ("w", "write"), ("t", "terminal")):
        reg.register(
            ToolDefinition(
                name=name,
                description="d",
                parameters={},
                handler=_noop_handler,
                access=access,
            )
        )
    assert reg.is_terminal("t") is True
    assert reg.is_terminal("r") is False
    assert reg.is_terminal("w") is False


def test_is_terminal_unregistered_returns_false():
    """未注册工具返回 False（不抛错，与 is_idempotent 同口径）。"""
    reg = ToolRegistry()
    assert reg.is_terminal("ghost") is False


# ---------------------------------------------------------------------------
# execute_batch 分段判据改为幂等（连续幂等段并行 / 非幂等串行）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_idempotent_segment_runs_in_parallel():
    """3 个幂等工具（默认 read）连续段并行执行。"""
    reg = ToolRegistry()
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    async def make_handler(key):
        async def handler(args):
            starts[key] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.05)
            ends[key] = asyncio.get_event_loop().time()
            return key

        return handler

    for k in ("a", "b", "c"):
        reg.register(
            ToolDefinition(
                name=k,
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=await make_handler(k),
                access="read",
            )
        )
    calls = [ToolUseBlock(id=k, name=k, input={}) for k in ("a", "b", "c")]
    t0 = asyncio.get_event_loop().time()
    results = await reg.execute_batch(calls)
    elapsed = asyncio.get_event_loop().time() - t0
    # 三条幂等工具并行，总耗时约等于单条（< 3 倍）
    assert elapsed < 0.13
    # 起始时间彼此接近（并发启动）
    assert max(starts.values()) - min(starts.values()) < 0.04
    # 结果保序
    assert [oid for oid, _ in results.ordered] == ["a", "b", "c"]
    for k in ("a", "b", "c"):
        assert _result_of(results, k).is_error is False


@pytest.mark.asyncio
async def test_execute_batch_non_idempotent_tools_run_serially():
    """2 个非幂等工具（默认 write）严格串行：后者在前者结束后才开始。"""
    reg = ToolRegistry()
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    async def make_handler(key):
        async def handler(args):
            starts[key] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.05)
            ends[key] = asyncio.get_event_loop().time()
            return key

        return handler

    for k in ("a", "b"):
        reg.register(
            ToolDefinition(
                name=k,
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=await make_handler(k),
                access="write",  # 默认非幂等
            )
        )
    calls = [ToolUseBlock(id=k, name=k, input={}) for k in ("a", "b")]
    t0 = asyncio.get_event_loop().time()
    await reg.execute_batch(calls)
    elapsed = asyncio.get_event_loop().time() - t0
    # 串行：总耗时约 2 倍单条
    assert elapsed >= 0.09
    # b 在 a 结束之后才启动
    assert starts["b"] >= ends["a"] - 0.005


@pytest.mark.asyncio
async def test_execute_batch_mixed_segments_preserve_order():
    """[幂等, 非幂等, 幂等]：段一并行、中间串行、段三并行，结果顺序与输入一致。"""
    reg = ToolRegistry()
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    async def make_handler(key):
        async def handler(args):
            starts[key] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.03)
            ends[key] = asyncio.get_event_loop().time()
            return key

        return handler

    # a/c 幂等（默认 read），b 非幂等（write）
    for k, access in (("a", "read"), ("b", "write"), ("c", "read")):
        reg.register(
            ToolDefinition(
                name=k,
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=await make_handler(k),
                access=access,  # type: ignore[arg-type]
            )
        )
    calls = [ToolUseBlock(id=k, name=k, input={}) for k in ("a", "b", "c")]
    results = await reg.execute_batch(calls)
    # 结果保序
    assert [oid for oid, _ in results.ordered] == ["a", "b", "c"]
    # 段边界不重叠：a 段结束后 b 才启动，b 结束后 c 段才启动
    assert starts["b"] >= ends["a"] - 0.005
    assert starts["c"] >= ends["b"] - 0.005


@pytest.mark.asyncio
async def test_execute_batch_non_idempotent_read_tool_runs_serially():
    """新语义：access=read + idempotent=False 的读工具按非幂等处理，串行执行。"""
    reg = ToolRegistry()
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    async def make_handler(key):
        async def handler(args):
            starts[key] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.05)
            ends[key] = asyncio.get_event_loop().time()
            return key

        return handler

    for k in ("a", "b"):
        reg.register(
            ToolDefinition(
                name=k,
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=await make_handler(k),
                access="read",
                idempotent=False,
            )
        )
    calls = [ToolUseBlock(id=k, name=k, input={}) for k in ("a", "b")]
    t0 = asyncio.get_event_loop().time()
    await reg.execute_batch(calls)
    elapsed = asyncio.get_event_loop().time() - t0
    # 串行：总耗时约 2 倍单条（若仍按 readonly 并行则 < 0.09）
    assert elapsed >= 0.09
    assert starts["b"] >= ends["a"] - 0.005


@pytest.mark.asyncio
async def test_execute_batch_idempotent_write_tool_runs_in_parallel():
    """新语义：access=write + idempotent=True 的写工具按幂等处理，并行执行。"""
    reg = ToolRegistry()
    starts: dict[str, float] = {}

    async def make_handler(key):
        async def handler(args):
            starts[key] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.05)
            return key

        return handler

    for k in ("a", "b"):
        reg.register(
            ToolDefinition(
                name=k,
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=await make_handler(k),
                access="write",
                idempotent=True,
            )
        )
    calls = [ToolUseBlock(id=k, name=k, input={}) for k in ("a", "b")]
    t0 = asyncio.get_event_loop().time()
    await reg.execute_batch(calls)
    elapsed = asyncio.get_event_loop().time() - t0
    # 并行：总耗时约等于单条（若串行则 >= 0.09）
    assert elapsed < 0.09
    assert max(starts.values()) - min(starts.values()) < 0.04


# ---------------------------------------------------------------------------
# 幂等段并发上限（_MAX_PARALLEL_TOOLS）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_idempotent_segment_caps_parallelism():
    """超过上限的幂等工具并发数不超过 _MAX_PARALLEL_TOOLS。"""
    from app.services.agent.execution.tools import _MAX_PARALLEL_TOOLS

    reg = ToolRegistry()
    active = 0
    max_active = 0

    async def handler(args):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "ok"

    reg.register(
        ToolDefinition(
            name="h",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="read",
        )
    )
    n = _MAX_PARALLEL_TOOLS * 2 + 5
    calls = [ToolUseBlock(id=f"t{i}", name="h", input={}) for i in range(n)]
    results = await reg.execute_batch(calls)

    assert max_active <= _MAX_PARALLEL_TOOLS
    assert max_active >= 2  # 仍存在并行
    assert all(not _result_of(results, c.id).is_error for c in calls)


@pytest.mark.asyncio
async def test_execute_batch_under_cap_still_parallel():
    """不超过上限时行为不变：全部并发启动。"""
    reg = ToolRegistry()
    active = 0
    max_active = 0

    async def handler(args):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "ok"

    reg.register(
        ToolDefinition(
            name="h",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            access="read",
        )
    )
    calls = [ToolUseBlock(id=f"t{i}", name="h", input={}) for i in range(5)]
    results = await reg.execute_batch(calls)

    assert max_active == 5
    assert all(not _result_of(results, c.id).is_error for c in calls)


# ---------------------------------------------------------------------------
# run_tool：唯一执行入口（成功 / 异常 / terminal 捕获 三分类）
# BDD：包装（序列化/错误块）、span、warning 单点收口
# ---------------------------------------------------------------------------


class TestRunTool:
    """run_tool：唯一执行入口的三分类结果（异常不上抛；terminal 为正常捕获）。"""

    @pytest.mark.asyncio
    async def test_success_returns_serialized_block_and_records_span(self):
        """成功：返回序列化结果块（is_error=False），span 记 ok。"""
        called: list = []

        def handler(args):
            called.append(args)
            return {"ok": True, "n": 3}

        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="read_x",
                description="d",
                parameters={},
                handler=handler,
                access="read",
            )
        )
        recorder = FakeRecorder()

        outcome = await reg.run_tool(
            ToolUseBlock(id="t1", name="read_x", input={"a": 1}),
            recorder=recorder,
            sequence=0,
        )

        assert called == [{"a": 1}]
        assert isinstance(outcome, ToolResultBlock)
        assert outcome.tool_use_id == "t1"
        assert outcome.is_error is False
        assert json.loads(outcome.content) == {"ok": True, "n": 3}
        spans = _spans_by_sequence(recorder)
        assert spans[0]["end"]["error"] == ""
        assert spans[0]["end"]["result"] is outcome

    @pytest.mark.asyncio
    async def test_error_returns_error_block_without_raising(self, caplog):
        """异常：不上抛，返回 is_error 块（含类型名）；warning + span error 字段。"""

        def boom(args):
            raise RuntimeError("tool down")

        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="read_boom",
                description="d",
                parameters={},
                handler=boom,
                access="read",
            )
        )
        recorder = FakeRecorder()

        with caplog.at_level(
            logging.WARNING, logger="app.services.agent.execution.tools"
        ):
            outcome = await reg.run_tool(
                ToolUseBlock(id="t9", name="read_boom", input={}),
                recorder=recorder,
                sequence=1,
            )

        assert isinstance(outcome, ToolResultBlock)
        assert outcome.is_error is True
        assert "RuntimeError" in outcome.content
        spans = _spans_by_sequence(recorder)
        assert spans[1]["end"]["error"] == "RuntimeError"
        assert spans[1]["end"]["result"] is None
        assert any("read_boom" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_terminal_returns_capture_without_executing_handler(self):
        """terminal：返回 capture（正常结束）、handler 绝不执行、不序列化。"""
        called: list = []

        def handler(args):
            called.append(args)
            return "should-not-run"

        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="submit_x",
                description="d",
                parameters={},
                handler=handler,
                access="terminal",
            )
        )
        recorder = FakeRecorder()

        outcome = await reg.run_tool(
            ToolUseBlock(id="s1", name="submit_x", input={"a": 1}),
            recorder=recorder,
            sequence=0,
        )

        assert called == []
        assert isinstance(outcome, TerminalCapture)
        assert outcome.name == "submit_x"
        assert outcome.args == {"a": 1}
        spans = _spans_by_sequence(recorder)
        # capture 不作为 tool_result 落 span（正常结束、无副作用）
        assert spans[0]["end"]["result"] is None
        assert spans[0]["end"]["error"] == ""

    @pytest.mark.asyncio
    async def test_error_block_includes_original_message(self):
        """错误块携带原始异常 message（限速排队超时等场景可读）。"""

        def boom(args):
            raise RuntimeError("限速排队需等待 61.2s，超过 timeout=30.0s")

        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="read_boom2",
                description="d",
                parameters={},
                handler=boom,
                access="read",
            )
        )

        outcome = await reg.run_tool(ToolUseBlock(id="t2", name="read_boom2", input={}))

        assert isinstance(outcome, ToolResultBlock)
        assert outcome.is_error is True
        assert outcome.content == (
            "工具执行失败: RuntimeError: 限速排队需等待 61.2s，超过 timeout=30.0s"
        )
