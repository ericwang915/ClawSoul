"""
Per-user quotas — disk usage + daily message budget.

These cheap checks live in their own module so anywhere that wants to gate
a runaway operation (selfie spam, write_file bombs, chat-flooding) can call
``check_disk()`` or ``check_messages()`` without dragging in a heavy
SessionManager / billing context.

Both quotas are cached in-process for performance; cache TTL is short
(15-60 s) so a real-time check after a write still picks up the delta.

Config keys (under ``quota`` in claw_soul.json):

    "quota": {
        "diskMb": 200,             # per-user volume budget
        "dailyMessages": 200,      # per-user chat messages per UTC day
        "checkEverySeconds": 30    # caching TTL
    }
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .. import config

logger = logging.getLogger(__name__)


DEFAULT_DISK_MB = 200
DEFAULT_DAILY_MESSAGES = 200
DEFAULT_CACHE_TTL = 30


@dataclass
class QuotaState:
    disk_bytes: int = 0
    messages_today: int = 0
    last_check: float = 0.0
    last_day: str = ""


# Process-wide cache keyed by tenant home path
_STATE: dict[str, QuotaState] = {}


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _disk_mb_limit() -> int:
    return int(config.get_int("quota", "diskMb", default=DEFAULT_DISK_MB) or DEFAULT_DISK_MB)


def _daily_messages_limit() -> int:
    return int(
        config.get_int("quota", "dailyMessages", default=DEFAULT_DAILY_MESSAGES)
        or DEFAULT_DAILY_MESSAGES
    )


def _ttl() -> int:
    return int(
        config.get_int("quota", "checkEverySeconds", default=DEFAULT_CACHE_TTL)
        or DEFAULT_CACHE_TTL
    )


def _home() -> str:
    return os.path.realpath(str(config.CLAWSOUL_HOME))


def _state() -> QuotaState:
    """Get or create the quota state for the current tenant."""
    home = _home()
    state = _STATE.get(home)
    if state is None:
        state = _STATE[home] = QuotaState()
    today = _today_key()
    if state.last_day != today:
        state.messages_today = 0
        state.last_day = today
    return state


# ── Disk usage ──────────────────────────────────────────────────────────────

def _measure_disk(home: str) -> int:
    """Walk the tenant home and sum file sizes (cheap enough at MB-scale)."""
    total = 0
    for root, _dirs, files in os.walk(home):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def disk_status() -> dict[str, Any]:
    """Return current usage + limit. Refreshes the cache if TTL expired."""
    state = _state()
    home = _home()
    if time.monotonic() - state.last_check > _ttl():
        state.disk_bytes = _measure_disk(home) if os.path.isdir(home) else 0
        state.last_check = time.monotonic()
    limit_b = _disk_mb_limit() * 1024 * 1024
    return {
        "used_bytes": state.disk_bytes,
        "limit_bytes": limit_b,
        "used_mb": round(state.disk_bytes / 1024 / 1024, 2),
        "limit_mb": _disk_mb_limit(),
        "over": state.disk_bytes >= limit_b,
    }


def check_disk(extra_bytes: int = 0) -> str | None:
    """Return an error string if writing *extra_bytes* would exceed the quota.

    Call this BEFORE the actual write — once you have a reasonable estimate
    of how big the new file is. Returns ``None`` when the write is allowed.
    """
    s = disk_status()
    if s["used_bytes"] + extra_bytes >= s["limit_bytes"]:
        return (
            f"Disk quota exceeded: used {s['used_mb']} MB of {s['limit_mb']} MB. "
            "Old photos auto-prune at 30 days; delete unneeded files to free space."
        )
    return None


# ── Daily message budget ────────────────────────────────────────────────────

def message_status() -> dict[str, Any]:
    """Today's count vs daily limit."""
    state = _state()
    limit = _daily_messages_limit()
    return {
        "used": state.messages_today,
        "limit": limit,
        "remaining": max(0, limit - state.messages_today),
        "over": state.messages_today >= limit,
        "resets_utc": _today_key() + " 24:00 UTC",
    }


def check_messages() -> str | None:
    """Return an error string if the user has hit their daily message limit.

    Call this BEFORE invoking the LLM. Returns ``None`` when the chat is allowed.
    """
    s = message_status()
    if s["over"]:
        return (
            f"Daily message budget exhausted ({s['used']}/{s['limit']}). "
            f"Resets at 00:00 UTC tomorrow. "
            "Need more? Ask the operator to bump your `quota.dailyMessages` cap."
        )
    return None


def record_message() -> None:
    """Increment the daily counter. Call exactly once per user chat turn."""
    state = _state()
    state.messages_today += 1


def reset_for_tests() -> None:
    """Drop all cached state — for tests that switch tenants in one process."""
    _STATE.clear()
