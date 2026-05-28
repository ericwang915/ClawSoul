"""
Central clock for the router/scheduler service.

Replaces the per-user APScheduler that used to live inside each worker.
The router knows every user's tier + chat_id + selfie schedule from the
Supabase tables; it fires cron ticks here and POSTs them through
:func:`dispatch.dispatch` to the user's worker machine (waking it first
if the machine is suspended).

Job IDs are namespaced by user so we can selectively reload one user
without touching the rest:

    planner:<user_id>
    proactive:<user_id>
    selfie:<user_id>:HHMM

Reloading happens whenever Supabase signals user_settings changes, or
on a periodic (5 min) full reconcile.

This is **only** the in-memory job mechanics — Supabase reads are
delegated to ``db.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, dispatch

logger = logging.getLogger(__name__)


# Reconcile every minute so newly-onboarded users start receiving ticks
# from *all* router machines (not just the one that handled provision)
# within at most 60 s.  The cost is tiny — one Supabase row read per
# minute per router instance.
_RECONCILE_INTERVAL_MIN = 1

# How long scheduled_runs rows are kept before prune.  PK uniqueness is
# only meaningful in the current minute, so a 24-h window is generous —
# enough for any debugging on "who fired what" without growing forever.
_PRUNE_OLDER_THAN_HOURS = 24

# Default selfie slots — these become per-user once we expose a config UI.
_DEFAULT_SELFIE_SLOTS = ["10:00", "16:00", "20:00"]


class RouterScheduler:
    """Owns the AsyncIOScheduler + the reconcile loop."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._known_users: set[str] = set()

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        # First reconcile loads everyone synchronously so the first tick
        # after boot doesn't miss a beat.
        await self.reconcile()
        # Periodic reconcile picks up new / paused users.
        self._scheduler.add_job(
            self.reconcile, "interval",
            minutes=_RECONCILE_INTERVAL_MIN,
            id="_router_reconcile",
            replace_existing=True,
        )
        # Daily prune of the scheduled_runs leader-claim table.  Wrapped
        # in a claim so only one router actually runs the DELETE.
        self._scheduler.add_job(
            self._fire_prune, CronTrigger(hour=3, minute=0),
            id="_router_prune_scheduled_runs",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("[router-sched] started (UTC) — reconcile every %d min",
                    _RECONCILE_INTERVAL_MIN)

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # ── reconcile ───────────────────────────────────────────────────────

    async def reconcile(self) -> None:
        """Re-read user_machines + user_settings, add jobs for new paid
        users, remove jobs for users no longer in scope.

        Only ``tier='paid'`` (or higher) users get the proactive +
        selfie + planner ticks; free tier is reactive-only.
        """
        try:
            rows = await db.list_user_machines()
        except httpx.HTTPError as exc:
            logger.warning("[router-sched] reconcile read failed: %s", exc)
            return

        active_ids = set()
        for row in rows:
            if row.tier == "free":
                continue
            active_ids.add(row.user_id)
            self._ensure_user_jobs(row.user_id, row.tier)

        # Drop jobs for users we used to schedule but no longer should.
        for stale in self._known_users - active_ids:
            self._remove_user_jobs(stale)
        self._known_users = active_ids
        logger.info("[router-sched] reconciled — %d active paid users",
                    len(active_ids))

    # ── per-user job ops ───────────────────────────────────────────────

    def _ensure_user_jobs(self, user_id: str, tier: str) -> None:
        sched = self._scheduler

        # Daily planner — 00:01 local UTC for now
        sched.add_job(
            self._fire_planner, CronTrigger(hour=0, minute=1),
            id=f"planner:{user_id}",
            kwargs={"user_id": user_id},
            replace_existing=True,
        )

        # Proactive tick — every 5 min, worker decides whether to send
        sched.add_job(
            self._fire_proactive, CronTrigger(minute="*/5"),
            id=f"proactive:{user_id}",
            kwargs={"user_id": user_id},
            replace_existing=True,
        )

        # Scheduled selfie slots
        for slot in _DEFAULT_SELFIE_SLOTS:
            hh, mm = slot.split(":")
            sched.add_job(
                self._fire_selfie,
                CronTrigger(hour=int(hh), minute=int(mm)),
                id=f"selfie:{user_id}:{hh}{mm}",
                kwargs={"user_id": user_id, "slot": slot},
                replace_existing=True,
            )

    def _remove_user_jobs(self, user_id: str) -> None:
        for j in list(self._scheduler.get_jobs()):
            if j.id.endswith(f":{user_id}") or f":{user_id}:" in j.id:
                self._scheduler.remove_job(j.id)

    # ── tick handlers ──────────────────────────────────────────────────
    # Each handler tries to claim (job_id, current_minute) in the
    # scheduled_runs table before dispatching. With N router machines
    # firing simultaneously, the unique PK guarantees exactly one
    # actually sends the tick to the worker.

    async def _claim(self, job_id: str) -> bool:
        minute = datetime.utcnow().replace(second=0, microsecond=0).isoformat()
        won = await db.try_claim_tick(job_id, minute)
        if not won:
            logger.debug("[router-sched] lost claim for %s @ %s", job_id, minute)
        return won

    async def _fire_planner(self, user_id: str) -> None:
        if not await self._claim(f"planner:{user_id}"):
            return
        await dispatch.dispatch(user_id, "planner_tick", {})

    async def _fire_proactive(self, user_id: str) -> None:
        if not await self._claim(f"proactive:{user_id}"):
            return
        await dispatch.dispatch(user_id, "proactive_tick",
                                {"ts": datetime.utcnow().isoformat()})

    async def _fire_selfie(self, user_id: str, slot: str) -> None:
        if not await self._claim(f"selfie:{user_id}:{slot}"):
            return
        await dispatch.dispatch(user_id, "selfie_tick", {"slot": slot})

    async def _fire_prune(self) -> None:
        if not await self._claim("prune:scheduled_runs"):
            return
        try:
            deleted = await db.prune_scheduled_runs(_PRUNE_OLDER_THAN_HOURS)
            logger.info("[router-sched] pruned %d scheduled_runs rows", deleted)
        except Exception as exc:
            logger.warning("[router-sched] prune failed: %s", exc)


# Singleton — the router app initializes this on startup.
_singleton: RouterScheduler | None = None


def get_scheduler() -> RouterScheduler:
    global _singleton
    if _singleton is None:
        _singleton = RouterScheduler()
    return _singleton


async def kick_reconcile() -> None:
    """Public — dashboard can call this when it has just provisioned a new
    user, instead of waiting up to 5 min for the next periodic reconcile."""
    await get_scheduler().reconcile()


def _user_ids(jobs: Iterable) -> set[str]:
    # used by tests to peek at scheduled users
    out: set[str] = set()
    for j in jobs:
        if ":" in j.id and j.id != "_router_reconcile":
            head, _, rest = j.id.partition(":")
            if head in ("planner", "proactive", "selfie"):
                uid = rest.split(":")[0]
                out.add(uid)
    return out
