"""
MemoryManager — long-term key-value memory with hybrid RAG recall and Soul Mate features.

Storage
-------
Memories are stored as Markdown files:
  - MEMORY.md        — curated long-term memory (latest value per key)
  - YYYY-MM-DD.md    — daily append-only log

Soul Mate extensions
--------------------
  - EmotionalGraph      — time-series emotion memory (affect/emotional_graph.jsonl)
  - TemporalMemoryIndex — timeline event index (memory/timeline.jsonl)
  - MilestoneManager    — relationship milestones (relationship/milestones.json)
  - RelationshipStore   — relationship dimensions (affect/relationship.json)

When writing, both MEMORY.md and today's daily log are updated.
When reading, MEMORY.md is the source of truth (holds latest per key).
Conflict resolution: if the same key is written multiple times, the most
recent write wins (MEMORY.md is always overwritten with the latest value).

Per-group isolation
-------------------
When ``global_memory_dir`` is set, recall merges results from BOTH the
local (group-specific) memory AND the global (shared) memory.  Writes
always go to the local memory only.  This lets each Telegram/Discord/
WhatsApp group maintain private memories while still having access to
shared knowledge.

Recall
------
When a specific query is given, the manager converts every memory entry into a
short "chunk"  ("{key}: {value}")  and runs hybrid sparse + dense retrieval to
return the most relevant ones.  When the query is empty or "*", ALL memories
are returned (full-dump mode, used by compaction).
"""

from __future__ import annotations

import logging

from ..retrieval.retriever import HybridRetriever
from .emotional_graph import EmotionalGraph, RelationshipStore
from .milestones import MilestoneManager
from .storage import MemoryStorage
from .temporal_index import TemporalMemoryIndex

logger = logging.getLogger(__name__)

_DUMP_TRIGGERS = {"", "*", "all", "everything"}


