"""app.services.llm.models 测试。"""

from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.services.llm.models import (
    ChatResponse,
    Message,
    RedactedThinkingBlock,
    StreamAggregator,
    StreamChunk,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
    collect,
)


class TestContentBlock:
    """ContentBlock 构造与校验。"""

    def test_text_block(self):
        b = TextBlock(text="hi")
        assert b.type == "text"
        assert b.text == "hi"

    def test_thinking_block(self):
        b = ThinkingBlock(thinking="思考", signature="sig1")
        assert b.type == "thinking"
        assert b.thinking == "思考"
        assert b.signature == "sig1"

    def test_thinking_block_signature_optional(self):
        b = ThinkingBlock(thinking="思考")
        assert b.signature is None

    def test_redacted_thinking_block(self):
        b = RedactedThinkingBlock(data="xxx")
        assert b.type == "redacted_thinking"
        assert b.data == "xxx"

    def test_invalid_type_raises(self):
        """type 字段被 Literal 强约束。"""
        with pytest.raises(ValidationError):
            TextBlock(type=cast("Any", "thinking"), text="hi")
        with pytest.raises(ValidationError):
            ThinkingBlock(type=cast("Any", "text"), thinking="x")

    def test_unknown_block_type_not_in_union(self):
        """Union 仅接受已知 block 类型。

        tool_use / tool_result 已被纳入 ContentBlock，
        故这两个类型现在可被 Message 正常解析；真正未知的 block type 仍抛
        ValidationError（向后强约束）。
        """
        from app.services.llm.models import ToolResultBlock, ToolUseBlock

        # tool_use / tool_result 现属合法 union 成员
        msg = Message.model_validate(
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "u1", "name": "search"}],
            }
        )
        assert isinstance(msg.content[0], ToolUseBlock)

        msg2 = Message.model_validate(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "u1", "content": "ok"}
                ],
            }
        )
        assert isinstance(msg2.content[0], ToolResultBlock)

        # 真正未知的类型仍被拒绝
        with pytest.raises(ValidationError):
            Message.model_validate(
                {
                    "role": "assistant",
                    "content": [{"type": "bogus", "foo": 1}],
                }
            )

    def test_content_block_dump(self):
        b = TextBlock(text="hi")
        assert b.model_dump(exclude_none=True) == {"type": "text", "text": "hi"}


class TestMessage:
    """Message 模型创建和序列化。"""

    def test_create_message(self):
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_message_all_roles(self):
        for role in ("system", "user", "assistant"):
            msg = Message(role=role, content="test")
            assert msg.role == role

    def test_message_invalid_role_raises(self):
        with pytest.raises(ValueError):
            Message(role=cast("Any", "invalid"), content="test")

    def test_message_serialization(self):
        msg = Message(role="user", content="What is AI?")
        d = msg.model_dump()
        assert d == {"role": "user", "content": "What is AI?"}

    def test_message_deserialization(self):
        d = {"role": "assistant", "content": "AI is ..."}
        msg = Message.model_validate(d)
        assert msg.role == "assistant"
        assert msg.content == "AI is ..."

    def test_message_empty_content(self):
        msg = Message(role="system", content="")
        assert msg.content == ""

    def test_message_content_blocks(self):
        """新用法——content 为 list[ContentBlock]。"""
        msg = Message(role="assistant", content=[TextBlock(text="hi")])
        assert isinstance(msg.content, list)
        block = msg.content[0]
        assert isinstance(block, TextBlock)
        assert block.text == "hi"

    def test_message_str_backward_compat(self):
        """旧用法——content 为 str。"""
        msg = Message(role="user", content="纯文本")
        assert isinstance(msg.content, str)
        assert msg.content == "纯文本"

    def test_message_serialization_with_blocks(self):
        msg = Message(role="assistant", content=[TextBlock(text="hi")])
        d = msg.model_dump()
        assert d == {
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
        }


class TestUsage:
    """Usage 模型默认值和序列化。"""

    def test_usage_defaults(self):
        u = Usage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_usage_custom_values(self):
        u = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert u.prompt_tokens == 100
        assert u.completion_tokens == 50
        assert u.total_tokens == 150

    def test_usage_serialization(self):
        u = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        d = u.model_dump()
        assert d == {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

    def test_usage_partial_values(self):
        u = Usage(prompt_tokens=5)
        assert u.prompt_tokens == 5
        assert u.completion_tokens == 0
        assert u.total_tokens == 0
        assert u.cache_creation_input_tokens == 0
        assert u.cache_read_input_tokens == 0

    def test_usage_cache_fields_settable(self):
        """缓存读写用量可写入（Anthropic prompt cache 命中率可观测）。"""
        u = Usage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cache_creation_input_tokens=1200,
            cache_read_input_tokens=800,
        )
        assert u.cache_creation_input_tokens == 1200
        assert u.cache_read_input_tokens == 800
        # 缓存命中不改写既有口径：total_tokens 仍为 prompt + completion
        assert u.total_tokens == 15


