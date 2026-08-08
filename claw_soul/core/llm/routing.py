"""
Routing LLM provider: cheap primary + vision fallback.

Wraps two ``LLMProvider`` instances and delegates each request to one of
them based on whether the message history contains image content. This
lets you keep a cheap text-only model (e.g. DeepSeek) as the default while
seamlessly upgrading to a multimodal model (e.g. Gemini) when the user
sends a photo — without rebuilding the agent or losing conversation
state.

Built by ``claw_soul.main._build_provider()`` when the primary provider
reports ``supports_images = False`` AND a Gemini key is configured.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from .base import LLMProvider

logger = logging.getLogger(__name__)


class RoutingProvider(LLMProvider):
    """Delegate to ``primary`` for text-only turns, ``vision`` when images appear."""

    # The wrapper exposes vision support; the agent's _normalize_input check
    # passes images through, and we route to the vision-capable provider.
    supports_images = True

    def __init__(self, primary: LLMProvider, vision: LLMProvider) -> None:
        self.primary = primary
        self.vision = vision

    # ── Diagnostics ────────────────────────────────────────────────────────
    @property
    def model_name(self) -> str:
        return f"{getattr(self.primary, 'model_name', '?')} + " \
               f"{getattr(self.vision, 'model_name', '?')}(vision)"

    @staticmethod
    def _has_image(messages: list[dict[str, Any]]) -> bool:
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False

    def _pick(self, messages: list[dict[str, Any]]) -> LLMProvider:
        if self._has_image(messages):
            logger.info(
                "[Routing] image detected → vision provider (%s)",
                getattr(self.vision, "model_name", "?"),
            )
            return self.vision
        return self.primary

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = "auto",
        **kwargs: Any,
    ) -> Any:
        return self._pick(messages).chat(
            messages, tools=tools, tool_choice=tool_choice, **kwargs,
        )

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = "auto",
        **kwargs: Any,
    ) -> Generator[dict[str, Any], None, Any]:
        provider = self._pick(messages)
        # `yield from` yields the inner generator's deltas, and its expression
        # value is the inner's return value (the final MockResponse). We MUST
        # explicitly `return` that — otherwise the outer generator falls off
        # the end with StopIteration.value=None, the agent sees no response,
        # and the chat goes silent. (Bare `yield from` here was the cause.)
        return (yield from provider.chat_stream(
            messages, tools=tools, tool_choice=tool_choice, **kwargs,
        ))
