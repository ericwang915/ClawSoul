"""Abstract base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Generator
from typing import Any


class LLMProvider(ABC):
    supports_images: bool = False

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = "auto",
        **kwargs: Any,
    ) -> Any:
        """
        Send a chat request to the LLM.

        All providers must return an object compatible with OpenAI's response
        structure: ``response.choices[0].message.content`` and
        ``response.choices[0].message.tool_calls``.

        Non-OpenAI providers should use the Mock* dataclasses from
        ``llm.response`` to build compatible return values.
        """

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = "auto",
        **kwargs: Any,
    ) -> Generator[dict[str, Any], None, Any]:
        """Stream chat responses.

        Yields dicts with ``{"type": "text_delta", "text": "..."}``
        or ``{"type": "tool_use", ...}`` events.

        Returns the final assembled MockResponse (accessible via
        ``generator.send(None)`` / StopIteration.value).

        Default implementation falls back to non-streaming ``chat()``.
        """
        result = self.chat(
            messages, tools=tools, tool_choice=tool_choice, **kwargs
        )
        msg = result.choices[0].message
        if msg.content:
            yield {"type": "text_delta", "text": msg.content}
        return result
