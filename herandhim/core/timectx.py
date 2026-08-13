"""
Two clocks, and which one is talking.

The companion lives somewhere. You live somewhere else. Almost every
human-sounding detail depends on keeping those apart: she should say "it's
nearly midnight here" using *her* timezone, while quiet hours and "you've
been quiet all day" are measured against *yours*.

Both are contextvars because a single process serves the web chat, the
Telegram bot, and the scheduler concurrently, and each turn may carry a
different browser-supplied timezone. The vars are async-safe — a Task
inherits its parent's binding and changes don't bleed into siblings.

Unset falls back to the daemon's system clock.
"""

from __future__ import annotations

import contextvars

# IANA timezone name (e.g. "Asia/Shanghai") for the current request's user.
# Set by the chat handler from the browser-supplied "clientTimezone" field.
# When unset, time formatting falls back to the daemon's system clock.
_current_timezone: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "herandhim_current_timezone", default=None,
)

# Bot's own location timezone — represents where the AI persona "lives".
# Defaults to Asia/Shanghai because the bundled personas are Chinese-speaking.
# Override per-user via persona's `bot_timezone` config or env BOT_TIMEZONE.
_BOT_TZ_FALLBACK = "Asia/Shanghai"



# ── Per-request timezone ────────────────────────────────────────────────────

def set_current_timezone(tz_name: str | None) -> contextvars.Token:
    """Bind an IANA tz name (e.g. ``"Asia/Shanghai"``) to the current context."""
    return _current_timezone.set(tz_name)



def user_timezone() -> str | None:
    """Resolved IANA timezone for the HUMAN user.

    Order: the per-turn contextvar (set by the web chat from the browser),
    then the saved ``user.timezone`` config (persisted so Telegram + proactive
    work too).  ``None`` if neither is known — callers then fall back to the
    bot's clock.
    """
    tz = _current_timezone.get()
    if tz:
        return tz
    try:
        from .. import config as _cfg
        tz = _cfg.get_str("user", "timezone", default="") or ""
    except Exception:
        tz = ""
    return tz or None


def now_in_user_tz():
    """Return ``datetime.now()`` localized to the user's timezone.

    Falls back to the bot's own timezone if the user's isn't known (so the
    server's UTC clock never leaks into the agent's "now").
    """
    from datetime import datetime
    tz_name = user_timezone()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return now_in_bot_tz()


def bot_timezone() -> str:
    """The AI persona's home timezone (e.g. where Eva 'lives').

    Resolves in order:
      1. The current tenant's ``persona.timezone`` config (herandhim.json)
      2. The ``BOT_TIMEZONE`` env var
      3. ``Asia/Shanghai`` (default — matches bundled Chinese personas)
    """
    import os

    from .. import config as _cfg
    try:
        tz = _cfg.get_str("persona", "timezone", default="") or ""
    except Exception:
        tz = ""
    tz = tz or os.environ.get("BOT_TIMEZONE") or _BOT_TZ_FALLBACK
    return tz


def now_in_bot_tz():
    """Return ``datetime.now()`` localized to the AI persona's home timezone.

    Always returns a tz-aware datetime — never the container's UTC clock.
    """
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(bot_timezone()))
    except Exception:
        return datetime.now()


