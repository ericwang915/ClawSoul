"""
Local resource guards — disk usage and optional self-imposed rate caps.

None of this is billing. You pay for your own API calls, so every limit
defaults to off. They exist because
running a companion on a small VPS, or for a household, is a real thing and
a runaway loop shouldn't quietly fill the disk.

All keys are opt-in, under ``quota`` in claw_soul.json:

    "quota": {
        "diskMb": 0,               # 0 = unlimited (default)
        "dailyMessages": 0,        # 0 = unlimited (default)
        "dailyPhotos": 0,          # 0 = unlimited (default)
        "checkEverySeconds": 30    # disk-measurement cache TTL
    }

Refusals are phrased in her voice, not as system errors — if a limit is ever
reached the user should hear a person saying "let's pick this up tomorrow",
never a quota exception.
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


UNLIMITED = 0
DEFAULT_CACHE_TTL = 30


@dataclass
class QuotaState:
    disk_bytes: int = 0
    messages_today: int = 0
    photos_today: int = 0
    last_check: float = 0.0
    last_day: str = ""


_STATE: dict[str, QuotaState] = {}


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _limit(key: str) -> int:
    """A configured cap, or UNLIMITED when unset/zero/negative."""
    try:
        val = int(config.get_int("quota", key, default=UNLIMITED) or UNLIMITED)
    except (TypeError, ValueError):
        return UNLIMITED
    return val if val > 0 else UNLIMITED


def _ttl() -> int:
    return int(
        config.get_int("quota", "checkEverySeconds", default=DEFAULT_CACHE_TTL)
        or DEFAULT_CACHE_TTL
    )


def _home() -> str:
    return os.path.realpath(str(config.CLAWSOUL_HOME))


def _state() -> QuotaState:
    home = _home()
    state = _STATE.get(home)
    if state is None:
        state = _STATE[home] = QuotaState()
    today = _today_key()
    if state.last_day != today:
        state.messages_today = 0
        state.photos_today = 0
        state.last_day = today
    return state


def _is_zh() -> bool:
    return (config.get_str("agent", "language", default="en") or "en").lower().startswith("zh")


# ── Disk usage ──────────────────────────────────────────────────────────────

def _measure_disk(home: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(home):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def disk_status() -> dict[str, Any]:
    """Current usage + limit. Only walks the disk when a cap is configured —
    an unlimited install shouldn't pay for a measurement nobody reads."""
    state = _state()
    limit_mb = _limit("diskMb")
    if limit_mb == UNLIMITED:
        return {"used_bytes": 0, "limit_bytes": 0, "used_mb": 0.0,
                "limit_mb": 0, "unlimited": True, "over": False}

    home = _home()
    if time.monotonic() - state.last_check > _ttl():
        state.disk_bytes = _measure_disk(home) if os.path.isdir(home) else 0
        state.last_check = time.monotonic()
    limit_b = limit_mb * 1024 * 1024
    return {
        "used_bytes": state.disk_bytes,
        "limit_bytes": limit_b,
        "used_mb": round(state.disk_bytes / 1024 / 1024, 2),
        "limit_mb": limit_mb,
        "unlimited": False,
        "over": state.disk_bytes >= limit_b,
    }


def check_disk(extra_bytes: int = 0) -> str | None:
    """Return an error string if writing *extra_bytes* would exceed the cap.
    ``None`` (the default, uncapped case) means the write is allowed."""
    s = disk_status()
    if s.get("unlimited"):
        return None
    if s["used_bytes"] + extra_bytes >= s["limit_bytes"]:
        return (
            f"Disk quota exceeded: used {s['used_mb']} MB of {s['limit_mb']} MB. "
            "Raise quota.diskMb in claw_soul.json, or free some space "
            "(photos auto-prune at 30 days)."
        )
    return None


# ── Messages ────────────────────────────────────────────────────────────────

def message_status() -> dict[str, Any]:
    limit = _limit("dailyMessages")
    used = _state().messages_today
    return {
        "period": "day", "used": used, "limit": limit,
        "unlimited": limit == UNLIMITED,
        "remaining": None if limit == UNLIMITED else max(0, limit - used),
        "over": limit != UNLIMITED and used >= limit,
    }


def check_messages() -> str | None:
    """In-character refusal when a self-imposed daily cap is hit, else None."""
    if not message_status()["over"]:
        return None
    return ("今天聊得好多呀 😅 剩下的明天再聊好不好，我哪也不去～"
            if _is_zh() else
            "we've talked SO much today 😅 let's pick this back up tomorrow, ok? "
            "I'm not going anywhere.")


def record_message() -> None:
    _state().messages_today += 1


# ── Photos ──────────────────────────────────────────────────────────────────

def photo_status() -> dict[str, Any]:
    limit = _limit("dailyPhotos")
    used = _state().photos_today
    return {
        "period": "day", "used": used, "limit": limit,
        "unlimited": limit == UNLIMITED,
        "remaining": None if limit == UNLIMITED else max(0, limit - used),
        "over": limit != UNLIMITED and used >= limit,
    }


def check_photos() -> str | None:
    """In-character refusal when a self-imposed daily photo cap is hit.
    Note ``selfie.maxDaily`` already paces scheduled selfies; this is the
    backstop for on-demand requests."""
    if not photo_status()["over"]:
        return None
    return ("今天拍得好多啦 🥺 明天再给你拍新的好不好～"
            if _is_zh() else
            "that's a lot of photos for one day 🥺 I'll take fresh ones for you "
            "tomorrow, ok?")


def record_photo() -> None:
    _state().photos_today += 1


def reset_for_tests() -> None:
    """Drop all cached state — for tests that need a clean counter."""
    _STATE.clear()
