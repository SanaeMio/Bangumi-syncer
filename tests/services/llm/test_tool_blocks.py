"""ToolUseBlock / ToolResultBlock 数据模型扩展测试（app.services.llm.models）。

覆盖：
- ToolUseBlock / ToolResultBlock 创建与序列化
- ContentBlock union 可承载五种 block（Text/Thinking/Redacted/ToolUse/ToolResult）
- ChatResponse.blocks 含 tool block 时无损往返
- 向后兼容：现有 Text/Thinking/Redacted 行为不变
"""

from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from app.services.llm.models import (
    ChatResponse,
    ContentBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class TestToolUseBlock:
    """ToolUseBlock 创建与序列化（§3.2.1）。"""

    def test_tool_use_block_create(self):
        b = ToolUseBlock(id="tu_1", name="search_bangumi", input={"title": "eva"})
        assert b.type == "tool_use"
        assert b.id == "tu_1"
        assert b.name == "search_bangumi"
        assert b.input == {"title": "eva"}

    def test_tool_use_block_default_input(self):
        b = ToolUseBlock(id="tu_1", name="search_bangumi")
        assert b.input == {}

    def test_tool_use_block_dump(self):
        b = ToolUseBlock(id="tu_1", name="search_bangumi", input={"title": "eva"})
        assert b.model_dump() == {
            "type": "tool_use",
            "id": "tu_1",
            "name": "search_bangumi",
            "input": {"title": "eva"},
        }

    def test_tool_use_block_type_fixed(self):
        """type 字段被 Literal 强约束，不可改写。"""
        with pytest.raises(ValidationError):
            ToolUseBlock(type=cast("Any", "text"), id="tu_1", name="x")


class TestToolResultBlock:
    """ToolResultBlock 创建与序列化（§3.2.1）。"""

    def test_tool_result_block_create(self):
        b = ToolResultBlock(
            tool_use_id="tu_1", content='{"subject_id": "123"}', is_error=False
        )
        assert b.type == "tool_result"
        assert b.tool_use_id == "tu_1"
        assert b.content == '{"subject_id": "123"}'
        assert b.is_error is False

    def test_tool_result_block_default_is_error(self):
        b = ToolResultBlock(tool_use_id="tu_1", content="ok")
        assert b.is_error is False

    def test_tool_result_block_is_error_true(self):
        b = ToolResultBlock(tool_use_id="tu_1", content="boom", is_error=True)
        assert b.is_error is True

    def test_tool_result_block_dump(self):
        b = ToolResultBlock(tool_use_id="tu_1", content="ok", is_error=True)
        assert b.model_dump() == {
            "type": "tool_result",
            "tool_use_id": "tu_1",
            "content": "ok",
            "is_error": True,
        }

    def test_tool_result_block_type_fixed(self):
        with pytest.raises(ValidationError):
            ToolResultBlock(type=cast("Any", "text"), tool_use_id="tu_1", content="x")


class TestContentBlockUnion:
    """ContentBlock union 可承载全部五种 block（§3.2.1）。"""

    def test_union_carries_five_types(self):
        adapter = TypeAdapter(ContentBlock)
        cases = [
            {"type": "text", "text": "hi"},
            {"type": "thinking", "thinking": "..."},
            {"type": "redacted_thinking", "data": "xxx"},
            {"type": "tool_use", "id": "tu_1", "name": "search_bangumi"},
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
        ]
        expected = [
            TextBlock,
            ThinkingBlock,
            RedactedThinkingBlock,
            ToolUseBlock,
            ToolResultBlock,
        ]
        for raw, klass in zip(cases, expected, strict=True):
            block = adapter.validate_python(raw)
            assert isinstance(block, klass)
            assert block.type == raw["type"]

    def test_union_rejects_genuinely_unknown_block(self):
        """真正未知的 block type 仍应被拒绝（向后强约束）。"""
        adapter = TypeAdapter(ContentBlock)
        with pytest.raises(ValidationError):
            adapter.validate_python({"type": "bogus", "foo": 1})


class TestChatResponseToolRoundTrip:
    """ChatResponse.blocks 含 tool block 时无损往返。"""

    def test_roundtrip_with_tool_use(self):
        block = ToolUseBlock(id="tu_1", name="search_bangumi", input={"title": "eva"})
        resp = ChatResponse(
            content="", blocks=[block], stop_reason="tool_use", model="claude"
        )
        restored = ChatResponse.model_validate(resp.model_dump())
        assert len(restored.blocks) == 1
        rb = restored.blocks[0]
        assert isinstance(rb, ToolUseBlock)
        assert rb.id == "tu_1"
        assert rb.name == "search_bangumi"
        assert rb.input == {"title": "eva"}
        assert restored.stop_reason == "tool_use"

    def test_roundtrip_with_tool_result(self):
        block = ToolResultBlock(tool_use_id="tu_1", content="result", is_error=False)
        resp = ChatResponse(content="", blocks=[block])
        restored = ChatResponse.model_validate(resp.model_dump())
        rb = restored.blocks[0]
        assert isinstance(rb, ToolResultBlock)
        assert rb.tool_use_id == "tu_1"
        assert rb.content == "result"
        assert rb.is_error is False

    def test_roundtrip_mixed_blocks_preserves_order_and_type(self):
        blocks = [
            TextBlock(text="let me look"),
            ToolUseBlock(id="tu_1", name="search_bangumi", input={"title": "eva"}),
            ToolResultBlock(tool_use_id="tu_1", content="found"),
            ThinkingBlock(thinking="hmm"),
        ]
        resp = ChatResponse(content="done", blocks=blocks)
        restored = ChatResponse.model_validate(resp.model_dump())
        assert [type(b) for b in restored.blocks] == [
            TextBlock,
            ToolUseBlock,
            ToolResultBlock,
            ThinkingBlock,
        ]
        b0, b1, b2, b3 = restored.blocks
        assert isinstance(b0, TextBlock)
        assert isinstance(b1, ToolUseBlock)
        assert isinstance(b2, ToolResultBlock)
        assert isinstance(b3, ThinkingBlock)
        assert b0.text == "let me look"
        assert b1.name == "search_bangumi"
        assert b2.content == "found"
        assert b3.thinking == "hmm"


class TestBackwardCompatibility:
    """向后兼容：现有 Text/Thinking/Redacted 行为不变。"""

    def test_text_block_unchanged(self):
        b = TextBlock(text="hi")
        assert b.type == "text"
        assert b.text == "hi"
        assert b.model_dump() == {"type": "text", "text": "hi"}

    def test_thinking_block_unchanged(self):
        b = ThinkingBlock(thinking="思考", signature="sig1")
        assert b.type == "thinking"
        assert b.thinking == "思考"
        assert b.signature == "sig1"
        # 无 signature 仍可选
        b2 = ThinkingBlock(thinking="思考")
        assert b2.signature is None

    def test_redacted_block_unchanged(self):
        b = RedactedThinkingBlock(data="xxx")
        assert b.type == "redacted_thinking"
        assert b.data == "xxx"

    def test_message_text_content_still_str(self):
        from app.services.llm.models import Message

        msg = Message(role="user", content="纯文本")
        assert isinstance(msg.content, str)
        assert msg.content == "纯文本"
