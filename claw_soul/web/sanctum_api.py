"""
Sanctum-landing API endpoints.

Powers the redesigned dashboard landing page:

  GET /api/sanctum/hero          → latest selfie/candid metadata
  GET /api/sanctum/photo/{name}  → stream a photo file (auth-gated)
  GET /api/sanctum/status        → companion status + tagline
  GET /api/sanctum/milestones    → timeline + bonding level

All endpoints read the *current* tenant via ``tenancy.get_current_user()``.
Hero + photo come from the per-tenant ``PhotoAlbum``; status + milestones
hit the Postgres ``events`` / ``turns`` / ``user_machines`` tables.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from ..core import tenancy
from ..core.image_gen import tigris
from ..core.image_gen.photo_album import PhotoAlbum

logger = logging.getLogger(__name__)


# ── Hero ────────────────────────────────────────────────────────────────


def _build_hero_caption(entry: dict) -> str:
    """Best-effort caption for the hero card.

    Selfies have an explicit ``activity`` in their stored prompt; candid
    shots have a category emoji.  Either way we fall back to a generic
    "Latest snapshot" so the UI never shows blank.
    """
    kind = entry.get("kind") or ""
    activity = (entry.get("metadata") or {}).get("activity") or entry.get("activity")
    if activity:
        return str(activity)
    if kind.startswith("candid_"):
        category = kind.split("_", 1)[1] if "_" in kind else "moment"
        cap = {
            "animal":  "saw this little one earlier",
            "scenery": "look at the view from here",
            "food":    "this is what I'm having",
            "fun":     "stumbled on this",
        }
        return cap.get(category, "snapshot from earlier")
    return "snapshot from earlier"


async def hero(request: Request) -> JSONResponse:
    """Return the most recent photo (selfie OR candid) for the Sanctum
    hero card.  Cloud mode (Tigris + Pg ``photos``) reads the latest
    row across machines; legacy single-tenant mode falls through to the
    local album.  Returns ``{photo: null}`` if neither has anything,
    so the frontend can show the "no photo yet" placeholder.
    """
    uid = tenancy.get_current_user()
    if not uid:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    if _pg_configured() and tigris.is_configured():
        latest = await _fetch_latest_photo_row(uid)
        if latest:
            url = tigris.presign_get(latest["object_key"]) or ""
            return JSONResponse({
                "photo": {
                    "filename":  latest["filename"],
                    "url":       url,
                    "caption":   latest.get("caption") or "snapshot from earlier",
                    "kind":      latest.get("kind"),
                    "timestamp": latest.get("ts"),
                },
            })
        # Fall through to local-album lookup if cloud has nothing yet
        # (e.g. first photo just generated, Pg insert raced the read).

    album = PhotoAlbum()
    latest = album.latest()  # any kind, just the newest
    if not latest:
        return JSONResponse({"photo": None})

    filename = latest.get("filename") or os.path.basename(latest.get("path", ""))
    return JSONResponse({
        "photo": {
            "filename":  filename,
            "url":       f"/api/sanctum/photo/{filename}",
            "caption":   _build_hero_caption(latest),
            "kind":      latest.get("kind"),
            "timestamp": latest.get("timestamp"),
        },
    })


async def photos(request: Request) -> JSONResponse:
    """Return the user's recent photos for the Memory Gallery.

    Cloud mode (Tigris + Pg ``photos``) is the SaaS path: photos
    generated on the per-user worker get uploaded to Tigris and
    indexed in Pg, and we surface them here with fresh presigned URLs
    so the legacy web app — running on a different Fly machine — can
    show them without touching the worker's filesystem.

    Falls through to the local on-disk album when Tigris/Pg aren't
    configured (dev / single-tenant), keeping the original behaviour.
    """
    uid = tenancy.get_current_user()
    if not uid:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    try:
        limit = int(request.query_params.get("limit", "24"))
    except Exception:
        limit = 24
    limit = max(1, min(limit, 60))

    if _pg_configured() and tigris.is_configured():
        rows = await _fetch_photo_rows(uid, limit=limit)
        items: list[dict[str, Any]] = []
        for r in rows:
            url = tigris.presign_get(r["object_key"]) or ""
            if not url:
                continue
            items.append({
                "filename":  r["filename"],
                "url":       url,
                "kind":      r.get("kind"),
                "caption":   r.get("caption") or "",
                "timestamp": r.get("ts"),
            })
        return JSONResponse({"items": items, "total": len(items)})

    album = PhotoAlbum()
    entries = album._load_index()  # noqa: SLF001 — internal helper, OK in same package
    entries = list(reversed(entries))[:limit]

    items: list[dict[str, Any]] = []
    for e in entries:
        filename = e.get("filename") or os.path.basename(e.get("path", ""))
        if not filename:
            continue
        items.append({
            "filename":  filename,
            "url":       f"/api/sanctum/photo/{filename}",
            "kind":      e.get("kind"),
            "caption":   _build_hero_caption(e),
            "timestamp": e.get("timestamp"),
        })
    return JSONResponse({"items": items, "total": len(items)})


async def _fetch_photo_row_by_name(uid: str, filename: str) -> dict | None:
    """Look up ONE photo row by (user_id, filename) — the ownership check."""
    if not _pg_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_pg_url()}/rest/v1/photos",
                params={
                    "user_id":  f"eq.{uid}",
                    "filename": f"eq.{filename}",
                    "select":   "object_key",
                    "limit":    "1",
                },
                headers=_pg_headers(),
            )
        rows = r.json() if r.is_success else []
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sanctum_api] photo ownership lookup failed: %s", exc)
        return None


async def photo(filename: str, request: Request):
    """Serve a photo — ONLY if it belongs to the current tenant.

    Cloud mode (Pg + Tigris): ownership is checked against the ``photos``
    table by (user_id, filename); a match redirects to a short-lived
    presigned Tigris URL.  A filename that exists but belongs to another
    tenant 404s — never leaks across tenants.

    Local / single-tenant mode falls back to streaming from the on-disk
    album (one tenant, no isolation concern), with a path-traversal guard.
    """
    uid = tenancy.get_current_user()
    if not uid:
        raise HTTPException(status_code=401, detail="not authenticated")

    # Path traversal guard — only allow basename matches.
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="bad filename")

    if _pg_configured() and tigris.is_configured():
        row = await _fetch_photo_row_by_name(uid, safe_name)
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        url = tigris.presign_get(row["object_key"])
        if not url:
            raise HTTPException(status_code=404, detail="not found")
        return RedirectResponse(url, status_code=302,
                                headers={"Cache-Control": "private, no-store"})

    album = PhotoAlbum()
    full_path = os.path.join(album.root, safe_name)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(full_path, headers={"Cache-Control": "private, max-age=60"})


# ── Status badge ────────────────────────────────────────────────────────


# Status windows are in seconds from "now".
_STATUS_ONLINE_SEC = 5 * 60         # last message within 5 min → Online
_STATUS_IDLE_SEC   = 60 * 60        # within 1 hour → Idle


def _status_for_gap(seconds_since_last: float | None) -> tuple[str, str, str]:
    """Return (status, color, tagline)."""
    if seconds_since_last is None:
        return ("offline", "gray", "Resting")
    if seconds_since_last < _STATUS_ONLINE_SEC:
        return ("online", "emerald", "Here with you")
    if seconds_since_last < _STATUS_IDLE_SEC:
        return ("idle", "amber", "Thinking of you")
    return ("offline", "gray", "Resting")


async def status(request: Request) -> JSONResponse:
    uid = tenancy.get_current_user()
    if not uid:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    last_at = await _fetch_last_message_at(uid)
    seconds_since = None
    if last_at is not None:
        seconds_since = (datetime.now(timezone.utc) - last_at).total_seconds()
    status_str, color, tagline = _status_for_gap(seconds_since)
    return JSONResponse({
        "status":          status_str,
        "color":           color,
        "tagline":         tagline,
        "seconds_since":   int(seconds_since) if seconds_since is not None else None,
    })


# ── Milestones + bonding level ──────────────────────────────────────────


# Bonding-level thresholds.  Score = active_days*2 + turns*0.1 + milestones*5.
# Each next level is harder than the last (Fibonacci-ish), so users feel
# steady progress early and persistent rarity at higher tiers.
_BONDING_THRESHOLDS = [0, 10, 25, 60, 140, 320, 720, 1620, 3640]


def _bonding_level_for_score(score: float) -> tuple[int, int, int]:
    """Return (level, current_threshold, next_threshold). Caps at the
    last entry — anyone past 3640 stays at level len(thresholds)-1."""
    for i in range(len(_BONDING_THRESHOLDS) - 1, -1, -1):
        if score >= _BONDING_THRESHOLDS[i]:
            curr = _BONDING_THRESHOLDS[i]
            nxt  = _BONDING_THRESHOLDS[i + 1] if i + 1 < len(_BONDING_THRESHOLDS) else curr
            return (i + 1, curr, nxt)
    return (1, 0, _BONDING_THRESHOLDS[1])


def _format_milestone_when(ts_str: str, *, now: datetime | None = None) -> str:
    """'Today, 20:40' / 'Yesterday' / 'Oct 12' style."""
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return ts_str
    now = now or datetime.now(timezone.utc)
    delta_days = (now.date() - ts.date()).days
    if delta_days == 0:
        return f"Today, {ts.strftime('%H:%M')}"
    if delta_days == 1:
        return "Yesterday"
    if delta_days < 365:
        return ts.strftime("%b %d")
    return ts.strftime("%b %d, %Y")


async def milestones(request: Request) -> JSONResponse:
    uid = tenancy.get_current_user()
    if not uid:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    rows = await _fetch_events(uid, kinds=["milestone", "bonding_level"], limit=20)
    active_days = await _fetch_active_days(uid)
    turn_count = await _fetch_turn_count(uid)
    milestone_count = len([r for r in rows if r.get("kind") == "milestone"])

    score = (active_days * 2.0) + (turn_count * 0.1) + (milestone_count * 5.0)
    level, curr_thresh, next_thresh = _bonding_level_for_score(score)
    progress_pct = 0.0
    if next_thresh > curr_thresh:
        progress_pct = max(0.0, min(1.0,
            (score - curr_thresh) / (next_thresh - curr_thresh)))

    items: list[dict[str, Any]] = []
    for r in rows[:12]:
        payload = r.get("payload") or {}
        items.append({
            "when":  _format_milestone_when(r.get("ts", "")),
            "title": payload.get("title") or payload.get("summary") or r.get("kind"),
            "kind":  r.get("kind"),
            "icon":  payload.get("icon"),   # optional emoji from emitter
            "ts":    r.get("ts"),
        })

    return JSONResponse({
        "bonding": {
            "level":        level,
            "score":        round(score, 1),
            "next_at":      next_thresh,
            "progress_pct": round(progress_pct * 100, 1),
            "active_days":  active_days,
            "turn_count":   turn_count,
        },
        "items": items,
    })


# ── Postgres helpers ────────────────────────────────────────────────────


def _pg_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _pg_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _pg_headers() -> dict[str, str]:
    return {
        "apikey":        _pg_key(),
        "Authorization": f"Bearer {_pg_key()}",
    }


def _pg_configured() -> bool:
    return bool(_pg_url() and _pg_key())


async def _fetch_events(uid: str, *, kinds: list[str], limit: int = 20) -> list[dict]:
    if not _pg_configured():
        return []
    kinds_filter = "(" + ",".join(f'"{k}"' for k in kinds) + ")"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_pg_url()}/rest/v1/events",
                params={
                    "user_id": f"eq.{uid}",
                    "kind":    f"in.{kinds_filter}",
                    "select":  "id,ts,kind,payload",
                    "order":   "ts.desc",
                    "limit":   str(limit),
                },
                headers=_pg_headers(),
            )
        if not r.is_success:
            return []
        return r.json() or []
    except Exception:
        return []


async def _fetch_turn_count(uid: str) -> int:
    if not _pg_configured():
        return 0
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_pg_url()}/rest/v1/turns",
                params={
                    "user_id": f"eq.{uid}",
                    "select":  "id",
                    "limit":   "1",
                },
                headers={**_pg_headers(),
                         "Prefer": "count=exact",
                         "Range":  "0-0"},
            )
        # PostgREST returns the total in Content-Range: 0-0/<total>
        content_range = r.headers.get("content-range") or ""
        if "/" in content_range:
            try:
                return int(content_range.rsplit("/", 1)[1])
            except ValueError:
                pass
    except Exception:
        pass
    return 0


async def _fetch_active_days(uid: str) -> int:
    """Count distinct calendar days the user has had any turn — proxy for
    relationship continuity.  Uses the ``count_active_days`` SQL RPC
    (migration 012) so Postgres does the COUNT(DISTINCT date) instead of
    shipping up to 5000 rows to the dashboard to de-dup in Python."""
    if not _pg_configured():
        return 0
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{_pg_url()}/rest/v1/rpc/count_active_days",
                json={"p_user_id": uid, "p_since": cutoff},
                headers={**_pg_headers(), "Content-Type": "application/json"},
            )
        if not r.is_success:
            logger.warning("[sanctum_api] active-days RPC failed: %s", r.status_code)
            return 0
        return int(r.json())
    except Exception as exc:
        logger.warning("[sanctum_api] active-days RPC errored: %s", exc)
        return 0


async def _fetch_photo_rows(uid: str, *, limit: int = 24) -> list[dict]:
    if not _pg_configured():
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_pg_url()}/rest/v1/photos",
                params={
                    "user_id": f"eq.{uid}",
                    "select":  "filename,object_key,kind,caption,ts",
                    "order":   "ts.desc",
                    "limit":   str(limit),
                },
                headers=_pg_headers(),
            )
        if not r.is_success:
            return []
        return r.json() or []
    except Exception as exc:
        logger.warning("[sanctum_api] photos fetch failed: %s", exc)
        return []


async def _fetch_latest_photo_row(uid: str) -> dict | None:
    rows = await _fetch_photo_rows(uid, limit=1)
    return rows[0] if rows else None


async def _fetch_last_message_at(uid: str) -> datetime | None:
    """Read user_machines.last_message_at (already maintained by the
    worker's _touch_message_at).  Falls back to None if no row."""
    if not _pg_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{_pg_url()}/rest/v1/user_machines",
                params={
                    "user_id": f"eq.{uid}",
                    "select":  "last_message_at",
                },
                headers=_pg_headers(),
            )
        if not r.is_success:
            return None
        rows = r.json() or []
        if not rows:
            return None
        v = rows[0].get("last_message_at")
        if not v:
            return None
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        return None


# ── Bonding-level helpers used by background emitters ───────────────────


def compute_bonding_score(*, active_days: int, turn_count: int,
                          milestone_count: int) -> float:
    return active_days * 2.0 + turn_count * 0.1 + milestone_count * 5.0


def compute_bonding_level(score: float) -> int:
    level, _, _ = _bonding_level_for_score(score)
    return level


__all__ = [
    "hero",
    "photo",
    "photos",
    "status",
    "milestones",
    "compute_bonding_score",
    "compute_bonding_level",
]


# Suppress unused-import warning for `math` — kept for future heavier
# bonding curves.
_ = math
