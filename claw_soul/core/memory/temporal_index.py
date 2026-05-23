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
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
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

    def _atomic_append(self, event: TimelineEvent) -> None:
        """Atomically append one event."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Failed to write timeline event: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def add_event(self, event: TimelineEvent) -> None:
        """Add a new timeline event and persist it."""
        self._events.append(event)
        self._atomic_append(event)

    def search(self, query: str, time_range: tuple[str, str] | None = None) -> list[TimelineEvent]:
        """Search timeline events by keyword/summary/topic, optionally within a time range.

        *time_range* is a tuple of (start_iso, end_iso), e.g. ("2026-01-01", "2026-02-01").
        """
        # Basic keyword matching (case-insensitive)
        q_lower = query.lower()
        tokens = set(re.findall(r"\w+", q_lower))

        results: list[TimelineEvent] = []
        for ev in self._events:
            # Time filter
            if time_range:
                start, end = time_range
                if ev.timestamp < start or ev.timestamp > end:
                    continue

            # Keyword match
            text = f"{ev.topic} {ev.summary} {' '.join(ev.keywords)}".lower()
            if any(t in text for t in tokens):
                results.append(ev)
                continue

        # Sort newest first
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results

    def get_timeline(self, start: str, end: str) -> list[TimelineEvent]:
        """Return all events within a time range (ISO 8601 strings)."""
        return [
            e for e in self._events
            if start <= e.timestamp <= end
        ]

    def get_topics(self) -> list[str]:
        """Return all unique topics, sorted by frequency (most common first)."""
        counts: dict[str, int] = defaultdict(int)
        for ev in self._events:
            counts[ev.topic] += 1
        return sorted(counts.keys(), key=lambda t: -counts[t])

    def get_sessions(self) -> list[str]:
        """Return all unique session IDs."""
        return list({ev.session_id for ev in self._events})

    def get_summary(self, days: int = 7) -> str:
        """Return a human-readable summary of recent events."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
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
                lines.append(f"    ↳ {latest.summary}")

        return "\n".join(lines)
