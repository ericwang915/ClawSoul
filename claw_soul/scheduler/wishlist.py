"""
Wishlist — long-running background "things the user said they wanted".

Flow
----
1. The LLM hears "I want to eat sushi soon" / "我下周想去日本"
2. It calls ``wishlist_add(text="去日本吃寿司", urgency="this_week")``
3. The cron-driven ``WishlistTicker`` runs hourly, scores each pending wish
4. When a wish's score exceeds the threshold, it triggers a wish-driven
   proactive message — preempting the normal sentiment-driven proactive
   tick and consuming one of the daily proactive budget slots.

Storage
-------
``~/.claw_soul/context/wishlist.json``  — JSON list, simple enough.

Scoring
-------
score = urgency × time_appropriateness × context_match × recency_bonus
  - urgency: low=0.3 / medium=0.6 / high=1.0
  - time_appropriateness: 0.0 during quiet hours, 0.6 mid-day, 1.0 evening
  - context_match: stub for now (always 1.0 — future: weather/location aware)
  - recency_bonus: linearly decays from 1.0 (just added) to 0.3 (60 days old)
    Wishes never expire automatically; user / LLM marks them fulfilled.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any

from .. import config

logger = logging.getLogger(__name__)


URGENCY_WEIGHTS = {"low": 0.3, "medium": 0.6, "high": 1.0}
DEFAULT_TRIGGER_THRESHOLD = 0.55
RECENCY_HALF_LIFE_DAYS = 30   # score halves at 30 days, hits 0.3 floor at ~60d
SURFACE_COOLDOWN_HOURS = 12   # don't re-surface a wish within this window


def _wishlist_path() -> str:
    return os.path.join(str(config.CLAWSOUL_HOME), "context", "wishlist.json")


@dataclass
class Wish:
    id: str
    text: str
    urgency: str = "medium"
    status: str = "pending"          # pending | fulfilled | dismissed
    created_at: str = ""
    fulfilled_at: str | None = None
    last_surfaced_at: str | None = None
    surface_count: int = 0
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, text: str, urgency: str = "medium", context: dict | None = None) -> "Wish":
        return cls(
            id=uuid.uuid4().hex[:12],
            text=text.strip(),
            urgency=urgency if urgency in URGENCY_WEIGHTS else "medium",
            created_at=datetime.now().isoformat(timespec="seconds"),
            context=context or {},
        )


class WishlistManager:
    """JSON-backed wishlist with scoring & due-detection helpers."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _wishlist_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> list[Wish]:
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [Wish(**w) for w in data]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[Wishlist] failed to load %s: %s", self.path, exc)
            return []

    def _save(self, wishes: list[Wish]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([asdict(w) for w in wishes], f, indent=2, ensure_ascii=False)

    # ── CRUD ─────────────────────────────────────────────────────────────

    def add(self, text: str, urgency: str = "medium", context: dict | None = None) -> Wish:
        wishes = self._load()
        # Dedupe by identical text in pending set
        for w in wishes:
            if w.status == "pending" and w.text.strip() == text.strip():
                return w
        wish = Wish.new(text, urgency, context)
        wishes.append(wish)
        self._save(wishes)
        logger.info("[Wishlist] added '%s' (id=%s urgency=%s)", text[:50], wish.id, urgency)
        return wish

    def mark_fulfilled(self, wish_id: str) -> bool:
        wishes = self._load()
        for w in wishes:
            if w.id == wish_id and w.status == "pending":
                w.status = "fulfilled"
                w.fulfilled_at = datetime.now().isoformat(timespec="seconds")
                self._save(wishes)
                return True
        return False

    def dismiss(self, wish_id: str) -> bool:
        wishes = self._load()
        for w in wishes:
            if w.id == wish_id and w.status == "pending":
                w.status = "dismissed"
                self._save(wishes)
                return True
        return False

    def list_pending(self) -> list[Wish]:
        return [w for w in self._load() if w.status == "pending"]

    def mark_surfaced(self, wish_id: str) -> None:
        wishes = self._load()
        for w in wishes:
            if w.id == wish_id:
                w.last_surfaced_at = datetime.now().isoformat(timespec="seconds")
                w.surface_count += 1
                self._save(wishes)
                return

    # ── Scoring ──────────────────────────────────────────────────────────

    @staticmethod
    def _recency_score(created_at: str) -> float:
        try:
            created = datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            return 0.5
        days_old = max(0.0, (datetime.now() - created).total_seconds() / 86400)
        # Exponential decay with floor at 0.3
        decay = 0.5 ** (days_old / RECENCY_HALF_LIFE_DAYS)
        return max(0.3, decay)

    @staticmethod
    def _time_appropriateness(now: datetime) -> float:
        """Quiet hours → 0, mid-day → 0.6, after-work / evening → 1.0."""
        h = now.hour
        if h < 8 or h >= 23:
            return 0.0  # quiet
        if 8 <= h < 12:
            return 0.5  # morning, casual time
        if 12 <= h < 18:
            return 0.7  # midday / afternoon, OK
        return 1.0      # 18-23, evening, prime nudge time

    def score(self, wish: Wish, now: datetime | None = None) -> float:
        now = now or datetime.now()

        # Cool-down check
        if wish.last_surfaced_at:
            try:
                last = datetime.fromisoformat(wish.last_surfaced_at)
                if (now - last) < timedelta(hours=SURFACE_COOLDOWN_HOURS):
                    return 0.0
            except ValueError:
                pass

        urgency = URGENCY_WEIGHTS.get(wish.urgency, 0.5)
        time_factor = self._time_appropriateness(now)
        recency = self._recency_score(wish.created_at)
        context_match = 1.0  # placeholder for future weather/loc match
        return urgency * time_factor * context_match * recency

    def due_now(self, now: datetime | None = None,
                threshold: float = DEFAULT_TRIGGER_THRESHOLD) -> list[tuple[Wish, float]]:
        """Return (wish, score) pairs whose score is at/above threshold,
        highest-scored first."""
        now = now or datetime.now()
        ranked = [
            (w, self.score(w, now))
            for w in self.list_pending()
        ]
        ranked = [(w, s) for w, s in ranked if s >= threshold]
        ranked.sort(key=lambda ws: ws[1], reverse=True)
        return ranked