class TestChatResponse:
    """ChatResponse 模型创建、序列化和可选的 usage。"""

    def test_create_without_usage(self):
        resp = ChatResponse(content="Hello", model="gpt-4o-mini")
        assert resp.content == "Hello"
        assert resp.model == "gpt-4o-mini"
        assert resp.usage is None

    def test_create_with_usage(self):
        usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        resp = ChatResponse(content="Hi", model="test", usage=usage)
        assert resp.content == "Hi"
        assert resp.model == "test"
        assert resp.usage is not None
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.total_tokens == 15

    def test_chat_response_serialization_without_usage(self):
        resp = ChatResponse(content="Hi", model="gpt-3.5-turbo")
        d = resp.model_dump()
        assert d["content"] == "Hi"
        assert d["model"] == "gpt-3.5-turbo"
        assert d["usage"] is None

    def test_chat_response_serialization_with_usage(self):
        usage = Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        resp = ChatResponse(content="X", model="m", usage=usage)
        d = resp.model_dump()
        assert d["content"] == "X"
        assert d["model"] == "m"
        assert d["usage"] == {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

    def test_chat_response_default_model(self):
        resp = ChatResponse(content="Hi")
        assert resp.model == ""

    def test_chat_response_extra_fields_ignored(self):
        """Pydantic 使用 model_validate 时应默认忽略未知字段。"""
        resp = ChatResponse.model_validate(
            {"content": "Hi", "model": "m", "extra": "should-be-ignored"}
        )
        assert resp.content == "Hi"


class TestStreamAggregator:
    """StreamChunk 序列聚合为等价 ChatResponse。"""

    def test_aggregate_text_deltas_concatenates_content(self):
        """多个 text_delta → content 拼接、blocks 含单个 TextBlock。"""
        agg = StreamAggregator()
        for part in ("你", "好", "世界"):
            agg.feed(StreamChunk(type="text_delta", text=part))

        resp = agg.finalize()

        assert resp.content == "你好世界"
        assert len(resp.blocks) == 1
        block = resp.blocks[0]
        assert isinstance(block, TextBlock)
        assert block.text == "你好世界"

    def test_aggregate_thinking_and_signature_deltas(self):
        """thinking_delta 与 signature 均按增量拼接。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="thinking_delta", thinking="思", signature="sig-1"))
        agg.feed(StreamChunk(type="thinking_delta", thinking="考", signature="sig-2"))

        resp = agg.finalize()

        assert len(resp.blocks) == 1
        block = resp.blocks[0]
        assert isinstance(block, ThinkingBlock)
        assert block.thinking == "思考"
        assert block.signature == "sig-1sig-2"

    def test_aggregate_tool_use_deltas_builds_input_dict(self):
        """tool_use_start + 多段 tool_use_delta → 完整 input dict。"""
        agg = StreamAggregator()
        agg.feed(
            StreamChunk(type="tool_use_start", tool_use_id="t1", tool_name="search")
        )
        agg.feed(
            StreamChunk(type="tool_use_delta", tool_use_id="t1", partial_json='{"q":')
        )
        agg.feed(
            StreamChunk(type="tool_use_delta", tool_use_id="t1", partial_json='"hi"}')
        )

        resp = agg.finalize()

        assert len(resp.blocks) == 1
        block = resp.blocks[0]
        assert isinstance(block, ToolUseBlock)
        assert block.id == "t1"
        assert block.name == "search"
        assert block.input == {"q": "hi"}

    def test_malformed_tool_json_falls_back_to_raw(self):
        """partial_json 非法 → input 兜底 {"raw": <原字符串>}。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="tool_use_start", tool_use_id="t1", tool_name="x"))
        agg.feed(
            StreamChunk(type="tool_use_delta", tool_use_id="t1", partial_json="{bad")
        )

        resp = agg.finalize()

        block = resp.blocks[0]
        assert isinstance(block, ToolUseBlock)
        assert block.input == {"raw": "{bad"}

    def test_usage_and_stop_propagated(self):
        """usage / stop 事件传递到 ChatResponse。"""
        agg = StreamAggregator()
        usage = Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        agg.feed(StreamChunk(type="usage", usage=usage))
        agg.feed(StreamChunk(type="stop", stop_reason="tool_use"))

        resp = agg.finalize()

        assert resp.usage is not None
        assert resp.usage.prompt_tokens == 1
        assert resp.usage.total_tokens == 3
        assert resp.stop_reason == "tool_use"

    def test_blocks_order_follows_first_appearance(self):
        """blocks 顺序 = 各块首次出现的事件顺序。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="thinking_delta", thinking="t"))
        agg.feed(StreamChunk(type="text_delta", text="a"))
        agg.feed(StreamChunk(type="tool_use_start", tool_use_id="t1", tool_name="f"))
        agg.feed(
            StreamChunk(type="tool_use_delta", tool_use_id="t1", partial_json="{}")
        )

        resp = agg.finalize()

        assert [type(b).__name__ for b in resp.blocks] == [
            "ThinkingBlock",
            "TextBlock",
            "ToolUseBlock",
        ]

    def test_empty_stream_finalize(self):
        """无事件 finalize → 空 blocks、content 为空字符串。"""
        resp = StreamAggregator().finalize()

        assert resp.blocks == []
        assert resp.content == ""
        assert resp.usage is None


class TestStreamAggregatorBlockIndexBuckets:
    """按 block_index 分桶：多段 text/thinking 保持各自块边界与相对顺序。"""

    def test_same_index_text_deltas_merge_into_one_block(self):
        """同一 block_index 的多段 text_delta 合并为单个 TextBlock。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="text_delta", text="你", block_index=0))
        agg.feed(StreamChunk(type="text_delta", text="好", block_index=0))
        agg.feed(StreamChunk(type="text_delta", text="世界", block_index=0))

        resp = agg.finalize()

        assert len(resp.blocks) == 1
        block = resp.blocks[0]
        assert isinstance(block, TextBlock)
        assert block.text == "你好世界"
        assert resp.content == "你好世界"

    def test_interleaved_text_around_tool_use_keeps_wire_order(self):
        """文本 → 工具调用 → 文本：两段文本各自成块，顺序与 wire 一致。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="text_delta", text="A", block_index=0))
        agg.feed(
            StreamChunk(
                type="tool_use_start",
                tool_use_id="t1",
                tool_name="search",
                block_index=1,
            )
        )
        agg.feed(
            StreamChunk(
                type="tool_use_delta",
                tool_use_id="t1",
                partial_json="{}",
                block_index=1,
            )
        )
        agg.feed(StreamChunk(type="text_delta", text="B", block_index=2))

        resp = agg.finalize()

        assert [type(b).__name__ for b in resp.blocks] == [
            "TextBlock",
            "ToolUseBlock",
            "TextBlock",
        ]
        first, middle, last = resp.blocks
        assert first.text == "A"
        assert middle.id == "t1"
        assert last.text == "B"
        assert resp.content == "AB"

    def test_multiple_thinking_blocks_keep_own_signature(self):
        """多段 thinking 各自成块，signature 不跨块拼接（否则端点签名校验失败）。"""
        agg = StreamAggregator()
        agg.feed(
            StreamChunk(
                type="thinking_delta", thinking="思1", signature="s1", block_index=0
            )
        )
        agg.feed(
            StreamChunk(
                type="tool_use_start", tool_use_id="t1", tool_name="f", block_index=1
            )
        )
        agg.feed(
            StreamChunk(
                type="thinking_delta", thinking="思2", signature="s2", block_index=2
            )
        )

        resp = agg.finalize()

        assert [type(b).__name__ for b in resp.blocks] == [
            "ThinkingBlock",
            "ToolUseBlock",
            "ThinkingBlock",
        ]
        first, _, last = resp.blocks
        assert (first.thinking, first.signature) == ("思1", "s1")
        assert (last.thinking, last.signature) == ("思2", "s2")

    def test_unindexed_deltas_keep_single_slot(self):
        """未声明 block_index（openai_compat 等无块概念的 wire）→ 仍合并为单块。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="text_delta", text="A"))
        agg.feed(StreamChunk(type="tool_use_start", tool_use_id="t1", tool_name="f"))
        agg.feed(StreamChunk(type="text_delta", text="B"))

        resp = agg.finalize()

        assert [type(b).__name__ for b in resp.blocks] == ["TextBlock", "ToolUseBlock"]
        assert resp.blocks[0].text == "AB"
        assert resp.content == "AB"

    def test_out_of_order_index_uses_first_appearance_order(self):
        """块序号非单调时按事件首次出现排序，不按序号重排。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="text_delta", text="B", block_index=5))
        agg.feed(StreamChunk(type="text_delta", text="A", block_index=2))

        resp = agg.finalize()

        assert [b.text for b in resp.blocks] == ["B", "A"]
        assert resp.content == "BA"

    def test_empty_text_delta_produces_no_block(self):
        """空文本增量不产出空 TextBlock。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="text_delta", text=""))

        resp = agg.finalize()

        assert resp.blocks == []
        assert resp.content == ""

    def test_unindexed_tool_delta_without_start_still_builds_block(self):
        """无 start 的 tool_use_delta 防御不退化：仍产出 input 完整的块。"""
        agg = StreamAggregator()
        agg.feed(
            StreamChunk(
                type="tool_use_delta",
                tool_use_id="t1",
                partial_json="{}",
                block_index=7,
            )
        )

        resp = agg.finalize()

        assert len(resp.blocks) == 1
        block = resp.blocks[0]
        assert isinstance(block, ToolUseBlock)
        assert block.id == "t1"
        assert block.input == {}


