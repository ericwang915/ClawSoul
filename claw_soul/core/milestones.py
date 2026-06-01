"""
Milestone emitter.

Called once after each successful user/assistant turn in the worker.
Cheap idempotent checks against Postgres state — emit a row into the
``events`` table when a trigger fires (first chat, day-streak, message-
count threshold, bonding-level up).

Wired into Postgres only (SaaS mode).  Single-tenant dev installs
without Supabase configured silently skip — no local fallback because
the milestones UI lives on the dashboard which already needs Pg.

Keep this thread / async-safe and side-effect free outside the
narrow window of "INSERT event row".  Failures must NOT block the
agent reply path.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)


# ── Pg helpers ──────────────────────────────────────────────────────────


def _pg_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _pg_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _pg_configured() -> bool:
    return bool(_pg_url() and _pg_key())


def _headers() -> dict[str, str]:
    return {
        "apikey":        _pg_key(),
        "Authorization": f"Bearer {_pg_key()}",
        "Content-Type":  "application/json",
    }


# ── Trigger thresholds ──────────────────────────────────────────────────


_TURN_THRESHOLDS = [1, 10, 50, 100, 500, 1000, 5000]
_STREAK_THRESHOLDS = [3, 7, 14, 30, 90, 180, 365]
_BONDING_THRESHOLDS = [0, 10, 25, 60, 140, 320, 720, 1620, 3640]


def _level_for_score(score: float) -> int:
    for i in range(len(_BONDING_THRESHOLDS) - 1, -1, -1):
        if score >= _BONDING_THRESHOLDS[i]:
            return i + 1
    return 1


# ── Public entry point ──────────────────────────────────────────────────


async def maybe_emit_after_turn(user_id: str) -> None:
    """Check trigger conditions and emit any newly-reached milestones.

    Safe to call after every dispatched chat — internal idempotency
    uses an event-kind+key lookup so we never emit the same milestone
    twice.
    """
    if not _pg_configured() or not user_id:
        return
    try:
        # Pull the lightweight counters we need.  One round trip for
        # turn count, one for distinct day count, one for which
        # milestones have already been emitted.
        turn_count = await _count_turns(user_id)
        if turn_count <= 0:
            return  # nothing happened yet
        active_days = await _count_active_days(user_id)
        existing = await _existing_milestone_keys(user_id)

        score = active_days * 2.0 + turn_count * 0.1 + len(existing) * 5.0
        new_events: list[dict] = []

        # ── turn-count milestones ─────────────────────────────────
        for thr in _TURN_THRESHOLDS:
            key = f"turns:{thr}"
            if turn_count >= thr and key not in existing:
                new_events.append({
                    "user_id": user_id,
                    "kind":    "milestone",
                    "payload": {
                        "key":   key,
                        "title": _title_for_turns(thr),
                        "icon":  "💬",
                    },
                })

        # ── streak milestones ─────────────────────────────────────
        for thr in _STREAK_THRESHOLDS:
            key = f"streak:{thr}"
            if active_days >= thr and key not in existing:
                new_events.append({
                    "user_id": user_id,
                    "kind":    "milestone",
                    "payload": {
                        "key":   key,
                        "title": f"{thr}-day streak together",
                        "icon":  "🔥",
                    },
                })

        # ── bonding-level milestones ──────────────────────────────
        level = _level_for_score(score)
        last_level_key = f"bonding_level:{level}"
        if level > 1 and last_level_key not in existing:
            new_events.append({
                "user_id": user_id,
                "kind":    "bonding_level",
                "payload": {
                    "key":   last_level_key,
                    "title": f"Soul Bond Level {level} reached",
                    "icon":  "💗",
                    "level": level,
                    "score": round(score, 1),
                },
            })

        if new_events:
            await _insert_events(new_events)
            for ev in new_events:
                logger.info("[milestones] emitted %s for %s",
                            ev["payload"].get("key"), user_id[:8])
    except Exception as exc:
        # Never let milestone emission break the reply path.
        logger.warning("[milestones] emit failed: %s", exc)


async def emit_first_use(user_id: str, *, skill: str) -> None:
    """Fire a 'first time using <skill>' milestone.  Idempotent on the
    skill key — repeated calls within the same user are no-ops.

    Called from skill-handler paths (e.g. after the first successful
    selfie / candid / letter generation).
    """
    if not _pg_configured() or not user_id or not skill:
        return
    key = f"first:{skill}"
    try:
        existing = await _existing_milestone_keys(user_id)
        if key in existing:
            return
        await _insert_events([{
            "user_id": user_id,
            "kind":    "milestone",
            "payload": {
                "key":   key,
                "title": _title_for_first(skill),
                "icon":  _icon_for_skill(skill),
            },
        }])
        logger.info("[milestones] first-%s for %s", skill, user_id[:8])
    except Exception as exc:
        logger.warning("[milestones] first-use emit failed: %s", exc)


# ── Title helpers ───────────────────────────────────────────────────────


def _title_for_turns(thr: int) -> str:
    return {
        1:    "First conversation",
        10:   "10 messages exchanged",
        50:   "50 messages exchanged",
        100:  "100 messages — we're really talking",
        500:  "500 messages together",
        1000: "1,000 messages",
        5000: "5,000 messages — inseparable",
    }.get(thr, f"{thr} messages")


def _title_for_first(skill: str) -> str:
    return {
        "selfie":   "First selfie shared",
        "candid":   "First candid shot",
        "letter":   "First long letter",
        "horoscope":"First horoscope reading",
        "look_back":"First photo-album recap",
    }.get(skill, f"First {skill}")


def _icon_for_skill(skill: str) -> str:
    return {
        "selfie":   "📷",
        "candid":   "📸",
        "letter":   "💌",
        "horoscope":"🔮",
        "look_back":"🖼️",
    }.get(skill, "✨")


# ── Pg primitives ───────────────────────────────────────────────────────


async def _count_turns(user_id: str) -> int:
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_pg_url()}/rest/v1/turns",
                params={"user_id": f"eq.{user_id}", "select": "id", "limit": "1"},
                headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"},
            )
        cr = r.headers.get("content-range") or ""
        if "/" in cr:
            return int(cr.rsplit("/", 1)[1])
    except Exception:
        pass
    return 0


async def _count_active_days(user_id: str) -> int:
    """Distinct UTC days the user has chatted in the last year.

    Uses the ``count_active_days`` SQL RPC (migration 012) so Postgres
    does the COUNT(DISTINCT date) instead of shipping up to 5000 rows to
    de-dup in Python on the hot path."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{_pg_url()}/rest/v1/rpc/count_active_days",
                json={"p_user_id": user_id, "p_since": cutoff},
                headers={**_headers(), "Content-Type": "application/json"},
            )
        if not r.is_success:
            logger.warning("[milestones] active-days RPC failed: %s %s",
                           r.status_code, r.text[:200])
            return 0
        return int(r.json())
    except Exception as exc:
        logger.warning("[milestones] active-days RPC errored: %s", exc)
        return 0


async def _existing_milestone_keys(user_id: str) -> set[str]:
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"{_pg_url()}/rest/v1/events",
                params={
                    "user_id": f"eq.{user_id}",
                    "kind":    'in.("milestone","bonding_level")',
                    "select":  "payload",
                    # This builds the set of ALREADY-achieved keys; an arbitrary
                    # truncation would drop achieved keys and re-fire milestones.
                    # Order newest-first and lift the cap well above the bounded
                    # milestone catalog so the set stays complete.
                    "order":   "ts.desc",
                    "limit":   "1000",
                },
                headers=_headers(),
            )
        if not r.is_success:
            return set()
        out: set[str] = set()
        for row in r.json() or []:
            key = (row.get("payload") or {}).get("key")
            if key:
                out.add(key)
        return out
    except Exception:
        return set()


async def _insert_events(rows: list[dict]) -> None:
    if not rows:
        return
    async with httpx.AsyncClient(timeout=8) as c:
        await c.post(
            f"{_pg_url()}/rest/v1/events",
            json=rows,
            headers={**_headers(), "Prefer": "return=minimal"},
        )
