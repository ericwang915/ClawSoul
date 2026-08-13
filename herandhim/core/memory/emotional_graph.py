"""
Emotional Graph — sentiment analysis, emotion timeline, and relationship state.

Components
----------
- SentimentAnalyzer   — LLM-side-effect sentiment/topic extraction
- EmotionalGraph      — time-series emotion memory (JSONL-backed)
- RelationshipStore   — relationship dimensions (temperature, trust, intimacy, understanding)

All data is persisted under ``~/.herandhim/context/affect/``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_AFFECT_DIR = "affect"

# EmotionalGraph pruning thresholds
MAX_DAYS_KEEP = 90           # Events older than this are pruned
MAX_EVENTS_KEEP = 10000      # Maximum events kept in file
PRUNE_CHECK_INTERVAL = 1000  # Check pruning every N writes

_SENTIMENT_SYSTEM_PROMPT = """\
You are an emotion-analysis sidecar. Given the user message and assistant response,
extract structured emotional metadata in JSON only. No explanation.

Return:
{
  "sentiment": "positive" | "negative" | "neutral",
  "intensity": 0.0-1.0,
  "topic": "short topic label",
  "summary": "one-sentence event summary",
  "follow_up": true | false
}

Rules:
- sentiment: the user's emotional tone
- intensity: how strong the emotion is (0=flat, 1=extreme)
- topic: 1-3 word topic (e.g. "work stress", "hobby sharing", "greeting")
- summary: max 15 words describing the key event or feeling
- follow_up: true if the conversation ended with an unresolved thread or question
"""


# ── SentimentAnalyzer ─────────────────────────────────────────────────────────

class SentimentAnalyzer:
    """Extract emotional metadata from conversation using LLM side-effect."""

    SYSTEM_PROMPT = _SENTIMENT_SYSTEM_PROMPT

    @staticmethod
    def parse(raw: str) -> dict[str, Any]:
        """Parse JSON from LLM output, with fallback to neutral defaults."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            # Find the first { and last }
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Failed to parse sentiment JSON: %s", raw[:200])
            return {
                "sentiment": "neutral",
                "intensity": 0.0,
                "topic": "general",
                "summary": "",
                "follow_up": False,
            }
        # Normalise + validate
        sentiment = data.get("sentiment", "neutral")
        if sentiment not in ("positive", "negative", "neutral"):
            sentiment = "neutral"
        intensity = float(data.get("intensity", 0.0))
        intensity = max(0.0, min(1.0, intensity))
        return {
            "sentiment": sentiment,
            "intensity": intensity,
            "topic": str(data.get("topic", "general"))[:50],
            "summary": str(data.get("summary", ""))[:120],
            "follow_up": bool(data.get("follow_up", False)),
        }


# ── EmotionalGraph ────────────────────────────────────────────────────────────

