"""
Cultural calendar lookup — per-country holidays, observances, shopping events.

Three-tier lookup, fastest first:

  1. Pg cache (``public.culture_calendars`` row keyed by country_code)
  2. Tigris object  (``culture/calendars/<CC>.json`` in the photos bucket)
  3. *Not* generated on the fly — the data is curated and seeded by
     ``scripts/seed_culture_calendars.py``; if the lookup misses both
     Pg and Tigris we return ``None`` and let the caller fall back to
     "no calendar context".  That keeps runtime predictable and free
     of LLM-spend at request time.

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

import logging
import os
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta

import httpx

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_TIGRIS_PREFIX = "culture/calendars"
_PG_TABLE = "/rest/v1/culture_calendars"

# In-process cache — calendars never change inside a process lifetime,
# so we don't need to re-hit Pg on every chat turn.  Keyed by upper-cased
# country code; ``None`` means "looked up, missing, don't retry".
_memo: dict[str, dict | None] = {}


# ── Public API ────────────────────────────────────────────────────────


def get_calendar(country_code: str) -> dict | None:
    """Return the full calendar payload for ``country_code``, or ``None``
    if we haven't seeded one.

    Only positive hits are memoized.  Caching ``None`` would poison the
    cache on a transient Pg/Tigris error and require a worker restart to
    recover; one extra round-trip per miss is the cheaper tradeoff.
    """
    cc = (country_code or "").upper()
    if not cc:
        return None
    if cc in _memo:
        return _memo[cc]
    payload = _fetch_from_pg(cc)
    if payload is None:
        payload = _fetch_from_tigris(cc)
        if payload is not None:
            _save_to_pg(cc, payload, source="tigris-restored")
    if payload is not None:
        _memo[cc] = payload
    return payload


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


# ── Storage helpers ───────────────────────────────────────────────────


def _pg_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _pg_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _pg_configured() -> bool:
    return bool(_pg_url() and _pg_key())


def _pg_headers(prefer: str = "return=representation") -> dict[str, str]:
    return {
        "apikey":        _pg_key(),
        "Authorization": f"Bearer {_pg_key()}",
        "Content-Type":  "application/json",
        "Prefer":        prefer,
    }


def _fetch_from_pg(cc: str) -> dict | None:
    if not _pg_configured():
        return None
    try:
        r = httpx.get(
            _pg_url() + _PG_TABLE,
            params={"country_code": f"eq.{cc}", "select": "payload"},
            headers=_pg_headers(),
            timeout=8,
        )
        if not r.is_success:
            logger.debug("[culture] Pg fetch %s -> %s", cc, r.status_code)
            return None
        rows = r.json() or []
        if not rows:
            return None
        return rows[0].get("payload")
    except Exception as exc:
        logger.debug("[culture] Pg fetch %s errored: %s", cc, exc)
        return None


def _save_to_pg(cc: str, payload: dict, *, source: str) -> None:
    if not _pg_configured():
        return
    try:
        httpx.post(
            _pg_url() + _PG_TABLE,
            params={"on_conflict": "country_code"},
            json={
                "country_code": cc,
                "payload":      payload,
                "source":       source,
            },
            headers=_pg_headers("resolution=merge-duplicates,return=minimal"),
            timeout=8,
        )
    except Exception as exc:
        logger.debug("[culture] Pg save %s errored: %s", cc, exc)


def _fetch_from_tigris(cc: str) -> dict | None:
    try:
        from .image_gen import tigris  # reuses photo bucket
    except Exception:
        return None
    if not tigris.is_configured():
        return None
    key = f"{_TIGRIS_PREFIX}/{cc}.json"
    url = tigris.presign_get(key, expires_sec=120)
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=10)
        if not r.is_success:
            return None
        return r.json()
    except Exception as exc:
        logger.debug("[culture] Tigris fetch %s errored: %s", cc, exc)
        return None


__all__ = [
    "get_calendar",
    "get_holiday_for_date",
    "get_upcoming",
    "SCHEMA_VERSION",
]
