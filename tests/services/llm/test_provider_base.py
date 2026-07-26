"""app.services.llm.providers.base 测试（任务 1.2）。"""

import pytest

from app.services.llm.models import ChatResponse, Message
from app.services.llm.providers.base import BaseProvider


class TestBaseProvider:
    """BaseProvider ABC 接口契约测试。"""

    def test_cannot_instantiate_abstract(self):
        """BaseProvider 无法直接实例化。"""
        with pytest.raises(TypeError):
            BaseProvider()  # type: ignore[abstract]

    def test_subclass_without_chat_raises(self):
        """未实现 chat() 的子类无法实例化。"""
        with pytest.raises(TypeError):

            class IncompleteProvider(BaseProvider):
                pass

            IncompleteProvider()  # type: ignore[abstract]

    @pytest.mark.asyncio
    async def test_properly_implemented_subclass_works(self):
        """实现了 chat() 的子类可以被实例化和调用。"""

        class WorkingProvider(BaseProvider):
            async def chat(self, messages, **kwargs):
                return ChatResponse(content="mocked", model="test-model")

        provider = WorkingProvider()
        resp = await provider.chat([Message(role="user", content="Hello")])
        assert isinstance(resp, ChatResponse)
        assert resp.content == "mocked"
        assert resp.model == "test-model"
