"""
In-process ledger of unanswered proactive messages, per session.

The companion is allowed a flash of "you ignored me" when she's reached out
several times with no reply — but only if that's actually true. The hosted
build counted ``proactive_sent`` events in Postgres; a single-user install has
no database, so we keep the count in memory:

    proactive sends  → record_sent(session_id)
    user replies     → clear(session_id)
    reply generation → unanswered(session_id)

Deliberately not persisted. A restart resets the streak to zero, which fails in
the forgiving direction: worst case she doesn't sulk about messages she sent
before the process came up. Persisting it would risk the opposite — greeting
someone with a grudge from last week.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_unanswered: dict[str, int] = {}


def record_sent(session_id: str) -> int:
    """Count one proactive message as sent-and-not-yet-answered."""
    with _lock:
        n = _unanswered.get(session_id, 0) + 1
        _unanswered[session_id] = n
        return n


def unanswered(session_id: str) -> int:
    """How many proactive messages are still unanswered in this session."""
    with _lock:
        return _unanswered.get(session_id, 0)


def clear(session_id: str) -> None:
    """The user said something — the streak is over."""
    with _lock:
        _unanswered.pop(session_id, None)


__all__ = ["record_sent", "unanswered", "clear"]
