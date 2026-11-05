"""OpenAI 兼容 provider 工具协议测试。

覆盖 _build_request 的 tool 拆并与 tools/tool_choice 透传，
以及 wire→内部历史消息合并。
"""

import json

from app.services.llm.models import (
    ContentBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from app.services.llm.providers.openai_compat import OpenAICompatProvider


class TestOpenAICompatToolsBuildRequest:
    """_build_request：内部模型 → OpenAI wire 工具协议拆并。"""

    def _provider(self, **kwargs):
        return OpenAICompatProvider(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
            **kwargs,
        )

    def test_tool_use_blocks_to_assistant_tool_calls(self):
        """assistant 消息含多个 ToolUseBlock → 聚合为单条 assistant.tool_calls，
        arguments 为 JSON 字符串。"""
        provider = self._provider()
        messages = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="call_1", name="search_bangumi", input={"title": "测试"}
                    ),
                    ToolUseBlock(
                        id="call_2", name="get_detail", input={"subject_id": "123"}
                    ),
                ],
            )
        ]
        wire = provider._build_request(messages)["messages"]
        assert len(wire) == 1
        assert wire[0]["role"] == "assistant"
        assert wire[0]["content"] is None
        tcs = wire[0]["tool_calls"]
        assert len(tcs) == 2
        assert tcs[0]["id"] == "call_1"
        assert tcs[0]["type"] == "function"
        assert tcs[0]["function"]["name"] == "search_bangumi"
        assert json.loads(tcs[0]["function"]["arguments"]) == {"title": "测试"}
        assert tcs[1]["function"]["name"] == "get_detail"
        assert json.loads(tcs[1]["function"]["arguments"]) == {"subject_id": "123"}

    def test_assistant_text_and_tool_use_coexist(self):
        """assistant 文本与 tool_use 共存：content 字段与 tool_calls 同消息。"""
        provider = self._provider()
        messages = [
            Message(
                role="assistant",
                content=[
                    TextBlock(text="我先查一下"),
                    ToolUseBlock(
                        id="call_1", name="search_bangumi", input={"title": "x"}
                    ),
                ],
            )
        ]
        wire = provider._build_request(messages)["messages"]
        assert wire[0]["content"] == "我先查一下"
        assert wire[0]["tool_calls"][0]["id"] == "call_1"

    def test_tool_result_blocks_to_role_tool_messages(self):
        """user 消息含多个 ToolResultBlock → 拆为多条 role=tool 消息，
        tool_call_id 对应；is_error 加 [ERROR] 前缀。"""
        provider = self._provider()
        messages = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="call_1", content="结果A"),
                    ToolResultBlock(
                        tool_use_id="call_2", content="结果B", is_error=True
                    ),
                ],
            )
        ]
        wire = provider._build_request(messages)["messages"]
        assert len(wire) == 2
        assert wire[0] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "结果A",
        }
        assert wire[1] == {
            "role": "tool",
            "tool_call_id": "call_2",
            "content": "[ERROR] 结果B",
        }

    def test_mixed_text_and_tool_result(self):
        """user 消息混排 text + tool_result：文本先输出独立 user 消息，
        再拆 tool 消息。"""
        provider = self._provider()
        messages = [
            Message(
                role="user",
                content=[
                    TextBlock(text="用户说：请查"),
                    ToolResultBlock(tool_use_id="call_1", content="结果"),
                ],
            )
        ]
        wire = provider._build_request(messages)["messages"]
        assert wire[0] == {"role": "user", "content": "用户说：请查"}
        assert wire[1] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "结果",
        }

    def test_tools_passthrough(self):
        """tools 参数由调用方构造 dict 列表，provider 透传。"""
        provider = self._provider()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_bangumi",
                    "description": "搜索",
                    "parameters": {"type": "object"},
                },
            }
        ]
        body = provider._build_request([Message(role="user", content="Q")], tools=tools)
        assert body["tools"] == tools

    def test_tool_choice_passthrough(self):
        """tool_choice 以 function 模式透传。"""
        provider = self._provider()
        tool_choice = {"type": "function", "function": {"name": "search_bangumi"}}
        body = provider._build_request(
            [Message(role="user", content="Q")], tool_choice=tool_choice
        )
        assert body["tool_choice"] == tool_choice

    def test_flat_parameters_wrapped_as_function(self):
        """flat 形态（parameters）→ 包装为 {"type":"function","function":{...}}。"""
        provider = self._provider()
        tools = [
            {
                "name": "search_bangumi",
                "description": "按标题搜索番组",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        body = provider._build_request([Message(role="user", content="Q")], tools=tools)
        assert "tools" in body
        t = body["tools"][0]
        assert t["type"] == "function"
        assert "function" in t
        assert t["function"]["name"] == "search_bangumi"
        assert t["function"]["description"] == "按标题搜索番组"
        assert t["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_string_tool_choice_auto_required_none_passthrough(self):
        """tool_choice 字符串 "none"/"auto"/"required" 原样透传。"""
        provider = self._provider()
        for choice in ("none", "auto", "required"):
            body = provider._build_request(
                [Message(role="user", content="Q")], tool_choice=choice
            )
            assert body["tool_choice"] == choice

    def test_string_tool_choice_tool_name_objectized(self):
        """tool_choice 字符串（工具名）→ {"type":"function","function":{"name":<str>}}。"""
        provider = self._provider()
        body = provider._build_request(
            [Message(role="user", content="Q")],
            tool_choice="submit_suggestion",
        )
        assert body["tool_choice"] == {
            "type": "function",
            "function": {"name": "submit_suggestion"},
        }

    def test_tool_choice_none_not_in_body(self):
        """tool_choice=None 时 body 不含该键（不发送 null）。"""
        provider = self._provider()
        body = provider._build_request(
            [Message(role="user", content="Q")],
            tool_choice=None,
        )
        assert "tool_choice" not in body

    def test_native_function_tools_passthrough(self):
        """原生 OpenAI 形态（含 type=function/function 包装）透传（无回归）。"""
        provider = self._provider()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_bangumi",
                    "description": "搜索",
                    "parameters": {"type": "object"},
                },
            }
        ]
        body = provider._build_request([Message(role="user", content="Q")], tools=tools)
        assert body["tools"] == tools

    def test_cache_control_not_in_body(self):
        """cache_control 是 Anthropic 专属参数，OpenAI 请求体不得包含。"""
        provider = self._provider()
        body = provider._build_request(
            [Message(role="user", content="Q")],
            cache_control={"type": "ephemeral"},
        )
        assert "cache_control" not in body


