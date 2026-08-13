"""Verify the cheap-primary + vision-fallback RoutingProvider."""

from __future__ import annotations

from unittest.mock import MagicMock

from herandhim.core.llm.routing import RoutingProvider


def _make_provider(name: str) -> MagicMock:
    p = MagicMock()
    p.model_name = name
    return p


def _text_msgs() -> list[dict]:
    return [
        {"role": "system", "content": "you are kind"},
        {"role": "user",   "content": "hello"},
    ]


def _image_msgs() -> list[dict]:
    return [
        {"role": "system", "content": "you are kind"},
        {"role": "user", "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
        ]},
    ]


def test_text_only_goes_to_primary():
    primary = _make_provider("deepseek-v4-flash")
    vision = _make_provider("gemini-2.5-flash")
    rp = RoutingProvider(primary, vision)

    rp.chat(_text_msgs())

    primary.chat.assert_called_once()
    vision.chat.assert_not_called()


def test_image_goes_to_vision():
    primary = _make_provider("deepseek-v4-flash")
    vision = _make_provider("gemini-2.5-flash")
    rp = RoutingProvider(primary, vision)

    rp.chat(_image_msgs())

    vision.chat.assert_called_once()
    primary.chat.assert_not_called()


def test_image_anywhere_in_history_triggers_vision():
    """Even if the current turn is text, an image earlier in the conversation
    keeps everything on the vision provider (so it can answer 'what was that
    thing earlier?' coherently)."""
    primary = _make_provider("deepseek-v4-flash")
    vision = _make_provider("gemini-2.5-flash")
    rp = RoutingProvider(primary, vision)

    msgs = _image_msgs() + [
        {"role": "assistant", "content": "I see a cat."},
        {"role": "user",      "content": "what color was it?"},
    ]
    rp.chat(msgs)

    vision.chat.assert_called_once()
    primary.chat.assert_not_called()


def test_supports_images_true():
    """RoutingProvider must claim image support so the agent doesn't strip
    images before the routing decision."""
    rp = RoutingProvider(_make_provider("a"), _make_provider("b"))
    assert rp.supports_images is True


def test_chat_stream_delegates_to_picked_provider():
    primary = _make_provider("primary")
    vision = _make_provider("vision")

    def stream_text(*args, **kwargs):
        yield {"type": "text_delta", "text": "hi from primary"}
    def stream_vision(*args, **kwargs):
        yield {"type": "text_delta", "text": "i see a cat"}

    primary.chat_stream.side_effect = stream_text
    vision.chat_stream.side_effect = stream_vision

    rp = RoutingProvider(primary, vision)

    assert list(rp.chat_stream(_text_msgs())) == [{"type": "text_delta", "text": "hi from primary"}]
    assert list(rp.chat_stream(_image_msgs())) == [{"type": "text_delta", "text": "i see a cat"}]


def test_chat_stream_propagates_return_value():
    """The agent reads ``StopIteration.value`` to get the final response.

    Regression guard: a bare ``yield from`` in the wrapper drops the inner
    generator's return value, leaving the agent with ``response = None`` and
    sending an empty reply to the user.
    """
    primary = _make_provider("primary")
    vision = _make_provider("vision")

    sentinel = object()

    def stream_with_return(*args, **kwargs):
        yield {"type": "text_delta", "text": "chunk"}
        return sentinel

    primary.chat_stream.side_effect = stream_with_return

    rp = RoutingProvider(primary, vision)
    gen = rp.chat_stream(_text_msgs())

    chunks = []
    captured_return = None
    while True:
        try:
            chunks.append(next(gen))
        except StopIteration as si:
            captured_return = si.value
            break

    assert chunks == [{"type": "text_delta", "text": "chunk"}]
    assert captured_return is sentinel


def test_kwargs_pass_through():
    primary = _make_provider("p")
    vision = _make_provider("v")
    rp = RoutingProvider(primary, vision)

    rp.chat(_text_msgs(), tools=[{"name": "t"}], temperature=0.4)

    args, kwargs = primary.chat.call_args
    assert kwargs.get("tools") == [{"name": "t"}]
    assert kwargs.get("temperature") == 0.4
