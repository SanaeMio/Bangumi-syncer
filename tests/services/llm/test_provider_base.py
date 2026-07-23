"""Tests for app.services.llm.providers.base (Task 1.2)."""

import pytest

from app.services.llm.models import ChatResponse, Message
from app.services.llm.providers.base import BaseProvider


class TestBaseProvider:
    """BaseProvider ABC contract tests."""

    def test_cannot_instantiate_abstract(self):
        """BaseProvider cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseProvider()  # type: ignore[abstract]

    def test_subclass_without_chat_raises(self):
        """A subclass that doesn't implement chat() cannot be instantiated."""
        with pytest.raises(TypeError):

            class IncompleteProvider(BaseProvider):
                pass

            IncompleteProvider()  # type: ignore[abstract]

    @pytest.mark.asyncio
    async def test_properly_implemented_subclass_works(self):
        """A subclass implementing chat() can be instantiated and called."""

        class WorkingProvider(BaseProvider):
            async def chat(self, messages, **kwargs):
                return ChatResponse(content="mocked", model="test-model")

        provider = WorkingProvider()
        resp = await provider.chat([Message(role="user", content="Hello")])
        assert isinstance(resp, ChatResponse)
        assert resp.content == "mocked"
        assert resp.model == "test-model"
