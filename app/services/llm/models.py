"""Pure data models for the LLM service layer (Task 1.1).

Defines the core Pydantic models used for chat interactions:
Message, Usage, and ChatResponse.
"""

from typing import Literal, Optional

from pydantic import BaseModel


class Message(BaseModel):
    """A single chat message with a role and content."""

    role: Literal["system", "user", "assistant"]
    content: str


class Usage(BaseModel):
    """Token usage statistics for a chat completion."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    """Response from a chat completion request.

    Includes the assistant's reply, the model used, and optional
    token usage statistics.
    """

    content: str
    model: str = ""
    usage: Optional[Usage] = None