class TestStreamChunkModelField:
    """StreamChunk.model 与 tool_use_stop 事件字段。"""

    def test_tool_use_stop_event_accepted(self):
        """tool_use_stop 是合法事件类型（停点：参数已完整、可校验）。"""
        chunk = StreamChunk(type="tool_use_stop", tool_use_id="t1")
        assert chunk.type == "tool_use_stop"
        assert chunk.tool_use_id == "t1"

    def test_model_field_defaults_empty(self):
        """model 默认为空串（provider 从流事件填入真实模型名）。"""
        assert StreamChunk(type="text_delta", text="x").model == ""

    def test_model_field_settable(self):
        chunk = StreamChunk(type="text_delta", text="x", model="gpt-4o")
        assert chunk.model == "gpt-4o"


class TestStreamAggregatorModelTracking:
    """聚合器跟踪真实 model（非空覆盖）。"""

    def test_finalize_uses_tracked_model(self):
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="text_delta", text="x", model="gpt-4o-real"))
        resp = agg.finalize()
        assert resp.model == "gpt-4o-real"

    def test_finalize_model_empty_without_any_model(self):
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="text_delta", text="x"))
        assert agg.finalize().model == ""

    def test_later_non_empty_model_overrides(self):
        """后续非空 model 覆盖先前值；空值不覆盖。"""
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="text_delta", text="x", model="first"))
        agg.feed(StreamChunk(type="text_delta", text="y"))
        agg.feed(StreamChunk(type="text_delta", text="z", model="second"))
        assert agg.finalize().model == "second"