class EmotionalGraph:
    """Time-series emotional memory backed by a JSONL file.

    Each event is stored as one JSON line:
      {ts, topic, sentiment, intensity, context_summary}
    """

    def __init__(self, affect_dir: str | None = None) -> None:
        if affect_dir is None:
            from ... import config as _cfg
            affect_dir = os.path.join(
                str(_cfg.HERANDHIM_HOME), "context", _DEFAULT_AFFECT_DIR,
            )
        self._affect_dir = affect_dir
        os.makedirs(self._affect_dir, exist_ok=True)
        self._path = os.path.join(self._affect_dir, "emotional_graph.jsonl")
        self._write_counter = 0  # tracks writes since last prune check
        self._event_count = self._count_lines()
        self._lock = threading.Lock()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _read_all(self) -> list[dict]:
        """Read all events from the JSONL file (oldest first).

        Note: With pruning enabled, the file is kept at a manageable size
        (at most MAX_EVENTS_KEEP entries covering up to MAX_DAYS_KEEP days).
        """
        if not os.path.isfile(self._path):
            return []
        events: list[dict] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass
        return events

    def _atomic_append(self, event: dict) -> None:
        """Atomically append one event to the JSONL file."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Failed to write emotional_graph event: %s", exc)

    def _count_lines(self) -> int:
        """Fast count of lines in the JSONL file (does not parse JSON)."""
        if not os.path.isfile(self._path):
            return 0
        try:
            with open(self._path, "rb") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def _prune_old_events(self, before_days: int = MAX_DAYS_KEEP) -> None:
        """Delete events older than *before_days* days and trim to MAX_EVENTS_KEEP."""
        if not os.path.isfile(self._path):
            return
        cutoff = (datetime.now() - timedelta(days=before_days)).isoformat(timespec="seconds")
        lines_to_keep: list[str] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        if ev.get("ts", "") >= cutoff:
                            lines_to_keep.append(json.dumps(ev, ensure_ascii=False) + "\n")
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return

        # If still over the max, keep only the most recent MAX_EVENTS_KEEP
        if len(lines_to_keep) > MAX_EVENTS_KEEP:
            lines_to_keep = lines_to_keep[-MAX_EVENTS_KEEP:]

        # If nothing changed, reset counter and return early
        if len(lines_to_keep) == self._event_count:
            self._write_counter = 0
            return

        # Atomic rewrite via temp file
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines_to_keep)
            os.replace(tmp_path, self._path)
            self._event_count = len(lines_to_keep)
            self._write_counter = 0
            logger.info(
                "[EmotionalGraph] Pruned to %d events (keeping last %d days).",
                self._event_count, before_days,
            )
        except OSError as exc:
            logger.warning("Failed to prune emotional_graph: %s", exc)
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    # ── Public API ────────────────────────────────────────────────────────────

    def add_event(
        self,
        topic: str,
        sentiment: str,
        intensity: float,
        context_summary: str = "",
    ) -> None:
        """Record one emotional event with the current timestamp."""
        with self._lock:
            event = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "topic": topic,
                "sentiment": sentiment,
                "intensity": round(intensity, 3),
                "context_summary": context_summary,
            }
            self._atomic_append(event)
            self._event_count += 1
            self._write_counter += 1
            # Periodic pruning check
            if self._write_counter >= PRUNE_CHECK_INTERVAL:
                self._prune_old_events()

    def get_recent(self, days: int = 7) -> list[dict]:
        """Return events from the last *days* days."""
        cutoff = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff.isoformat()
        return [
            e for e in self._read_all()
            if e.get("ts", "") >= cutoff_str
        ]

    def get_topic_sentiment(self, topic: str) -> dict[str, Any]:
        """Return aggregate sentiment stats for a topic.

        Scans at most MAX_EVENTS_KEEP events (which covers up to
        MAX_DAYS_KEEP days of history after pruning).
        """
        all_events = self._read_all()
        matching = [e for e in all_events if e.get("topic", "").lower() == topic.lower()]
        if not matching:
            return {"topic": topic, "count": 0, "avg_sentiment": "neutral", "avg_intensity": 0.0}

        sentiment_scores = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
        scores = [sentiment_scores.get(e.get("sentiment", "neutral"), 0.0) * e.get("intensity", 0.5) for e in matching]
        avg_score = sum(scores) / len(scores)
        avg_intensity = sum(e.get("intensity", 0.5) for e in matching) / len(matching)

        # Map avg_score back to a label
        if avg_score > 0.2:
            avg_sent = "positive"
        elif avg_score < -0.2:
            avg_sent = "negative"
        else:
            avg_sent = "neutral"

        return {
            "topic": topic,
            "count": len(matching),
            "avg_sentiment": avg_sent,
            "avg_intensity": round(avg_intensity, 3),
        }

    def get_trend(self) -> list[dict]:
        """Return daily aggregate sentiment for the last 14 days.

        Each entry: {date, avg_sentiment, avg_intensity, event_count}
        """
        all_events = self._read_all()
        sentiment_scores = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}

        daily: dict[str, list[float]] = defaultdict(list)
        daily_intensity: dict[str, list[float]] = defaultdict(list)

        for e in all_events:
            ts = e.get("ts", "")
            day = ts[:10] if len(ts) >= 10 else "unknown"
            score = sentiment_scores.get(e.get("sentiment", "neutral"), 0.0) * e.get("intensity", 0.5)
            daily[day].append(score)
            daily_intensity[day].append(e.get("intensity", 0.5))

        # Build trend for last 14 days
        trend: list[dict] = []
        for i in range(13, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            scores = daily.get(day, [])
            if scores:
                avg = sum(scores) / len(scores)
                avg_int = sum(daily_intensity.get(day, [0.5])) / len(daily_intensity.get(day, [0.5]))
                if avg > 0.2:
                    label = "positive"
                elif avg < -0.2:
                    label = "negative"
                else:
                    label = "neutral"
                trend.append({
                    "date": day,
                    "avg_sentiment": label,
                    "avg_intensity": round(avg_int, 3),
                    "event_count": len(scores),
                })
            else:
                trend.append({
                    "date": day,
                    "avg_sentiment": "neutral",
                    "avg_intensity": 0.0,
                    "event_count": 0,
                })
        return trend

    def get_summary(self, max_events: int = 5) -> str:
        """Return a human-readable summary of recent emotional state."""
        recent = self.get_recent(days=7)
        if not recent:
            return "No recent emotional data."

        # Calculate overall sentiment
        sentiment_scores = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
        scores = [sentiment_scores.get(e.get("sentiment", "neutral"), 0.0) * e.get("intensity", 0.5) for e in recent]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        if avg_score > 0.2:
            overall = "positive 😊"
        elif avg_score < -0.2:
            overall = "negative 😔"
        else:
            overall = "neutral 😐"

        # Top topics
        topics = defaultdict(list)
        for e in recent:
            topics[e.get("topic", "general")].append(e)
        top_topics = sorted(topics.items(), key=lambda x: len(x[1]), reverse=True)[:3]

        lines = [
            f"Recent mood (7 days): {overall}",
            f"Events recorded: {len(recent)}",
        ]
        if top_topics:
            lines.append("Topics:")
            for topic, evts in top_topics:
                sents = [e.get("sentiment", "neutral") for e in evts]
                sent_summary = f"{sents.count('positive')}👍 {sents.count('negative')}👎 {sents.count('neutral')}➖"
                lines.append(f"  - {topic}: {sent_summary}")

        return "\n".join(lines)


# ── RelationshipStore ─────────────────────────────────────────────────────────

class RelationshipStore:
    """Maintains relationship state across multiple dimensions.

    Dimensions
    ----------
    - relationship_temperature : 0-100 (warmth / affection)
    - trust                    : 0-100
    - intimacy                 : 0-100
    - understanding            : 0-100

    Each dimension starts at 50.  Sentiment events nudge the values.
    """

    DIMENSIONS = ("relationship_temperature", "trust", "intimacy", "understanding")

    def __init__(self, affect_dir: str | None = None) -> None:
        if affect_dir is None:
            from ... import config as _cfg
            affect_dir = os.path.join(
                str(_cfg.HERANDHIM_HOME), "context", _DEFAULT_AFFECT_DIR,
            )
        self._affect_dir = affect_dir
        os.makedirs(self._affect_dir, exist_ok=True)
        self._path = os.path.join(self._affect_dir, "relationship.json")
        self._data: dict[str, float] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.isfile(self._path):
            self._reset()
            self._save()
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._data = {k: float(v) for k, v in data.items() if k in self.DIMENSIONS}
        except (OSError, json.JSONDecodeError, ValueError):
            self._reset()

    def _save(self) -> None:
        """Atomic write via temporary file."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(suffix=".json", dir=os.path.dirname(self._path))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning("Failed to save relationship data: %s", exc)

    def _reset(self) -> None:
        self._data = {dim: 50.0 for dim in self.DIMENSIONS}

    # ── Public API ────────────────────────────────────────────────────────────

    def update_from_sentiment(self, sentiment: str, intensity: float) -> None:
        """Nudge relationship dimensions based on a sentiment event.

        Positive → increase temperature, trust, intimacy, understanding
        Negative → decrease temperature, trust; slightly affect others
        Neutral  → small positive drift toward 50
        """
        delta = 0.0
        if sentiment == "positive":
            delta = intensity * 3.0  # +0 to +3 per event
        elif sentiment == "negative":
            delta = -intensity * 2.0  # -0 to -2 per event
        else:
            # Neutral: gentle drift toward 50
            for dim in self.DIMENSIONS:
                if self._data.get(dim, 50) < 50:
                    delta = intensity * 0.5
                elif self._data.get(dim, 50) > 50:
                    delta = -intensity * 0.5
                else:
                    delta = 0.0
                self._data[dim] = max(0.0, min(100.0, self._data.get(dim, 50) + delta))
            self._save()
            return

        # Apply delta to all dimensions (with different weights)
        weights = {
            "relationship_temperature": 1.0,
            "trust": 0.7,
            "intimacy": 0.8,
            "understanding": 0.6,
        }
        for dim in self.DIMENSIONS:
            w = weights.get(dim, 1.0)
            self._data[dim] = max(0.0, min(100.0, self._data.get(dim, 50) + delta * w))

        self._save()

    def get_summary(self) -> str:
        """Return human-readable relationship summary."""
        from .. import lang as _lang
        parts = []
        labels = (
            {
                "relationship_temperature": "温度",
                "trust": "信任",
                "intimacy": "亲密",
                "understanding": "理解",
            }
            if _lang.is_chinese()
            else {
                "relationship_temperature": "Warmth",
                "trust": "Trust",
                "intimacy": "Intimacy",
                "understanding": "Understanding",
            }
        )
        for dim in self.DIMENSIONS:
            val = int(self._data.get(dim, 50))
            emoji = "🟢" if val >= 70 else ("🟡" if val >= 40 else "🔴")
            parts.append(f"{emoji} {labels.get(dim, dim)}: {val}/100")
        return " | ".join(parts)

    def get_all(self) -> dict[str, float]:
        """Return raw dimension dict."""
        return dict(self._data)

    def update(self, data: dict[str, float]) -> None:
        """Bulk-update relationship dimensions."""
        for k, v in data.items():
            if k in self.DIMENSIONS:
                self._data[k] = max(0.0, min(100.0, float(v)))
        self._save()
