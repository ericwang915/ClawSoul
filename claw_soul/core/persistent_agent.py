"""
PersistentAgent — an Agent subclass that automatically saves its message
history to a SessionStore after every chat() or compact() call.

On construction it restores the previous conversation from the store so that
sessions survive server restarts.

Restoration strategy
--------------------
  messages[0]   — always rebuilt fresh by Agent.__init__ (soul + persona + skills)
  messages[1:]  — restored from the Markdown session store

This means soul/persona/skill changes take effect on the next restart while
the full conversation history (including compaction summaries and skill
injection messages) is preserved.

Timestamps
----------
Each message carries a ``_ts`` field (ISO 8601) that records when it was
created.  This enables time-based truncation in the SessionStore.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from .agent import Agent
from .storage import StorageManager
from .storage_pg import make_storage_manager

if TYPE_CHECKING:
    from .session_store import SessionStore

logger = logging.getLogger(__name__)


class PersistentAgent(Agent):
    """Agent that auto-saves to and restores from a Markdown SessionStore."""

    def __init__(
        self,
        *args,
        store: "SessionStore",
        session_id: str,
        **kwargs,
    ) -> None:
        kwargs.setdefault("session_id", session_id)
        super().__init__(*args, **kwargs)
        self._store = store
        self._session_id = session_id

        # Unified storage (events + FTS5 transcript index, OR Postgres in
        # SaaS worker mode).  The Agent reads back via attribute name
        # ``_session_index`` (legacy) so the recall_conversation tool
        # dispatcher in agent.py keeps working.
        try:
            self._session_index: StorageManager | None = make_storage_manager()
        except Exception as exc:
            logger.warning("[PersistentAgent] StorageManager init failed: %s", exc)
            self._session_index = None

        # ── Soul Mate: ensure milestone first-chat date on init ────────────
        try:
            if hasattr(self, "memory") and hasattr(self.memory, "milestones"):
                self.memory.milestones.ensure_first_chat_date()
        except Exception:
            pass

        self._restore()

    # ── Restore ──────────────────────────────────────────────────────────────

    def _restore(self) -> None:
        """Load saved messages and merge with the freshly built system prompt."""
        saved = self._store.load(self._session_id)
        if not saved:
            return

        initial_system = self.messages[0]   # freshly built system prompt

        # Sanitize restored messages to remove broken tool-call sequences
        # that may have been persisted from a previous crash or error.
        saved = self._sanitize_tool_pairs(saved)

        self.messages = [initial_system] + saved

        # Re-infer which skills were loaded so _use_skill doesn't double-inject
        for msg in saved:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                m = re.search(r"(?:Skill Enabled|SKILL ACTIVATED):\s*(.+)", content)
                if m:
                    self.loaded_skill_names.add(m.group(1).strip().rstrip("]"))

        # Inject a fresh memory snapshot so the LLM sees up-to-date context
        # near the end of the history (not just buried in the system prompt).
        self._inject_memory_refresh()

        logger.info(
            "[PersistentAgent] Restored session '%s': %d messages, %d skills",
            self._session_id, len(saved), len(self.loaded_skill_names),
        )

    def _inject_memory_refresh(self) -> None:
        """Append a fresh memory snapshot as a system message.

        Called after session restore so the LLM sees up-to-date long-term
        memory near the latest conversation context, not just the stale
        snapshot in the original system prompt.
        """
        try:
            boot_mem = self.memory.boot_context(max_chars=2000)
        except Exception:
            return
        if not boot_mem:
            return
        self.messages.append({
            "role": "system",
            "content": (
                "[Memory Refresh — session restored]\n"
                "The following is your latest long-term memory. "
                "Use this context to personalize responses.\n\n"
                f"{boot_mem}"
            ),
        })

    # ── Timestamp injection ──────────────────────────────────────────────────

    @staticmethod
    def _ensure_ts(msg: dict) -> dict:
        """Add a ``_ts`` field to a message if it doesn't have one."""
        if "_ts" not in msg:
            msg["_ts"] = datetime.now().isoformat(timespec="seconds")
        return msg

    # ── Auto-save ────────────────────────────────────────────────────────────

    def _save(self) -> None:
        # Ensure every message has a timestamp before saving
        for msg in self.messages[1:]:
            self._ensure_ts(msg)
        self._store.save(self._session_id, self.messages)

        # Mirror to the FTS5 transcript index for cross-session recall.
        # index_turn() dedupes by content hash so re-saving the whole list
        # each chat() call only inserts the *new* turns.
        #
        # In Pg mode the store (SessionStorePg) already batch-writes every
        # turn into the same `turns` table the index reads — so the mirror
        # would be a redundant per-message N+1 POST.  Skip it there.
        if self._session_index is not None and not getattr(
            self._store, "writes_turn_index", False
        ):
            try:
                self._session_index.index_messages(self._session_id, self.messages[1:])
            except Exception as exc:
                logger.warning("[PersistentAgent] FTS5 index write failed: %s", exc)

    def chat(self, user_input: str) -> str:
        response = super().chat(user_input)
        self._save()
        return response

    def chat_proactive(self, user_input: str) -> str:
        """Generate a proactive message WITHOUT persisting the synthetic
        prompt as a user turn.

        The proactive scheduler feeds the agent an internal instruction
        ("it's morning — say something") as ``user_input``.  With plain
        ``chat()`` that instruction lands in the Postgres ``turns`` table
        as a user message and then shows up in the web Chronicles as if
        the user sent it — which is why web and Telegram looked different.
        Here we drop that synthetic user message after generation and keep
        only the assistant reply (the message actually delivered)."""
        start = len(self.messages)
        response = super().chat(user_input)
        if start < len(self.messages) and self.messages[start].get("role") == "user":
            del self.messages[start]
        self._save()
        return response

    def compact(self, instruction: str | None = None) -> str:
        result = super().compact(instruction)
        self._save()
        return result

    def chat_stream(self, user_input: str | list, on_token=None) -> str:
        response = super().chat_stream(user_input, on_token=on_token)
        self._save()
        return response