class TestOpenAICompatWireToMessages:
    """_wire_to_messages：wire → 内部历史消息合并。"""

    def _provider(self):
        return OpenAICompatProvider(
            api_base="https://api.openai.com/v1", api_key="sk-test"
        )

    def test_consecutive_role_tool_merged_to_tool_results(self):
        """连续的 role=tool 消息 → 合并回一条 user 消息的多个 ToolResultBlock。"""
        provider = self._provider()
        wire = [
            {"role": "user", "content": "请查"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "结果A"},
            {"role": "tool", "tool_call_id": "call_2", "content": "[ERROR] 结果B"},
            {"role": "user", "content": "继续"},
        ]
        messages = provider._wire_to_messages(wire)

        tool_msgs = [
            m
            for m in messages
            if isinstance(m.content, list)
            and any(isinstance(b, ToolResultBlock) for b in m.content)
        ]
        assert len(tool_msgs) == 1
        blocks: list[ContentBlock] = tool_msgs[0].content
        assert len(blocks) == 2
        rb0, rb1 = blocks[0], blocks[1]
        assert isinstance(rb0, ToolResultBlock)
        assert isinstance(rb1, ToolResultBlock)
        assert rb0.tool_use_id == "call_1"
        assert rb0.content == "结果A"
        assert rb0.is_error is False
        assert rb1.tool_use_id == "call_2"
        assert rb1.content == "结果B"
        assert rb1.is_error is True

        # 普通文本 user 消息原样保留
        assert any(m.role == "user" and m.content == "请查" for m in messages)
        assert any(m.role == "user" and m.content == "继续" for m in messages)

    def test_role_tool_error_prefix_stripped_on_merge(self):
        """合并时 [ERROR] 前缀还原为 is_error 标记，内容去掉前缀。"""
        provider = self._provider()
        wire = [{"role": "tool", "tool_call_id": "c1", "content": "[ERROR] boom"}]
        messages = provider._wire_to_messages(wire)
        assert len(messages) == 1
        blocks = messages[0].content
        assert blocks[0].is_error is True
        assert blocks[0].content == "boom"
