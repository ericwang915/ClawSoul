"""
Chat-language helper used wherever code needs to choose between CN and
EN copy that gets fed back to the agent's chat surface.

Three classes of strings need to honour the configured language so an
English persona doesn't get yanked into Chinese mid-conversation:

  1. Prompts sent as ``user``-role messages to the LLM (proactive,
     planner, wishlist).  A CN system rule "reply in English only" is
     out-voted by an immediate CN ``user`` instruction.
  2. Context blocks the agent loads on boot (memory headers, emotional
     graph labels, milestone strings).  These ride inside the system
     prompt as inline facts; whatever language they're in pulls the
     model toward the same.
  3. Captions / system replies the user actually sees on TG ("photo
     was sent", "/reset done", relationship-age strings).

Lives in ``claw_soul.core`` so every layer (scheduler, memory, agent
itself, channel code) can read it without inventing a circular import.
"""

from __future__ import annotations

from .. import config


def chat_lang() -> str:
    """Lower-cased configured chat language code (e.g. ``en``, ``zh-cn``)."""
    return (config.get_str("agent", "language", default="en") or "en").lower()


def is_chinese() -> bool:
    """True when the persona is configured for any Chinese variant."""
    return chat_lang().startswith("zh")


__all__ = ["chat_lang", "is_chinese"]
