"""
Temporal Memory Index — time-line events that enrich recall with time-aware search.

Events are stored as JSONL lines under ``~/.claw_soul/context/memory/timeline.jsonl``.

Each event has:
  - timestamp
  - session_id
  - topic (LLM-extracted label)
  - summary (one-sentence)
  - sentiment (-1 to 1)
  - keywords (list)
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── Pruning thresholds ────────────────────────────────────────────────────────

MAX_MEMORY_EVENTS = 1000   # Events kept in memory (used for fast queries)
MAX_FILE_EVENTS = 20000    # Maximum events kept in the JSONL file
PRUNE_AFTER = 500          # Check pruning every N events added


@dataclass
class TimelineEvent:
    """A single event on the conversation timeline."""

    timestamp: str          # ISO 8601
    session_id: str
    topic: str = "general"
    summary: str = ""
    sentiment: float = 0.0   # -1 to 1
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sentiment"] = round(self.sentiment, 3)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TimelineEvent":
        return cls(
            timestamp=d.get("timestamp", datetime.now().isoformat()),
            session_id=d.get("session_id", ""),
            topic=d.get("topic", "general"),
            summary=d.get("summary", ""),
            sentiment=float(d.get("sentiment", 0.0)),
            keywords=d.get("keywords", []),
        )


class TemporalMemoryIndex:
    """Time-line index of conversation events with text search."""

    def __init__(self, memory_dir: str | None = None) -> None:
        if memory_dir is None:
            from ... import config as _cfg
            memory_dir = os.path.join(str(_cfg.CLAWSOUL_HOME), "context", "memory")
        self._memory_dir = memory_dir
        os.makedirs(self._memory_dir, exist_ok=True)
        self._path = os.path.join(self._memory_dir, "timeline.jsonl")
        self._events: list[TimelineEvent] = []
        self._add_counter = 0  # tracks events since last prune check
        # Load only the most recent events into memory
        self._load_recent(max_events=MAX_MEMORY_EVENTS)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Legacy full load (kept for backward compatibility).

        New code prefers _load_recent() which is O(n) on file size but
        only retains the tail.
        """
        if not os.path.isfile(self._path):
            self._events = []
            return
        events: list[TimelineEvent] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(TimelineEvent.from_dict(json.loads(line)))
                        except (json.JSONDecodeError, ValueError):
                            continue
        except OSError:
            pass
        self._events = events

    def _load_recent(self, max_events: int = MAX_MEMORY_EVENTS) -> None:
        """Load only the most recent *max_events* from file into memory."""
        if not os.path.isfile(self._path):
            self._events = []
            return
        # Read all lines, keep only the last max_events
        lines: list[str] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        lines.append(line)
        except OSError:
            self._events = []
            return
        recent_lines = lines[-max_events:] if len(lines) > max_events else lines
        self._events = []
        for line in recent_lines:
            try:
                self._events.append(TimelineEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue

    def _atomic_append(self, event: TimelineEvent) -> None:
        """Atomically append one event."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Failed to write timeline event: %s", exc)

    def _prune_file(self, max_events: int = MAX_FILE_EVENTS) -> None:
        """Trim the JSONL file to at most *max_events* lines (keeps the newest)."""
        if not os.path.isfile(self._path):
            return
        lines: list[str] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return
        if len(lines) <= max_events:
            return
        # Atomic rewrite with only the last max_events lines
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines[-max_events:])
            os.replace(tmp_path, self._path)
            logger.info("[TemporalIndex] Pruned to %d events.", max_events)
            # Reload memory to stay in sync
            self._load_recent(max_events=MAX_MEMORY_EVENTS)
        except OSError as exc:
            logger.warning("Failed to prune timeline file: %s", exc)
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    # ── File-level query helpers ──────────────────────────────────────────────

    def _search_file(self, query: str, time_range: tuple[str, str] | None = None) -> list[TimelineEvent]:
        """Search timeline events directly from file (no memory dependency).

        Used for correctness when memory only holds a subset.  Falls back
        to the in-memory search if the file covers all events within the
        query range (which it usually will after pruning).
        """
        if not os.path.isfile(self._path):
            return []

        q_lower = query.lower()
        tokens = set(re.findall(r"\w+", q_lower))
        results: list[TimelineEvent] = []

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = TimelineEvent.from_dict(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
                    # Time filter
                    if time_range:
                        start, end = time_range
                        if ev.timestamp < start or ev.timestamp > end:
                            continue
                    # Keyword match
                    text = f"{ev.topic} {ev.summary} {' '.join(ev.keywords)}".lower()
                    if any(t in text for t in tokens):
                        results.append(ev)
        except OSError:
            pass

        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results

    def _timeline_from_file(self, start: str, end: str) -> list[TimelineEvent]:
        """Read timeline events within a time range directly from file."""
        if not os.path.isfile(self._path):
            return []
        results: list[TimelineEvent] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = TimelineEvent.from_dict(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if start <= ev.timestamp <= end:
                        results.append(ev)
        except OSError:
            pass
        return results

    # ── Public API ────────────────────────────────────────────────────────────

    def add_event(self, event: TimelineEvent) -> None:
        """Add a new timeline event and persist it."""
        self._events.append(event)
        self._atomic_append(event)
        self._add_counter += 1

        # Limit memory size
        if len(self._events) > MAX_MEMORY_EVENTS:
            self._events = self._events[-MAX_MEMORY_EVENTS:]

        # Periodic file pruning
        if self._add_counter >= PRUNE_AFTER:
            self._prune_file()
            self._add_counter = 0

    def search(self, query: str, time_range: tuple[str, str] | None = None) -> list[TimelineEvent]:
        """Search timeline events by keyword/summary/topic, optionally within a time range.

        *time_range* is a tuple of (start_iso, end_iso), e.g. ("2026-01-01", "2026-02-01").
        Uses file-level search for correctness (memory only holds recent subset).
        """
        return self._search_file(query, time_range)

    def get_timeline(self, start: str, end: str) -> list[TimelineEvent]:
        """Return all events within a time range (ISO 8601 strings)."""
        return self._timeline_from_file(start, end)

    def get_topics(self) -> list[str]:
        """Return all unique topics, sorted by frequency (most common first).

        Uses in-memory events (recent subset). For full coverage, this is
        limited to the last MAX_MEMORY_EVENTS entries, which after pruning
        covers the last MAX_FILE_EVENTS entries.
        """
        counts: dict[str, int] = defaultdict(int)
        for ev in self._events:
            counts[ev.topic] += 1
        return sorted(counts.keys(), key=lambda t: -counts[t])

    def get_sessions(self) -> list[str]:
        """Return all unique session IDs from recent events in memory."""
        return list({ev.session_id for ev in self._events})

    def get_summary(self, days: int = 7) -> str:
        """Return a human-readable summary of recent events."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        # For recent queries, memory events are sufficient (they cover the tail)
        recent = [e for e in self._events if e.timestamp >= cutoff]
        if not recent:
            return "No recent timeline events."

        topics = defaultdict(list)
        for ev in recent:
            topics[ev.topic].append(ev)

        lines = [f"Recent conversations ({days} days):"]
        for topic, evts in sorted(topics.items(), key=lambda x: -len(x[1]))[:5]:
            n = len(evts)
            lines.append(f"  - {topic}: {n} event(s)")
            # Show latest summary
            latest = max(evts, key=lambda e: e.timestamp)
            if latest.summary:
                lines.append(f"    \u21b3 {latest.summary}")

        return "\n".join(lines)
