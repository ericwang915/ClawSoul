"""
City profile lookup — coordinates, timezone, and hometown "vibe" text.

Mirrors :mod:`claw_soul.core.culture` (per-country cultural calendars):
a three-tier lookup, fastest first —

  1. In-process memo (keyed by country code)
  2. Pg cache (``public.city_profiles`` row keyed by country_code)
  3. Tigris object ``culture/cities/<CC>.json``

Data is curated and seeded by ``scripts/seed_city_profiles.py`` — never
generated at runtime, so weather/timezone/backstory lookups stay free of
LLM spend and predictable.

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

import logging
import os

import httpx

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_TIGRIS_PREFIX = "culture/cities"
_PG_TABLE = "/rest/v1/city_profiles"

# In-process cache keyed by upper-cased country code.  ``None`` means
# "looked up, missing, don't retry".  Only positive hits are memoized
# (a transient Pg/Tigris error shouldn't poison the cache).
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
        payload = _fetch_from_pg(cc)
        if payload is None:
            payload = _fetch_from_tigris(cc)
            if payload is not None:
                _save_to_pg(cc, payload, source="tigris-restored")
        if payload is not None:
            _memo[cc] = payload
    if not payload:
        return None
    return payload.get("cities") or None


def all_regions_by_country() -> dict[str, list[str]]:
    """``{country_code: [city names in authored order]}`` for the setup wizard.

    One Pg read over ``city_profiles``.  Returns ``{}`` (logged) if Pg isn't
    configured or the read fails — the wizard's city field is free-text, so
    an empty map just means "no suggestions", not a broken flow.
    """
    if not _pg_configured():
        return {}
    try:
        r = httpx.get(
            _pg_url() + _PG_TABLE,
            params={"select": "country_code,payload"},
            headers=_pg_headers(), timeout=10,
        )
        if not r.is_success:
            logger.warning("[city] all_regions read -> %s", r.status_code)
            return {}
        out: dict[str, list[str]] = {}
        for row in r.json() or []:
            cc = row.get("country_code")
            cities = (row.get("payload") or {}).get("cities") or {}
            if cc:
                out[cc] = list(cities.keys())
        return out
    except Exception as exc:
        logger.warning("[city] all_regions read errored: %s", exc)
        return {}


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


# ── Storage helpers (mirror culture.py) ────────────────────────────────


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
            logger.debug("[city] Pg fetch %s -> %s", cc, r.status_code)
            return None
        rows = r.json() or []
        return rows[0].get("payload") if rows else None
    except Exception as exc:
        logger.debug("[city] Pg fetch %s errored: %s", cc, exc)
        return None


def _save_to_pg(cc: str, payload: dict, *, source: str) -> None:
    if not _pg_configured():
        return
    try:
        httpx.post(
            _pg_url() + _PG_TABLE,
            params={"on_conflict": "country_code"},
            json={"country_code": cc, "payload": payload, "source": source},
            headers=_pg_headers("resolution=merge-duplicates,return=minimal"),
            timeout=8,
        )
    except Exception as exc:
        logger.debug("[city] Pg save %s errored: %s", cc, exc)


def _fetch_from_tigris(cc: str) -> dict | None:
    try:
        from .image_gen import tigris
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
        logger.debug("[city] Tigris fetch %s errored: %s", cc, exc)
        return None


__all__ = ["get_city", "get_country_cities", "get_city_events",
           "all_regions_by_country", "SCHEMA_VERSION"]
