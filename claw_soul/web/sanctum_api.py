"""
Sanctum-landing API endpoints.

Powers the redesigned dashboard landing page:

  GET /api/sanctum/hero          → latest selfie/candid metadata
  GET /api/sanctum/photo/{name}  → stream a photo file
  GET /api/sanctum/status        → companion status + tagline
  GET /api/sanctum/milestones    → timeline + bonding level

Hero + photo come from the on-disk ``PhotoAlbum``; status + milestones are
computed from the local SQLite ``events`` / ``turns`` tables.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

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
    """The most recent photo (selfie or candid) for the Sanctum hero card.

    Returns ``{photo: null}`` when the album is empty, so the frontend can
    show its "no photo yet" placeholder.
    """
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
    """The most recent photos for the Sanctum gallery, newest first."""
    try:
        limit = int(request.query_params.get("limit", "24"))
    except Exception:
        limit = 24
    limit = max(1, min(limit, 60))

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



async def photo(filename: str, request: Request):
    """Stream a photo from the local album.

    One install, one album, so there is no ownership check to make — the
    only guard needed is against a filename escaping the album directory.
    """
    # Path traversal guard — only allow basename matches.
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="bad filename")

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
    last_at = _fetch_last_message_at()
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
    rows = _fetch_events(kinds=["milestone", "bonding_level"], limit=20)
    active_days = _fetch_active_days()
    turn_count = _fetch_turn_count()
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


# ── Local reads ─────────────────────────────────────────────────────────
#
# These used to hit Postgres. On a single machine everything already lives
# in the local SQLite store, so read it directly — before this, every panel
# below rendered permanently empty (Level 1, 0 messages, no timeline) while
# the real data sat on disk two function calls away.


def _store():
    from ..core.storage import StorageManager
    return StorageManager.instance()


def _fetch_events(*, kinds: list[str], limit: int = 20) -> list[dict]:
    """Most recent events of the given kinds, newest first."""
    out: list[dict] = []
    for kind in kinds:
        out.extend(_store().recent_events(kind=kind, limit=limit))
    out.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return out[:limit]


def _fetch_turn_count() -> int:
    row = _store()._conn().execute(          # noqa: SLF001
        "SELECT COUNT(*) FROM turns WHERE role = 'user'").fetchone()
    return int(row[0]) if row else 0


def _fetch_active_days() -> int:
    """Distinct calendar days with at least one message from the user."""
    row = _store()._conn().execute(          # noqa: SLF001
        "SELECT COUNT(DISTINCT substr(ts, 1, 10)) FROM turns "
        "WHERE role = 'user'").fetchone()
    return int(row[0]) if row else 0


def _fetch_last_message_at() -> datetime | None:
    row = _store()._conn().execute(          # noqa: SLF001
        "SELECT ts FROM turns ORDER BY ts DESC LIMIT 1").fetchone()
    if not row or not row[0]:
        return None
    try:
        ts = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


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