class TestAggregatorIgnoresToolUseStop:
    """tool_use_stop 停点事件不参与参数累积，安全忽略且不告警。"""

    def test_tool_use_stop_safely_ignored(self):
        agg = StreamAggregator()
        agg.feed(StreamChunk(type="tool_use_start", tool_use_id="t1", tool_name="f"))
        agg.feed(
            StreamChunk(type="tool_use_delta", tool_use_id="t1", partial_json="{}")
        )
        agg.feed(StreamChunk(type="tool_use_stop", tool_use_id="t1"))

        resp = agg.finalize()

        assert len(resp.blocks) == 1
        block = resp.blocks[0]
        assert isinstance(block, ToolUseBlock)
        assert block.input == {}


class TestCollect:
    """模块级 collect()：把事件流消费至结束并聚合为完整响应。"""

    @pytest.mark.asyncio
    async def test_collect_aggregates_stream(self):
        async def _stream():
            yield StreamChunk(type="text_delta", text="Hello", model="gpt-4o")
            yield StreamChunk(type="text_delta", text=" world")
            yield StreamChunk(
                type="usage",
                usage=Usage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
            )
            yield StreamChunk(type="stop", stop_reason="end_turn")

        resp = await collect(_stream())

        assert resp.content == "Hello world"
        assert resp.model == "gpt-4o"
        assert resp.stop_reason == "end_turn"
        assert resp.usage is not None
        assert resp.usage.total_tokens == 5

    @pytest.mark.asyncio
    async def test_collect_empty_stream(self):
        async def _stream():
            if False:  # pragma: no cover - 空 async generator
                yield StreamChunk(type="text_delta", text="")

        resp = await collect(_stream())

        assert resp.content == ""
        assert resp.blocks == []
        assert resp.usage is None
        assert resp.model == ""
