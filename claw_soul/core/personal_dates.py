"""
Personal dates — the user's real-life calendar the companion must not miss.

Birthdays, anniversaries, exams, interviews, flights, a friend's wedding…
Previously these lived as flat memory strings recalled only if BM25 happened
to rank them that turn — so she'd forget your birthday unless you mentioned
it the same week. Cultural holidays had a scanned calendar; the user's own
dates did not. This module gives personal dates the same treatment:

  - The LLM records them via the ``remember_date`` tool the moment they come
    up in chat ("my birthday is March 3rd", "面试在周四").
  - Every turn, the volatile context scans for dates within the next 7 days
    and injects them so she anticipates ("your interview is tomorrow —
    nervous?") and never misses the day itself.
  - The proactive scheduler checks today's hits first, so a birthday wish
    preempts a generic check-in.

Storage: one JSON file under the tenant's context dir (same durability class
as milestones/relationship state; included in the groups/compaction backup
path only if placed there, so we keep it under context/memory which ships
with the Tigris memory backup when present).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


def _store_path() -> str:
    from .. import config
    return os.path.join(str(config.CLAWSOUL_HOME), "context", "memory",
                        "personal_dates.json")


@dataclass
class PersonalDate:
    id: str
    date: str            # "YYYY-MM-DD"; recurring entries match on MM-DD yearly
    label: str           # "their birthday", "面试(新公司)"
    recurring: bool = False
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class PersonalDates:
    """Tiny JSON-backed store; all ops are best-effort and atomic-write."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _store_path()

    # ── persistence ────────────────────────────────────────────────────

    def _load(self) -> list[PersonalDate]:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [PersonalDate(**d) for d in raw if isinstance(d, dict)]
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def _save(self, items: list[PersonalDate]) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".json", dir=os.path.dirname(self._path))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([asdict(i) for i in items], f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    # ── public API ─────────────────────────────────────────────────────

    def add(self, date_str: str, label: str, recurring: bool = False) -> PersonalDate:
        # Validate/normalize the date early — a bad date silently never firing
        # is exactly the failure mode this module exists to kill.
        d = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        items = self._load()
        # Same label+date → update rather than duplicate.
        for it in items:
            if it.label == label.strip() and it.date == d.isoformat():
                it.recurring = recurring
                self._save(items)
                return it
        item = PersonalDate(id=uuid.uuid4().hex[:8], date=d.isoformat(),
                            label=label.strip(), recurring=recurring)
        items.append(item)
        self._save(items)
        return item

    def remove(self, date_id: str) -> bool:
        items = self._load()
        kept = [i for i in items if i.id != date_id]
        if len(kept) == len(items):
            return False
        self._save(kept)
        return True

    def all(self) -> list[PersonalDate]:
        return self._load()

    def upcoming(self, days: int = 7, today: date | None = None) -> list[tuple[int, PersonalDate]]:
        """Dates hitting within the next ``days`` days → [(days_until, item)].

        Recurring entries match yearly on month-day (Feb-29 folds to Feb-28 in
        non-leap years). One-off entries only match their exact date and are
        dropped from consideration once past.
        """
        today = today or date.today()
        out: list[tuple[int, PersonalDate]] = []
        for it in self._load():
            try:
                d = datetime.strptime(it.date, "%Y-%m-%d").date()
            except ValueError:
                continue
            if it.recurring:
                m, dd = d.month, d.day
                for year in (today.year, today.year + 1):
                    try:
                        occ = date(year, m, dd)
                    except ValueError:          # Feb 29 → Feb 28
                        occ = date(year, m, dd - 1)
                    delta = (occ - today).days
                    if 0 <= delta <= days:
                        out.append((delta, it))
                        break
            else:
                delta = (d - today).days
                if 0 <= delta <= days:
                    out.append((delta, it))
        out.sort(key=lambda t: t[0])
        return out

    def today_hits(self, today: date | None = None) -> list[PersonalDate]:
        return [it for delta, it in self.upcoming(days=0, today=today) if delta == 0]
