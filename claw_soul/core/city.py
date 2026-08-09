"""
City profile lookup — coordinates, timezone, and hometown "vibe" text.

Mirrors :mod:`claw_soul.core.culture` (per-country holiday calendars):
an in-process memo over a local JSON dataset. Curated data, never generated
at runtime, so where she lives stays consistent and costs no LLM spend.

Drop your own ``<CC>.json`` in ``<CLAWSOUL_HOME>/data/cities/`` to add or
override a country; it takes precedence over anything bundled.

Payload schema (v1), one row per country, all its cities inside:

    {
      "country_code":   "US",
      "schema_version": 1,
      "cities": {
        "New York": {
          "lat": 40.7128, "lon": -74.0060,
          "timezone": "America/New_York",
          "language": "en",
          "vibe": "New York runs on density. Bagels at 7am ..."
        },
        ...
      }
    }

Public API:

    get_city(country_code, region)  -> dict | None   # one city's profile
    get_country_cities(country_code) -> dict | None  # all cities for a country
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# In-process cache keyed by upper-cased country code.  ``None`` means
# "looked up, missing, don't retry".  Only positive hits are memoized
# (a transient read error shouldn't poison the cache).
_memo: dict[str, dict | None] = {}


# ── Public API ────────────────────────────────────────────────────────


def get_country_cities(country_code: str) -> dict | None:
    """Return ``{city_name: profile}`` for ``country_code``, or ``None``."""
    cc = (country_code or "").upper()
    if not cc:
        return None
    if cc in _memo:
        payload = _memo[cc]
    else:
        payload = _load_local(cc)
        _memo[cc] = payload
    if not payload:
        return None
    return payload.get("cities") or None


def all_regions_by_country() -> dict[str, list[str]]:
    """``{country_code: [city names in authored order]}`` for the setup wizard.

    Scans whichever ``<CC>.json`` files exist. Returns ``{}`` when none do —
    the wizard's city field is free-text, so an empty map just means "no
    suggestions", not a broken flow.
    """
    from .. import config as _config
    out: dict[str, list[str]] = {}
    dirs = [
        Path(__file__).resolve().parent.parent / "data" / "cities",
        Path(_config.CLAWSOUL_HOME) / "data" / "cities",   # user's wins
    ]
    for d in dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    cities = (json.load(f) or {}).get("cities") or {}
            except (OSError, ValueError) as exc:
                logger.warning("[city] could not read %s: %s", path, exc)
                continue
            if cities:
                out[path.stem.upper()] = list(cities)
    return out



def get_city(country_code: str, region: str | None) -> dict | None:
    """Return one city's profile dict (lat/lon/timezone/language/vibe),
    augmented with ``country_code`` and ``city``.  ``None`` if unknown.

    Matches ``region`` against the country's cities case-insensitively so
    "new york" / "New York" both resolve.
    """
    cc = (country_code or "").upper()
    region = (region or "").strip()
    if not cc or not region:
        return None
    cities = get_country_cities(cc)
    if not cities:
        return None
    hit = cities.get(region)
    if hit is None:
        low = region.lower()
        for name, prof in cities.items():
            if name.lower() == low:
                hit = prof
                region = name
                break
    if hit is None:
        return None
    return {**hit, "country_code": cc, "city": region}


def get_city_events(country_code: str, region: str | None, *,
                    within_days: int = 14, from_date=None) -> list[dict]:
    """Signature city events (festivals) falling in ``[from_date,
    from_date+within_days)``, sorted by date.

    Events are annual + approximate (month/day) — grounding for the agent
    ("SXSW is on right now"), not a ticketing calendar.  Each returned item
    is the event dict plus a ``date`` (YYYY-MM-DD) for the matched year.
    """
    from datetime import date as _date
    from datetime import timedelta

    prof = get_city(country_code, region)
    if not prof:
        return []
    events = prof.get("events") or []
    if not events:
        return []
    start = from_date or _date.today()
    end_excl = start + timedelta(days=max(1, int(within_days)))
    hits: list[tuple[_date, dict]] = []
    for ev in events:
        m, d = ev.get("month"), ev.get("day")
        if not isinstance(m, int) or not isinstance(d, int):
            continue
        for year in (start.year, end_excl.year):
            try:
                anchor = _date(year, m, d)
            except ValueError:
                continue
            if start <= anchor < end_excl:
                hits.append((anchor, {**ev, "date": anchor.isoformat()}))
    hits.sort(key=lambda pair: pair[0])
    seen: set[str] = set()
    out: list[dict] = []
    for _anchor, item in hits:
        key = item["date"] + "|" + (item.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ── Local dataset ─────────────────────────────────────────────────────
#
# City profiles are plain JSON on disk, looked up in two places so you can
# add or override a city without touching the package:
#
#   1. <CLAWSOUL_HOME>/data/cities/<CC>.json   — yours, wins
#   2. claw_soul/data/cities/<CC>.json         — bundled with the package
#
# Format: {"schema": 1, "cities": {"<City>": {"vibe": "...", "events": [...]}}}
# Missing file = no rich city profile; every caller degrades gracefully.


def _load_local(cc: str) -> dict | None:
    """Read ``<CC>.json`` from the user dir, then the bundled dir."""
    from .. import config as _config
    candidates = [
        Path(_config.CLAWSOUL_HOME) / "data" / "cities" / f"{cc}.json",
        Path(__file__).resolve().parent.parent / "data" / "cities" / f"{cc}.json",
    ]
    for path in candidates:
        try:
            if path.is_file():
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, ValueError) as exc:
            logger.warning("[city] could not read %s: %s", path, exc)
    return None



__all__ = ["get_city", "get_country_cities", "get_city_events",
           "all_regions_by_country", "SCHEMA_VERSION"]
