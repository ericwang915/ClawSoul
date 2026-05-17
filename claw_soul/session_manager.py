"""
SessionManager — central registry of session_id -> Agent instances.

All channels (Telegram, CLI, Web, etc.) and the cron scheduler go through a
single SessionManager so that session lifecycle is managed in one place.

Session ID conventions
----------------------
  telegram:{chat_id}   — one per Telegram chat
  discord:{channel_id} — one per Discord channel / DM
  whatsapp:{phone}     — one per WhatsApp number
  cron:{job_id}        — one per scheduled job (persistent across runs)
  cli                  — the interactive REPL session
  web:dashboard        — the web dashboard session

Concurrency
-----------
Each session has its own ``asyncio.Lock`` so that two messages for the same
session are processed sequentially (preventing history interleaving).  A
global ``asyncio.Semaphore`` caps the total number of concurrent agent
executions across all sessions.

Factory signature
-----------------
The factory callable must accept the session_id as its first positional arg:

    def factory(session_id: str) -> Agent: ...

Usage
-----
    sm = SessionManager(agent_factory, store=session_store)
    agent = sm.get_or_create("telegram:123456")

    async with sm.acquire("telegram:123456"):
        response = agent.chat("hello")
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Callable

from . import config

if TYPE_CHECKING:
    from .core.agent import Agent
    from .core.session_store import SessionStore

logger = logging.getLogger(__name__)

AgentFactory = Callable[[str], "Agent"]

_DEFAULT_MAX_CONCURRENT = 4


class SessionManager:
    """
    Central registry that maps session_id strings to Agent instances.

    Provides per-session locking and a global concurrency cap so that
    channels can safely call ``agent.chat()`` without interleaving.
    """

    def __init__(
        self,
        agent_factory: AgentFactory,
        store: "SessionStore | None" = None,
        max_concurrent: int | None = None,
    ) -> None:
        self._factory = agent_factory
        self._store = store
        self._sessions: dict[str, "Agent"] = {}

        # Per-session locks prevent interleaving within the same session
        self._locks: dict[str, asyncio.Lock] = {}

        # Global semaphore caps total concurrent agent executions
        cap = max_concurrent or config.get_int(
            "concurrency", "maxAgents", default=_DEFAULT_MAX_CONCURRENT,
        )
        self._semaphore = asyncio.Semaphore(cap)

    # ── Factory ──────────────────────────────────────────────────────────────

    def set_factory(self, factory: AgentFactory) -> None:
        """Late-bind the factory (used to resolve circular dependencies in server.py)."""
        self._factory = factory

    # ── Core API ─────────────────────────────────────────────────────────────

    def get_or_create(self, session_id: str) -> "Agent":
        """Return the existing Agent for session_id, creating one if needed."""
        if session_id not in self._sessions:
            logger.info("[SessionManager] Creating session '%s'", session_id)
            self._sessions[session_id] = self._factory(session_id)
        return self._sessions[session_id]

    def reset(self, session_id: str) -> "Agent":
        """Discard the current Agent, erase persisted history, and start fresh."""
        logger.info("[SessionManager] Resetting session '%s'", session_id)
        if self._store is not None:
            self._store.delete(session_id)
        self._sessions[session_id] = self._factory(session_id)
        return self._sessions[session_id]

    def remove(self, session_id: str) -> None:
        """Remove a session from memory (session file is kept unless store.delete is called)."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info("[SessionManager] Removed session '%s'", session_id)

    def list_sessions(self) -> list[str]:
        """Return all active (in-memory) session IDs."""
        return list(self._sessions.keys())

    def get(self, session_id: str) -> "Agent | None":
        """Return the Agent for session_id, or None if it doesn't exist."""
        return self._sessions.get(session_id)

    # ── Concurrency control ──────────────────────────────────────────────────

    def get_lock(self, session_id: str) -> asyncio.Lock:
        """Return (creating if necessary) the per-session lock."""
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    @asynccontextmanager
    async def acquire(self, session_id: str) -> AsyncIterator[None]:
        """Acquire the per-session lock AND the global semaphore.

        Use as::

            async with sm.acquire(sid):
                response = agent.chat(msg)
        """
        lock = self.get_lock(session_id)
        async with lock:
            async with self._semaphore:
                yield

    def is_locked(self, session_id: str) -> bool:
        """Return True if the session lock is currently held."""
        lock = self._locks.get(session_id)
        return lock.locked() if lock else False

    # ── Dunder ───────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._sessions
