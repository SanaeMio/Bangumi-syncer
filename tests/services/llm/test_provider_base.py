"""app.services.llm.providers.base 测试。"""

from typing import Any, cast

import pytest

from app.services.llm.models import Message, StreamChunk
from app.services.llm.providers.base import BaseProvider


class TestBaseProvider:
    """BaseProvider ABC 接口契约测试。

    chat() 抽象方法已删除，stream() 提升为唯一抽象入口。
    """

    def test_cannot_instantiate_abstract(self):
        """BaseProvider 无法直接实例化。"""
        with pytest.raises(TypeError):
            cast("Any", BaseProvider)()

    def test_subclass_without_stream_raises(self):
        """未实现 stream() 的子类无法实例化。"""
        with pytest.raises(TypeError):

            class IncompleteProvider(BaseProvider):
                pass

            cast("Any", IncompleteProvider)()

    @pytest.mark.asyncio
    async def test_properly_implemented_subclass_works(self):
        """实现了 stream() 的子类可以被实例化和调用。"""

        class WorkingProvider(BaseProvider):
            async def stream(self, messages, **kwargs):
                yield StreamChunk(type="text_delta", text="mocked", model="test-model")

        provider = WorkingProvider()
        chunks = [
            c async for c in provider.stream([Message(role="user", content="Hello")])
        ]
        assert chunks[0].text == "mocked"
        assert chunks[0].model == "test-model"
