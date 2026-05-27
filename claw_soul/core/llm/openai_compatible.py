"""OpenAI-compatible LLM provider.

Works with any API that follows the OpenAI chat-completions contract:
DeepSeek, Grok (xAI), Kimi (Moonshot), GLM (Zhipu), and others.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from openai import OpenAI

from .base import LLMProvider
from .response import MockChoice, MockFunction, MockMessage, MockResponse, MockToolCall


def _promote_reasoning_to_content(resp: Any) -> None:
    """If a response's `content` is empty but `reasoning_content` is set
    (DeepSeek V4 quirk), copy reasoning into content so callers see the
    actual answer rather than a blank message bubble.
    """
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError):
        return
    if msg is None or getattr(msg, "content", None):
        return
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        try:
            msg.content = reasoning
        except Exception:
            pass


class OpenAICompatibleProvider(LLMProvider):
    """Thin wrapper around the OpenAI SDK for chat completions."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_name: str,
        supports_images: bool | None = None,
    ) -> None:
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=300.0,
        )
        self.model_name = model_name
        # Auto-detect vision support from the model name. Only explicit
        # OpenAI-compatible vision endpoints make the cut.
        #
        # NOTE on DeepSeek: as of 2026-05-26, the hosted DeepSeek API still
        # 400s on `image_url` content for every published model id we have
        # access to (verified empirically — ``deepseek-chat``,
        # ``deepseek-reasoner`` AND ``deepseek-v4-flash`` all reject it with
        # "unknown variant `image_url`, expected `text`"). The marketing
        # material about V4 being multimodal is ahead of the hosted API.
        # When DeepSeek opens it for real, re-add `deepseek-v4` here.
        if supports_images is None:
            n = (model_name or "").lower()
            supports_images = (
                n.startswith("deepseek-vl")        # self-hosted VL2 family only
                or "vision" in n                   # gpt-4-vision, grok-vision, …
                or n.startswith("qwen-vl") or n.startswith("qwen2-vl") or n.startswith("qwen2.5-vl")
                or n.startswith("llava")           # llava-1.5, llava-next, …
                or n.startswith("internvl")
                or n.startswith("yi-vl")
                or n.startswith("moonshot-v1-vision")
            )
        self.supports_images = supports_images

    def _scrub_images_if_unsupported(
        self, messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Defensive: when the model can't see images, walk every message and
        flatten any ``image_url`` content parts that may have leaked in from
        an earlier vision-enabled turn or from session replay. Without this,
        the live agent's in-memory history keeps poisoning subsequent text
        turns with a 400 from the API.
        """
        if self.supports_images:
            return messages
        scrubbed: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                scrubbed.append(msg)
                continue
            text_bits: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    text_bits.append(str(part))
                elif part.get("type") == "text":
                    text_bits.append(str(part.get("text", "")))
                elif part.get("type") == "image_url":
                    text_bits.append("[image attached — vision not enabled on this model]")
            scrubbed.append({**msg, "content": "\n".join(b for b in text_bits if b)})
        return scrubbed

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = "auto",
        **kwargs: Any,
    ) -> Any:
        req: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._scrub_images_if_unsupported(messages),
            **kwargs,
        }
        if tools:
            req["tools"] = tools
            req["tool_choice"] = tool_choice

        resp = self.client.chat.completions.create(**req)
        _promote_reasoning_to_content(resp)
        return resp

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = "auto",
        **kwargs: Any,
    ) -> Generator[dict[str, Any], None, Any]:
        """Stream OpenAI-compatible responses, yielding text deltas."""
        req: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._scrub_images_if_unsupported(messages),
            "stream": True,
            **kwargs,
        }
        if tools:
            req["tools"] = tools
            req["tool_choice"] = tool_choice

        content_text = ""
        reasoning_text = ""  # DeepSeek V4 puts the actual answer here when in
                              # thinking mode; we fall back to it if `content`
                              # ends up empty.
        tool_calls_acc: dict[int, dict] = {}

        for chunk in self.client.chat.completions.create(**req):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            if delta.content:
                content_text += delta.content
                yield {"type": "text_delta", "text": delta.content}

            # Some providers (notably DeepSeek V4) stream the visible answer
            # via `reasoning_content` instead of `content`. Capture it so we
            # can fall back if `content_text` is empty at end-of-stream.
            r = getattr(delta, "reasoning_content", None)
            if r:
                reasoning_text += r
                yield {"type": "text_delta", "text": r}

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "args": "",
                        }
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_acc[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_acc[idx]["args"] += (
                                tc_delta.function.arguments
                            )

        mock_tool_calls = [
            MockToolCall(
                id=v["id"],
                function=MockFunction(name=v["name"], arguments=v["args"] or "{}"),
            )
            for v in sorted(tool_calls_acc.values(), key=lambda x: x["id"])
        ]

        # If the provider only filled reasoning_content (e.g. DeepSeek V4-Flash
        # for tiny prompts), surface that as the visible response.
        final_content = content_text or reasoning_text or None

        return MockResponse(choices=[
            MockChoice(message=MockMessage(
                content=final_content,
                tool_calls=mock_tool_calls or None,
            ))
        ])
