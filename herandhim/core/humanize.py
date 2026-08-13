"""
Humanize — the shared "texts like a real person" toolkit.

Every delivery-side behaviour that makes the companion feel human lives here,
channel-agnostic, so the Telegram bot and the proactive scheduler share ONE
implementation:

  - reply_delay()   — a length-scaled, jittered pause before sending (slower
                      during her local sleep hours), so replies aren't instant.
  - split_burst()   — split a reply into 1–3 message bubbles on blank lines,
                      the way a real texter fires consecutive messages.
  - send_burst()    — deliver the bubbles with typing actions + human gaps.
  - pick_reaction() / maybe_react()
                    — a selective emoji reaction on the user's message (photo
                      ❤️, laughter 🤣, wins 🎉…), fired BEFORE the reply, like
                      a human seeing the message first. Selective by design:
                      reacting to everything reads as a bot.
  - humanize_gap()  — "about 3 hours" phrasing for absence awareness.
  - open_thread()   — the most emotionally-significant recent event with its
                      actual content, for specific follow-ups.

These once lived in one delivery path while another kept an older, flatter
version. Keeping them here is what stops that drift from coming back: a
realism fix landed once shows up everywhere she speaks.
"""

from __future__ import annotations

import asyncio
import logging
import random

logger = logging.getLogger(__name__)


# ── Reply pacing ──────────────────────────────────────────────────────────


def reply_delay(text: str) -> float:
    """A short, human-feeling pause before sending — scales with length +
    jitter, longer during her local sleep hours, hard-capped so it never
    feels laggy."""
    n = len(text or "")
    base = 0.7 + min(n, 360) * 0.011          # ~0.7s + up to ~4s for long replies
    delay = base * random.uniform(0.6, 1.5)
    try:
        from . import timectx
        if 1 <= timectx.now_in_bot_tz().hour < 7:
            delay += random.uniform(2.0, 6.0)  # groggy / slow at her night
    except Exception:
        pass
    return min(delay, 9.0)


# ── Multi-message bursts ──────────────────────────────────────────────────


def split_burst(text: str, max_parts: int = 3) -> list[str]:
    """Split a reply into 1–3 message bubbles on blank lines, like a real
    texter firing off consecutive messages.

    The persona prompt already asks her to write in short paragraphs
    separated by blank lines — those ARE the natural bubble boundaries.
    Merges down to ``max_parts`` by joining the shortest neighbours, so we
    never chop mid-thought. Single-paragraph replies pass through as one
    message. Each part respects Telegram's 4096-char limit.
    """
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) <= 1:
        return [text[:4096]]
    while len(paras) > max_parts:
        i = min(range(len(paras) - 1),
                key=lambda j: len(paras[j]) + len(paras[j + 1]))
        paras[i:i + 2] = [paras[i] + "\n\n" + paras[i + 1]]
    return [p[:4096] for p in paras]


async def send_burst(bot, chat_id, text: str) -> bool:
    """Send a reply as 1–3 consecutive bubbles with human typing gaps.

    Between bubbles: a typing action + a short pause scaled to the length of
    the UPCOMING bubble (you type before you send), so the rhythm reads like
    a person, not a queue flush. Returns True if at least one bubble sent.
    """
    parts = split_burst(text)
    sent = False
    for i, part in enumerate(parts):
        if i > 0:
            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            gap = 0.8 + min(len(part), 200) * 0.012 * random.uniform(0.6, 1.4)
            await asyncio.sleep(min(gap, 3.5))
        try:
            await bot.send_message(chat_id=chat_id, text=part)
            sent = True
        except Exception as exc:
            logger.warning("[humanize] burst send failed (part %d/%d): %s",
                           i + 1, len(parts), exc)
            break
    return sent


# ── Emoji reactions ───────────────────────────────────────────────────────

# Telegram only allows a fixed emoji set for reactions; these are all legal.
_REACT_PHOTO = ["❤️", "😍", "🥰", "🔥"]
_REACT_FUNNY = ["🤣", "😁"]
_REACT_SAD   = ["😢", "🤗"]
_REACT_LOVE  = ["😘", "❤️"]
_REACT_WIN   = ["🎉", "🏆", "👏"]


def pick_reaction(has_image: bool, text: str) -> str | None:
    """A human reacts to SOME messages — a photo almost always, a big feeling
    sometimes, ordinary logistics never. Returns an emoji or None."""
    t = (text or "").lower()
    if has_image and random.random() < 0.85:
        return random.choice(_REACT_PHOTO)
    if any(k in t for k in ("哈哈", "笑死", "😂", "🤣", "lmao", "lol", "haha")):
        return random.choice(_REACT_FUNNY) if random.random() < 0.5 else None
    if any(k in t for k in ("想你", "爱你", "miss you", "love you", "爱死")):
        return random.choice(_REACT_LOVE) if random.random() < 0.6 else None
    if any(k in t for k in ("难过", "哭了", "累死", "崩溃", "😭", "sad", "awful", "terrible")):
        return random.choice(_REACT_SAD) if random.random() < 0.5 else None
    if any(k in t for k in ("通过了", "成功", "拿到", "offer", "升职", "过了", "passed", "got the job", "nailed")):
        return random.choice(_REACT_WIN) if random.random() < 0.7 else None
    return None


async def maybe_react(bot, chat_id, message_id, has_image: bool, text: str) -> None:
    """Best-effort emoji reaction on the user's message — the instant
    acknowledgment a real texter gives before typing a reply."""
    emoji = pick_reaction(has_image, text)
    if not emoji or message_id is None:
        return
    try:
        from telegram import ReactionTypeEmoji
        await bot.set_message_reaction(
            chat_id=chat_id, message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji)],
        )
    except Exception as exc:
        logger.debug("[humanize] reaction skipped: %s", exc)


# ── Absence & follow-up awareness ─────────────────────────────────────────


def humanize_gap(mins: float) -> str:
    if mins < 60:
        return f"about {int(mins)} minutes"
    hrs = mins / 60.0
    if hrs < 24:
        n = int(round(hrs))
        return f"about {n} hour{'s' if n != 1 else ''}"
    days = int(round(hrs / 24.0))
    return f"about {days} day{'s' if days != 1 else ''}"


def open_thread(agent) -> str:
    """The most emotionally-significant recent event (last 2 days) with its
    actual content — '{topic}: \"{summary}\"' — or '' if nothing qualifies.

    Prefers negative / high-intensity events (the ones a caring partner would
    circle back on) and skips filler topics.
    """
    try:
        events = agent.memory.emotional_graph.get_recent(days=2)
    except Exception:
        return ""
    cands = [
        e for e in events
        if (e.get("context_summary") or "").strip()
        and (e.get("topic") or "").lower() not in ("general", "greeting", "")
    ]
    if not cands:
        return ""

    def _score(e: dict) -> float:
        s = float(e.get("intensity", 0) or 0)
        if e.get("sentiment") == "negative":
            s += 0.5
        return s

    best = max(cands, key=_score)
    return f"{best.get('topic')}: \"{best.get('context_summary')}\""


__all__ = ["reply_delay", "split_burst", "send_burst", "pick_reaction",
           "maybe_react", "humanize_gap", "open_thread"]
