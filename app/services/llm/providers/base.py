"""Abstract base class for LLM providers (Task 1.2).

All LLM provider implementations must subclass BaseProvider and
implement the async chat() method.
"""

from abc import ABC, abstractmethod
from typing import Any

from app.services.llm.models import ChatResponse, Message


class BaseProvider(ABC):
    """Abstract base class for LLM service providers.

    Subclasses must implement the async chat() method that accepts
    a list of messages and additional keyword arguments, returning
    a ChatResponse.
    """

    @abstractmethod
    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """Send messages to the LLM and return the response.

        Args:
            messages: A list of Message objects representing the
                conversation history.
            **kwargs: Provider-specific additional parameters.

        Returns:
            A ChatResponse containing the assistant's reply and
            optional usage statistics.
        """
        ...
