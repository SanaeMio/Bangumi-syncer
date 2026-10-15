"""跨层工具协议契约测试。

验证场景层（ToolDefinition.to_schema() 输出的内部通用形态）经过各 provider
_build_request 后，wire 格式合法：
- Anthropic：tools 项含 input_schema（不含 parameters）；字符串 tool_choice 对象化
- OpenAI：tools 项为 {"type":"function","function":{...}} 包装；字符串 tool_choice 对象化
- 两者：tool_choice=None / 不传时 body 不含该键

使用 register_match_tools 得到的真实 defns，模拟 loop 末轮
tool_choice="submit_suggestion"。stub bgm 仅提供 search/get_subject/get_related_subjects
空实现，使注册可执行。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.execution.tools import ToolRegistry
from app.services.llm.models import Message
from app.services.llm.providers.anthropic import AnthropicProvider
from app.services.llm.providers.openai_compat import OpenAICompatProvider
from app.services.matching import llm_assist


def _make_stub_bgm() -> MagicMock:
    """构造 stub bgm：仅 register_match_tasks 需要的 search/get_subject/get_related_subjects。"""
    bgm = MagicMock()
    bgm.search.return_value = []
    bgm.get_subject.return_value = {}
    bgm.get_related_subjects.return_value = []
    # 工具 handler 已协程化（走 *_async）；契约测试不执行 handler，仅为对齐协议
    bgm.search_async = AsyncMock(return_value=[])
    bgm.get_subject_async = AsyncMock(return_value={})
    bgm.get_related_subjects_async = AsyncMock(return_value=[])
    return bgm


def _real_tool_schemas() -> list[dict]:
    """通过 register_match_tools 得到真实 defns，取 to_schema().to_wire() 的 flat 形态。"""
    registry = ToolRegistry()
    defns = llm_assist.register_match_tools(registry, _make_stub_bgm())
    return [d.to_schema().to_wire() for d in defns]


class TestCrossLayerAnthropicWire:
    """跨层契约：场景层 flat schema → Anthropic wire 格式合法。"""

    @pytest.fixture()
    def provider(self) -> AnthropicProvider:
        return AnthropicProvider(
            api_base="https://api.anthropic.com/v1",
            api_key="sk-test",
            model="claude-sonnet-4-6",
        )

    @pytest.fixture()
    def schemas(self) -> list[dict]:
        return _real_tool_schemas()

    def test_anthropic_tools_have_input_schema_not_parameters(self, provider, schemas):
        """Anthropic body.tools 每项含 input_schema，不含 parameters（flat → 转换）。"""
        body = provider._build_request(
            [Message(role="user", content="hi")],
            tools=schemas,
            tool_choice="submit_suggestion",
        )
        assert "tools" in body
        for t in body["tools"]:
            assert "input_schema" in t, f"tool {t['name']} 缺少 input_schema"
            assert "parameters" not in t, (
                f"tool {t['name']} 不应含 parameters（应转为 input_schema）"
            )
            assert "name" in t
            assert "description" in t

    def test_anthropic_string_tool_choice_objectized(self, provider, schemas):
        """loop 末轮 tool_choice="submit_suggestion" → {"type":"tool","name":"submit_suggestion"}。"""
        body = provider._build_request(
            [Message(role="user", content="hi")],
            tools=schemas,
            tool_choice="submit_suggestion",
        )
        assert body["tool_choice"] == {"type": "tool", "name": "submit_suggestion"}

    def test_anthropic_tool_choice_none_not_in_body(self, provider, schemas):
        """tool_choice=None 时 body 不含该键（不发送 null）。"""
        body = provider._build_request(
            [Message(role="user", content="hi")],
            tools=schemas,
            tool_choice=None,
        )
        assert "tool_choice" not in body

    def test_anthropic_native_input_schema_tools_passthrough(self, provider):
        """原生 Anthropic 形态（含 input_schema）仍透传（无回归）。"""
        tools = [
            {
                "name": "search_bangumi",
                "description": "按标题搜索番组",
                "input_schema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            }
        ]
        body = provider._build_request(
            [Message(role="user", content="hi")], tools=tools
        )
        assert body["tools"] == tools
        assert "parameters" not in body["tools"][0]

    def test_anthropic_tools_and_tool_choice_absent_when_not_passed(self, provider):
        """未传 tools/tool_choice 时 body 不含这两个键。"""
        body = provider._build_request([Message(role="user", content="hi")])
        assert "tools" not in body
        assert "tool_choice" not in body


class TestCrossLayerOpenAIWire:
    """跨层契约：场景层 flat schema → OpenAI wire 格式合法。"""

    @pytest.fixture()
    def provider(self) -> OpenAICompatProvider:
        return OpenAICompatProvider(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
        )

    @pytest.fixture()
    def schemas(self) -> list[dict]:
        return _real_tool_schemas()

    def test_openai_tools_wrapped_as_function(self, provider, schemas):
        """OpenAI body.tools 每项为 {"type":"function","function":{...}} 包装。"""
        body = provider._build_request(
            [Message(role="user", content="hi")],
            tools=schemas,
            tool_choice="submit_suggestion",
        )
        assert "tools" in body
        for t in body["tools"]:
            assert t["type"] == "function", f"tool 缺少 type=function 包装: {t}"
            assert "function" in t, f"tool 缺少 function 包装: {t}"
            assert "name" in t["function"]
            assert "description" in t["function"]
            assert "parameters" in t["function"]

    def test_openai_string_tool_choice_objectized(self, provider, schemas):
        """tool_choice="submit_suggestion" → {"type":"function","function":{"name":"submit_suggestion"}}。"""
        body = provider._build_request(
            [Message(role="user", content="hi")],
            tools=schemas,
            tool_choice="submit_suggestion",
        )
        assert body["tool_choice"] == {
            "type": "function",
            "function": {"name": "submit_suggestion"},
        }

    def test_openai_tool_choice_none_not_in_body(self, provider, schemas):
        """tool_choice=None 时 body 不含该键（不发送 null）。"""
        body = provider._build_request(
            [Message(role="user", content="hi")],
            tools=schemas,
            tool_choice=None,
        )
        assert "tool_choice" not in body

    def test_openai_native_function_tools_passthrough(self, provider):
        """原生 OpenAI 形态（含 type=function/function 包装）仍透传（无回归）。"""
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
        body = provider._build_request(
            [Message(role="user", content="hi")], tools=tools
        )
        assert body["tools"] == tools

    def test_openai_tools_absent_when_not_passed(self, provider):
        """未传 tools 时 body 不含该键。"""
        body = provider._build_request([Message(role="user", content="hi")])
        assert "tools" not in body
