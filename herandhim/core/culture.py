"""
Cultural calendar lookup — per-country holidays, observances, shopping events.

An in-process memo over a local JSON dataset. Curated, never generated on
the fly: a miss returns ``None`` and the caller simply goes without calendar
context, which keeps runtime predictable and free of LLM spend.

Drop your own ``<CC>.json`` in ``<HERANDHIM_HOME>/data/holidays/`` to add a
country; it takes precedence over anything bundled.

Payload schema (v1):

    {
      "country_code":   "US",
      "country_name":   "United States",
      "language":       "en",
      "schema_version": 1,
      "fixed_dates": [
        {"month": 1, "day": 1, "name": "New Year's Day",
         "name_local": "New Year's Day",
         "type": "public", "emoji": "🎉",
         "significance": "Civic kickoff — fireworks, resolutions."}
      ],
      "movable_dates_by_year": {
        "2026": [{"date": "2026-11-26", "name": "Thanksgiving", ...}]
      }
    }

Types: ``public`` | ``religious`` | ``shopping`` | ``observance`` |
``cultural``.

Public API:

    get_calendar(cc)              -> dict | None
    get_holiday_for_date(cc, d)   -> dict | None
    get_upcoming(cc, within_days) -> list[dict]
"""

from __future__ import annotations

import json
import logging
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# In-process cache — calendars never change inside a process lifetime,
# so we don't need to re-hit Pg on every chat turn.  Keyed by upper-cased
# country code; ``None`` means "looked up, missing, don't retry".
_memo: dict[str, dict | None] = {}


# ── Public API ────────────────────────────────────────────────────────


def get_calendar(country_code: str) -> dict | None:
    """Return the full calendar payload for ``country_code``, or ``None``
    if no dataset exists for it."""
    cc = (country_code or "").upper()
    if not cc:
        return None
    if cc not in _memo:
        _memo[cc] = _load_local(cc)
    return _memo[cc]


def get_holiday_for_date(country_code: str, when: _date | _datetime) -> dict | None:
    """Return the holiday entry that *exactly* falls on ``when``, or
    ``None`` if nothing's scheduled.  Movable holidays are matched by
    explicit ``YYYY-MM-DD`` in ``movable_dates_by_year``."""
    cal = get_calendar(country_code)
    if not cal:
        return None
    d = when.date() if isinstance(when, _datetime) else when
    for entry in cal.get("fixed_dates") or []:
        if entry.get("month") == d.month and entry.get("day") == d.day:
            return entry
    movable = (cal.get("movable_dates_by_year") or {}).get(str(d.year)) or []
    for entry in movable:
        try:
            if _datetime.strptime(entry["date"], "%Y-%m-%d").date() == d:
                return entry
        except (KeyError, ValueError):
            continue
    return None


def get_upcoming(country_code: str, *, within_days: int = 14,
                 from_date: _date | None = None) -> list[dict]:
    """Return holidays falling in ``[from_date, from_date+within_days)``
    sorted by date.  Each item is a copy of the calendar entry with a
    ``date`` field added (YYYY-MM-DD) so the caller has a stable key
    regardless of fixed/movable origin.

    Used by the agent boot context ("upcoming: Thanksgiving in 3 days").
    """
    cal = get_calendar(country_code)
    if not cal:
        return []
    start = from_date or _date.today()
    end_excl = start + timedelta(days=max(1, int(within_days)))
    hits: list[tuple[_date, dict]] = []

    # Fixed-date entries — materialize for the year(s) within window.
    for entry in cal.get("fixed_dates") or []:
        m, d = entry.get("month"), entry.get("day")
        if not isinstance(m, int) or not isinstance(d, int):
            continue
        for year in (start.year, end_excl.year):
            try:
                anchor = _date(year, m, d)
            except ValueError:
                continue  # e.g. Feb 29 in non-leap year
            if start <= anchor < end_excl:
                merged = {**entry, "date": anchor.isoformat()}
                hits.append((anchor, merged))

    # Movable entries are already explicit per year.
    movable_by_year = cal.get("movable_dates_by_year") or {}
    for year in {start.year, end_excl.year}:
        for entry in movable_by_year.get(str(year)) or []:
            try:
                anchor = _datetime.strptime(entry["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            if start <= anchor < end_excl:
                hits.append((anchor, entry))

    hits.sort(key=lambda pair: pair[0])
    # Deduplicate by (date, name) — fixed-date sweep across two years
    # can otherwise double-list a Jan 1 that's in window for both.
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for anchor, item in hits:
        key = (item.get("date") or anchor.isoformat(), item.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ── Local dataset ─────────────────────────────────────────────────────
#
# Holiday calendars are plain JSON on disk, looked up in two places so you
# can add your own country without touching the package:
#
#   1. <HERANDHIM_HOME>/data/holidays/<CC>.json   — yours, wins
#   2. herandhim/data/holidays/<CC>.json         — bundled with the package
#
# Missing file = she just doesn't bring up that country's holidays.


def _load_local(cc: str) -> dict | None:
    from .. import config as _config
    candidates = [
        Path(_config.HERANDHIM_HOME) / "data" / "holidays" / f"{cc}.json",
        Path(__file__).resolve().parent.parent / "data" / "holidays" / f"{cc}.json",
    ]
    for path in candidates:
        try:
            if path.is_file():
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, ValueError) as exc:
            logger.warning("[culture] could not read %s: %s", path, exc)
    return None



__all__ = [
    "get_calendar",
    "get_holiday_for_date",
    "get_upcoming",
    "SCHEMA_VERSION",
]
