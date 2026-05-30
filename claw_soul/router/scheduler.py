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

import hashlib
import logging
import os
import socket
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

# Skip proactive/selfie ticks that would fire within this window of the
# user's last inbound/outbound message — avoids dogpiling check-ins on
# top of an active conversation.
_QUIET_AFTER_MESSAGE_MIN = 30

# Default selfie slots — these become per-user once we expose a config UI.
_DEFAULT_SELFIE_SLOTS = ["10:00", "16:00", "20:00"]


def _stable_offset(*parts: str, mod: int) -> int:
    """Deterministic per-user offset in [0, mod).

    Uses md5 (not builtin hash, which is per-process salted) so a user's
    tick minute/second stays stable across router restarts — avoids
    reshuffling everyone's schedule on every deploy.
    """
    h = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
    return int(h, 16) % mod


class RouterScheduler:
    """Owns the AsyncIOScheduler + the reconcile loop."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._known_users: set[str] = set()
        # Sharding: this instance owns users where hash % count == index.
        # Defaults (index 0 / count 1) mean "own everyone" — the single-
        # instance / fallback behaviour.
        self._instance_id = (
            os.environ.get("FLY_MACHINE_ID") or socket.gethostname() or "local"
        )
        self._shard_index = 0
        self._shard_count = 1

    # ── sharding ────────────────────────────────────────────────────────

    async def _refresh_shard(self) -> None:
        """Heartbeat self and recompute (shard_index, shard_count) from the
        set of live router instances.

        On any Pg error we fall back to owning everyone (index 0 / count 1);
        the per-tick DB claim still prevents double-dispatch, so the worst
        case is the pre-sharding behaviour, never a missed tick."""
        try:
            await db.heartbeat_router_instance(self._instance_id)
            live = await db.list_live_router_instances()
        except Exception as exc:
            logger.warning("[router-sched] shard refresh failed, owning all: %s", exc)
            self._shard_index, self._shard_count = 0, 1
            return
        # Our own heartbeat may not be readable yet (write/read race) — make
        # sure we're in the ring so two fresh instances don't both pick index 0.
        if self._instance_id not in live:
            live = sorted(set(live) | {self._instance_id})
        self._shard_count = max(1, len(live))
        self._shard_index = live.index(self._instance_id)

    def _owns(self, user_id: str) -> bool:
        """True if this instance is responsible for *user_id* under the
        current shard assignment."""
        if self._shard_count <= 1:
            return True
        return _stable_offset(user_id, mod=self._shard_count) == self._shard_index

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

        Two gates:
          - tier must be ``paid`` or higher (free is reactive-only).
          - ``onboarded`` must be true; otherwise the worker hasn't
            confirmed the user named the companion yet, and proactive/
            selfie ticks would interrupt the onboarding chat.
        """
        # Recompute our shard first so we only register jobs for our slice.
        await self._refresh_shard()

        try:
            rows = await db.list_user_machines()
        except httpx.HTTPError as exc:
            logger.warning("[router-sched] reconcile read failed: %s", exc)
            return

        active: dict[str, str] = {}   # user_id -> tier
        for row in rows:
            if row.tier == "free":
                continue
            if not row.onboarded:
                continue
            if not self._owns(row.user_id):
                continue   # another router instance owns this user's ticks
            active[row.user_id] = row.tier
        active_ids = set(active)

        # Incremental diff: only touch APScheduler for users who joined or
        # left the active set.  Re-adding all N users' 6 jobs every minute
        # (the old behaviour) churns the jobstore needlessly at scale.
        joined = active_ids - self._known_users
        left = self._known_users - active_ids
        for uid in joined:
            self._ensure_user_jobs(uid, active[uid])
        for stale in left:
            self._remove_user_jobs(stale)
        self._known_users = active_ids
        logger.info("[router-sched] reconciled — shard %d/%d — %d owned paid "
                    "users (+%d/-%d)",
                    self._shard_index, self._shard_count,
                    len(active_ids), len(joined), len(left))

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

        # Proactive tick — twice an hour, but SHARDED per user so 1000
        # users don't all fire on :00/:30 and stampede the wake path.
        # Each user gets their own minute-of-half-hour from a stable hash.
        off = _stable_offset(user_id, "proactive", mod=30)
        sched.add_job(
            self._fire_proactive, CronTrigger(minute=f"{off},{off + 30}"),
            id=f"proactive:{user_id}",
            kwargs={"user_id": user_id},
            replace_existing=True,
        )

        # Scheduled selfie slots — jitter the minute (0-15) and second
        # within the slot hour, again per-user, so the shared 10/16/20:00
        # slots don't fan out as a synchronized burst.
        for slot in _DEFAULT_SELFIE_SLOTS:
            hh, mm = slot.split(":")
            jmin = (int(mm) + _stable_offset(user_id, slot, "min", mod=16)) % 60
            jsec = _stable_offset(user_id, slot, "sec", mod=60)
            sched.add_job(
                self._fire_selfie,
                CronTrigger(hour=int(hh), minute=jmin, second=jsec),
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

    async def _is_quiet_window(self, user_id: str) -> bool:
        """True if the user has chatted within the last 30 min — we
        shouldn't drop a proactive/selfie on top of an active session."""
        row = await db.get_user_machine(user_id)
        if not row or not row.last_message_at:
            return False
        try:
            from datetime import datetime, timedelta, timezone
            last = datetime.fromisoformat(row.last_message_at.replace("Z", "+00:00"))
        except Exception:
            return False
        return (datetime.now(timezone.utc) - last) < timedelta(
            minutes=_QUIET_AFTER_MESSAGE_MIN
        )

    async def _fire_planner(self, user_id: str) -> None:
        # Planner is internal state (today_plan.md), not an outbound
        # message, so it doesn't need the quiet-window check.
        if not await self._claim(f"planner:{user_id}"):
            return
        await dispatch.dispatch(user_id, "planner_tick", {})

    async def _fire_proactive(self, user_id: str) -> None:
        if not await self._claim(f"proactive:{user_id}"):
            return
        if await self._is_quiet_window(user_id):
            logger.info("[router-sched] skip proactive for %s (quiet window)",
                        user_id[:8])
            return
        await dispatch.dispatch(user_id, "proactive_tick",
                                {"ts": datetime.utcnow().isoformat()})

    async def _fire_selfie(self, user_id: str, slot: str) -> None:
        if not await self._claim(f"selfie:{user_id}:{slot}"):
            return
        if await self._is_quiet_window(user_id):
            logger.info("[router-sched] skip selfie:%s for %s (quiet window)",
                        slot, user_id[:8])
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
        try:
            deleted = await db.prune_telegram_updates(_PRUNE_OLDER_THAN_HOURS)
            logger.info("[router-sched] pruned %d telegram_updates rows", deleted)
        except Exception as exc:
            logger.warning("[router-sched] telegram_updates prune failed: %s", exc)
        try:
            deleted = await db.prune_router_instances(_PRUNE_OLDER_THAN_HOURS)
            logger.info("[router-sched] pruned %d router_instances rows", deleted)
        except Exception as exc:
            logger.warning("[router-sched] router_instances prune failed: %s", exc)


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
