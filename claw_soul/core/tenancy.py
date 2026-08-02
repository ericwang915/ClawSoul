"""
Per-user tenant context for ClawSoul.

A single ClawSoul process can serve many users. Each web request, Telegram
update, or scheduler tick runs inside a *tenant context* that pins one
``user_id``. Storage paths, config lookups, and agent caches all read this
context implicitly.

Usage from a request handler::

    from claw_soul.core import tenancy
    tenancy.set_current_user("alice-uuid")
    # all subsequent config.CLAWSOUL_HOME, memory paths, etc.
    # automatically resolve under /data/users/alice-uuid/

The contextvar is async-safe — each asyncio Task inherits its parent's
binding, and changes inside a Task don't bleed into siblings.

When the contextvar is unset (e.g. during ``claw_soul start`` on a laptop
without auth), ClawSoul falls back to single-tenant mode and writes to the
plain ``~/.claw_soul/`` directory. This keeps the open-source local install
unchanged.
"""

from __future__ import annotations

import contextvars
from pathlib import Path

# None = single-tenant fallback (legacy behavior on a laptop)
_current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "claw_soul_current_user", default=None,
)

# IANA timezone name (e.g. "Asia/Shanghai") for the current request's user.
# Set by the chat handler from the browser-supplied "clientTimezone" field.
# When unset, time formatting falls back to the daemon's system clock.
_current_timezone: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "claw_soul_current_timezone", default=None,
)

# Bot's own location timezone — represents where the AI persona "lives".
# Defaults to Asia/Shanghai because the bundled personas are Chinese-speaking.
# Override per-user via persona's `bot_timezone` config or env BOT_TIMEZONE.
_BOT_TZ_FALLBACK = "Asia/Shanghai"


def set_current_user(user_id: str | None) -> contextvars.Token:
    """Bind a user_id to the current async/thread context. Returns a token
    that the caller can pass to ``reset_current_user`` to revert."""
    return _current_user.set(user_id)


def reset_current_user(token: contextvars.Token) -> None:
    _current_user.reset(token)


def get_current_user() -> str | None:
    return _current_user.get()


def is_multi_tenant() -> bool:
    """True if a user is currently bound (multi-tenant mode)."""
    return _current_user.get() is not None


def resolve_home(base: Path) -> Path:
    """Return the per-user data dir if a user is bound, else *base* (legacy)."""
    user_id = _current_user.get()
    if user_id:
        return base / "users" / user_id
    return base


class user_context:
    """Context manager: bind a user_id for the duration of a ``with`` block.

    Example::

        with user_context("alice-uuid"):
            config.load(force=True)  # loads alice's config
            agent = build_agent()    # writes to alice's namespace
    """

    def __init__(self, user_id: str | None) -> None:
        self._user_id = user_id
        self._token: contextvars.Token | None = None

    def __enter__(self) -> str | None:
        self._token = set_current_user(self._user_id)
        return self._user_id

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            reset_current_user(self._token)
            self._token = None


# ── Per-request timezone ────────────────────────────────────────────────────

def set_current_timezone(tz_name: str | None) -> contextvars.Token:
    """Bind an IANA tz name (e.g. ``"Asia/Shanghai"``) to the current context."""
    return _current_timezone.set(tz_name)


def get_current_timezone() -> str | None:
    return _current_timezone.get()


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
      1. The current tenant's ``persona.timezone`` config (claw_soul.json)
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


# ── APScheduler job wrapping ────────────────────────────────────────────────

def wrap_async_for_user(user_id: str, fn):
    """Return a coroutine function that runs ``fn`` under ``user_context(user_id)``.

    Use this when registering a per-user job with a shared APScheduler instance:
    APScheduler fires the job from an arbitrary task with no tenancy binding,
    so without this wrapper the job's calls to ``config.CLAWSOUL_HOME``,
    ``MemoryManager``, ``SessionStore``, etc. would resolve to the global
    daemon namespace and silently corrupt the wrong user's data.

    ``fn`` must be a coroutine function. *args/**kwargs from APScheduler pass
    through unchanged.
    """
    async def wrapped(*args, **kwargs):
        with user_context(user_id):
            return await fn(*args, **kwargs)
    wrapped.__name__ = f"{getattr(fn, '__name__', 'job')}__user_{user_id[:8]}"
    return wrapped
