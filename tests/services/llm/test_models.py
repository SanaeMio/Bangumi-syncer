"""Tests for app.services.llm.models (Task 1.1)."""

import pytest

from app.services.llm.models import ChatResponse, Message, Usage


class TestMessage:
    """Message model creation and serialization."""

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
            Message(role="invalid", content="test")

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


class TestUsage:
    """Usage model defaults and serialization."""

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
        assert d == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

    def test_usage_partial_values(self):
        u = Usage(prompt_tokens=5)
        assert u.prompt_tokens == 5
        assert u.completion_tokens == 0
        assert u.total_tokens == 0


class TestChatResponse:
    """ChatResponse model creation, serialization, and optional usage."""

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
        }

    def test_chat_response_default_model(self):
        resp = ChatResponse(content="Hi")
        assert resp.model == ""

    def test_chat_response_extra_fields_ignored(self):
        """Pydantic should ignore unknown fields by default with model_validate."""
        resp = ChatResponse.model_validate(
            {"content": "Hi", "model": "m", "extra": "should-be-ignored"}
        )
        assert resp.content == "Hi"