class MemoryManager:
    """
    Manages long-term memories stored as Markdown files.

    Parameters
    ----------
    memory_dir        : path to the local memory directory.
    global_memory_dir : optional path to a shared/global memory directory.
                        When set, recall() merges results from both local and
                        global stores.  Writes always go to local only.
    use_dense         : include embedding retrieval for recall (False by default
                        — BM25 alone is fast and sufficient for small corpora).
    """

    def __init__(
        self,
        memory_dir: str | None = None,
        global_memory_dir: str | None = None,
        use_dense: bool = False,
    ) -> None:
        import os as _os

        if memory_dir is None:
            from ... import config as _cfg
            memory_dir = _os.path.join(str(_cfg.CLAWSOUL_HOME), "context", "memory")

        self.storage = MemoryStorage(memory_dir)
        self._global_storage: MemoryStorage | None = None
        if global_memory_dir and _os.path.isdir(global_memory_dir):
            self._global_storage = MemoryStorage(global_memory_dir)
        self._use_dense = use_dense

        # ── Soul Mate extensions ──────────────────────────────────────────
        from ... import config as _cfg
        _home = str(_cfg.CLAWSOUL_HOME)
        _affect_dir = _os.path.join(_home, "context", "affect")
        _memory_dir = memory_dir or _os.path.join(_home, "context", "memory")
        _rel_dir = _os.path.join(_home, "context", "relationship")

        self.emotional_graph = EmotionalGraph(_affect_dir)
        self.relationship = RelationshipStore(_affect_dir)
        self.timeline = TemporalMemoryIndex(_memory_dir)
        self.milestones = MilestoneManager(_rel_dir)

    # ── Merged memories (local + global) ─────────────────────────────────────

    def _merged_memories(self) -> dict[str, str]:
        """Return local memories overlaid on global memories."""
        merged: dict[str, str] = {}
        if self._global_storage is not None:
            for k, v in self._global_storage.list_all().items():
                merged[f"[global] {k}"] = v
        merged.update(self.storage.list_all())
        return merged

    # ── Core operations ──────────────────────────────────────────────────────

    def remember(self, content: str, key: str | None = None) -> str:
        """Store *content* under *key* in local (group) memory."""
        if not key:
            raise ValueError("Key is required for memory storage.")
        self.storage.set(key, content)
        return f"Memory stored: [{key}] = {content}"

    def recall(self, query: str, top_k: int = 10) -> str:
        """
        Retrieve memories relevant to *query*.

        Searches both local and global memories when global_memory_dir is set.

        - If query is empty / "*" / "all" → returns ALL memories (full dump).
        - Otherwise → runs hybrid BM25 (+ optional dense) retrieval and
          returns the top *top_k* most relevant entries.
        """
        all_memories = self._merged_memories()
        if not all_memories:
            return "No memories found."

        if query.strip().lower() in _DUMP_TRIGGERS:
            lines = [f"- {k}: {v}" for k, v in all_memories.items()]
            return "\n".join(lines)

        corpus = [
            {"source": k, "content": f"{k}: {v}"}
            for k, v in all_memories.items()
        ]

        retriever = HybridRetriever(
            provider=None,
            use_sparse=True,
            use_dense=self._use_dense,
            use_reranker=False,
        )
        retriever.fit(corpus)
        hits = retriever.retrieve(query, top_k=top_k)

        if not hits:
            logger.debug("[MemoryManager] No RAG hits for '%s', returning all.", query)
            lines = [f"- {k}: {v}" for k, v in all_memories.items()]
            return "(No close match found; showing all memories)\n" + "\n".join(lines)

        lines = [f"- {h['source']}: {h['content'].split(': ', 1)[-1]}" for h in hits]
        return "\n".join(lines)

    def forget(self, key: str) -> str:
        """Remove a memory entry by key from local memory."""
        if self.storage.get(key) is not None:
            self.storage.delete(key)
            return f"Forgot: {key}"
        return f"Nothing found for: {key}"

    def memory_get(self, path: str) -> str:
        """Read a specific file under the memory directory."""
        return self.storage.read_memory_file(path)

    def list_files(self) -> list[str]:
        """List all .md files in the memory directory."""
        return self.storage.list_memory_files()

    # ── Boot context (auto-injected at session start) ────────────────────────

    def boot_context(self, max_chars: int = 3000) -> str:
        """Build a concise memory snapshot to inject at session start.

        Includes:
        1. Curated long-term memory (MEMORY.md) — user profile entries first,
           then other entries, truncated to fit within the budget.
        2. Recent daily logs (today + yesterday) — trimmed to fit remaining budget.
        3. (Soul Mate) Emotional context summary, relationship state, milestone.
        """
        parts: list[str] = []

        index_content = self.storage.read_index()
        if index_content:
            parts.append("### INDEX (System Info)\n" + index_content)

        all_mem = self._merged_memories()
        used = sum(len(p) for p in parts)
        mem_budget = int((max_chars - used) * 0.6)  # slightly less to make room for affect context

        if all_mem:
            profile_keys = {"bot_name", "user_name", "user_profile",
                           "assistant_personality", "assistant_focus_area",
                           "assistant_tone", "assistant_domain",
                           "onboarding_completed"}
            profile = {k: v for k, v in all_mem.items() if k in profile_keys}
            other = {k: v for k, v in all_mem.items() if k not in profile_keys}

            lines: list[str] = []
            for k, v in profile.items():
                lines.append(f"- **{k}**: {v}")
            total_len = sum(len(ln) for ln in lines)

            for k, v in other.items():
                line = f"- **{k}**: {v}"
                if total_len + len(line) > mem_budget:
                    remaining = len(other) - (len(lines) - len(profile))
                    if remaining > 0:
                        lines.append(f"- …({remaining} more entries — use `recall()` to search)")
                    break
                lines.append(line)
                total_len += len(line)

            parts.append("### Long-Term Memory\n" + "\n".join(lines))

        # ── Soul Mate: affect context ──────────────────────────────────────
        try:
            affect_lines: list[str] = []
            eg_summary = self.emotional_graph.get_summary(max_events=3)
            if eg_summary and "No recent" not in eg_summary:
                affect_lines.append(eg_summary)

            rel_summary = self.relationship.get_summary()
            if rel_summary:
                affect_lines.append(f"**关系状态**: {rel_summary}")

            days = self.milestones.get_relationship_age_str()
            affect_lines.append(f"**认识时间**: {days}")

            # Check special day
            is_special, label = self.milestones.is_special_day()
            if is_special:
                affect_lines.append(f"✨ **{label}** ✨")

            if affect_lines:
                parts.append("### 情感上下文\n" + "\n".join(affect_lines))
        except Exception:
            logger.debug("Soul Mate affect context injection failed (non-fatal)", exc_info=True)

        daily_budget = max(500, max_chars - sum(len(p) for p in parts))
        daily = self.storage.read_recent_daily_logs(days=2)
        if daily:
            if len(daily) > daily_budget:
                daily = daily[:daily_budget] + "\n\n…(truncated)"
            parts.append("### Recent Activity (Daily Logs)\n" + daily)

        return "\n\n".join(parts) if parts else ""

    # ── INDEX.md — curated system/config info ───────────────────────────────

    def read_index(self) -> str:
        """Read the INDEX.md curated system info file."""
        return self.storage.read_index()

    def write_index(self, content: str) -> str:
        """Write the INDEX.md curated system info file."""
        return self.storage.write_index(content)

    # ── Helpers used by compaction ───────────────────────────────────────────

    def list_all(self) -> dict:
        """Return the raw {key: value} dict (local only, used by compaction)."""
        return self.storage.list_all()
