"""
Supabase access for the router/scheduler.

Reads the `user_settings` table (already-existing) for bot tokens + tiers,
and read/write of `user_machines` (migration 003) for the per-user Fly
machine state.  Uses the service-role key, so RLS is bypassed — this code
is owner-only (router service).

All helpers are async and return plain dicts (not pydantic models) to keep
the surface small.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _url() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/")


def _service_key() -> str:
    return os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def _headers() -> dict[str, str]:
    key = _service_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


# Shared connection-pooled async client for the per-dispatch hot paths
# (token lookup, machine lookup, tick/update claims, message touch).  The
# router is one event loop, so a single lazily-built client is reused for
# the process lifetime — avoids a TLS handshake on every webhook/tick.
_ACLIENT: httpx.AsyncClient | None = None


def _aclient() -> httpx.AsyncClient:
    global _ACLIENT
    if _ACLIENT is None:
        _ACLIENT = httpx.AsyncClient(timeout=10)
    return _ACLIENT


@dataclass
class UserMachineRow:
    user_id: str
    machine_id: str
    region: str
    state: str
    tier: str
    webhook_url: str | None
    image_ref: str | None
    cpus: int
    memory_mb: int
    onboarded: bool = False
    last_message_at: str | None = None     # ISO-8601


def _row(d: dict) -> UserMachineRow:
    return UserMachineRow(
        user_id=d["user_id"],
        machine_id=d["machine_id"],
        region=d.get("region", ""),
        state=d.get("state", "unknown"),
        tier=d.get("tier", "free"),
        webhook_url=d.get("webhook_url"),
        image_ref=d.get("image_ref"),
        cpus=int(d.get("cpus") or 1),
        memory_mb=int(d.get("memory_mb") or 256),
        onboarded=bool(d.get("onboarded") or False),
        last_message_at=d.get("last_message_at"),
    )


# ── user_machines CRUD ───────────────────────────────────────────────────

async def get_user_machine(user_id: str) -> UserMachineRow | None:
    r = await _aclient().get(
        f"{_url()}/rest/v1/user_machines",
        params={"user_id": f"eq.{user_id}", "select": "*"},
        headers=_headers(),
    )
    r.raise_for_status()
    rows = r.json() or []
    return _row(rows[0]) if rows else None


async def upsert_user_machine(
    user_id: str,
    *,
    machine_id: str | None = None,
    region: str | None = None,
    state: str | None = None,
    tier: str | None = None,
    webhook_url: str | None = None,
    image_ref: str | None = None,
) -> bool:
    """Upsert or partial-update a user_machines row.

    Full inserts (machine_id present) use POST + merge-duplicates so the
    NOT NULL columns are populated.  Partial updates of an already-existing
    row would otherwise fail the INSERT path's NOT NULL checks even though
    ON CONFLICT would UPDATE — so we PATCH instead when machine_id is None.
    """
    body: dict[str, Any] = {}
    for k, v in (
        ("region", region), ("state", state),
        ("tier", tier), ("webhook_url", webhook_url), ("image_ref", image_ref),
    ):
        if v is not None:
            body[k] = v
    if machine_id is not None:
        body["machine_id"] = machine_id
        body["user_id"] = user_id
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{_url()}/rest/v1/user_machines",
                params={"on_conflict": "user_id"},
                headers={**_headers(),
                         "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=body,
            )
    else:
        if not body:
            return True  # nothing to change
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.patch(
                f"{_url()}/rest/v1/user_machines",
                params={"user_id": f"eq.{user_id}"},
                headers={**_headers(),
                         "Prefer": "return=minimal"},
                json=body,
            )
    if not r.is_success:
        logger.warning("[router-db] upsert user_machines failed: %s %s",
                       r.status_code, r.text[:200])
    return r.is_success


async def mark_user_machine_state(user_id: str, state: str) -> None:
    """Quick state transition without changing other fields."""
    await upsert_user_machine(user_id, state=state)


async def touch_message_at(user_id: str) -> None:
    """Bump last_message_at to now() — called on inbound and outbound
    Telegram messages so the scheduler's proactive/selfie throttle can
    skip ticks that would land in the middle of a conversation."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    r = await _aclient().patch(
        f"{_url()}/rest/v1/user_machines",
        params={"user_id": f"eq.{user_id}"},
        headers={**_headers(), "Prefer": "return=minimal"},
        json={"last_message_at": now_iso},
    )
    if not r.is_success:
        logger.warning("[router-db] touch_message_at failed: %s %s",
                       r.status_code, r.text[:200])


async def mark_onboarded(user_id: str) -> None:
    """Flip onboarded=true once the worker confirms the agent has a
    bot_name in memory.  Idempotent — re-calling is cheap and harmless."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.patch(
            f"{_url()}/rest/v1/user_machines",
            params={"user_id": f"eq.{user_id}",
                    "onboarded": "is.false"},  # only if currently false
            headers={**_headers(), "Prefer": "return=minimal"},
            json={"onboarded": True},
        )
    if r.is_success:
        logger.info("[router-db] marked %s onboarded", user_id[:8])
    else:
        logger.warning("[router-db] mark_onboarded failed: %s %s",
                       r.status_code, r.text[:200])


_PAGE_SIZE = 1000


async def list_user_machines(
    *, state: str | None = None, tier: str | None = None,
) -> list[UserMachineRow]:
    """List all user_machines rows, paginated.

    PostgREST/Supabase cap a single response at ``db-max-rows`` (commonly
    1000).  Without paging, every user past the cap is silently dropped —
    so at 1000+ users the scheduler would simply stop ticking the tail.
    We page with limit/offset until a short page comes back.
    """
    base: dict[str, str] = {"select": "*", "order": "user_id.asc"}
    if state:
        base["state"] = f"eq.{state}"
    if tier:
        base["tier"] = f"eq.{tier}"
    out: list[UserMachineRow] = []
    offset = 0
    async with httpx.AsyncClient(timeout=15) as c:
        while True:
            params = {**base, "limit": str(_PAGE_SIZE), "offset": str(offset)}
            r = await c.get(f"{_url()}/rest/v1/user_machines",
                            params=params, headers=_headers())
            r.raise_for_status()
            rows = r.json() or []
            out.extend(_row(d) for d in rows)
            if len(rows) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
    return out


async def delete_user_machine(user_id: str) -> None:
    """Used after destroy_machine() — keeps the user_machines row out of
    the active set. We could soft-delete (state='destroyed') instead, but
    we keep it simple."""
    async with httpx.AsyncClient(timeout=10) as c:
        await c.delete(
            f"{_url()}/rest/v1/user_machines",
            params={"user_id": f"eq.{user_id}"},
            headers=_headers(),
        )


# ── user_settings reads (token lookup for /telegram/<token> routing) ────

async def find_user_id_by_bot_token(token: str) -> str | None:
    """Reverse-lookup: given a Telegram bot token, return its owning user_id.
    Used by the webhook router to figure out where to forward updates."""
    r = await _aclient().get(
        f"{_url()}/rest/v1/user_settings",
        params={
            "select": "user_id",
            "telegram_bot_token": f"eq.{token}",
        },
        headers=_headers(),
    )
    if not r.is_success:
        return None
    rows = r.json() or []
    return rows[0]["user_id"] if rows else None


# ── Per-tick leader election (scheduled_runs) ───────────────────────────

async def try_claim_tick(job_id: str, fire_minute: str,
                         *, machine_id: str | None = None) -> bool:
    """Insert a (job_id, fire_minute) row; return True iff THIS process
    won the race. The PK constraint guarantees exactly one winner across
    all router machines firing the same job at the same minute.

    fire_minute must be an ISO-8601 timestamp truncated to the minute so
    two machines whose clocks differ by sub-second still compute the
    same key. Callers should pass datetime.utcnow().replace(second=0,
    microsecond=0).isoformat().
    """
    body = {
        "job_id":      job_id,
        "fire_minute": fire_minute,
        "machine_id":  machine_id or os.environ.get("FLY_MACHINE_ID", ""),
    }
    r = await _aclient().post(
        f"{_url()}/rest/v1/scheduled_runs",
        headers={**_headers(), "Prefer": "return=minimal"},
        json=body,
        timeout=5,
    )
    if r.status_code in (200, 201, 204):
        return True
    # 409 (Conflict) — other machine got it. Anything else = real error.
    if r.status_code != 409:
        # PostgREST surfaces unique-violation as 23505 in the body.
        if "23505" in r.text:
            return False
        logger.warning("[router-db] try_claim_tick unexpected %s: %s",
                       r.status_code, r.text[:200])
    return False


async def try_claim_telegram_update(user_id: str, update_id: int) -> bool:
    """Claim a Telegram update_id by INSERT; return True iff THIS delivery
    won (i.e. we haven't processed this update before).

    Durable cross-machine, cross-cold-boot dedup: the (user_id, update_id)
    PK means a Telegram retry — even one that lands after the worker has
    suspended and lost its in-memory ring buffer — loses the race and is
    dropped here, before we pay the cost of waking the worker.

    Fails OPEN (returns True) only on an unexpected transport error, so a
    transient Pg blip degrades to "might double-process" rather than
    "drop the user's message entirely".
    """
    body = {"user_id": user_id, "update_id": update_id}
    try:
        r = await _aclient().post(
            f"{_url()}/rest/v1/telegram_updates",
            headers={**_headers(), "Prefer": "return=minimal"},
            json=body,
            timeout=5,
        )
    except httpx.HTTPError as exc:
        logger.warning("[router-db] claim_telegram_update transport err: %s", exc)
        return True  # fail open — don't silently swallow the user's message
    if r.status_code in (200, 201, 204):
        return True
    if r.status_code == 409 or "23505" in r.text:
        return False  # already claimed → duplicate delivery
    logger.warning("[router-db] claim_telegram_update unexpected %s: %s",
                   r.status_code, r.text[:200])
    return True  # unknown error — prefer processing over dropping


async def prune_telegram_updates(older_than_hours: int = 24) -> int:
    """DELETE telegram_updates dedup rows older than *older_than_hours*."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours))
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(
            f"{_url()}/rest/v1/telegram_updates",
            params={"claimed_at": f"lt.{cutoff.isoformat()}"},
            headers={**_headers(), "Prefer": "return=representation"},
        )
    if not r.is_success:
        logger.warning("[router-db] prune_telegram_updates failed: %s %s",
                       r.status_code, r.text[:200])
        return 0
    try:
        return len(r.json() or [])
    except Exception:
        return 0


async def heartbeat_router_instance(instance_id: str) -> None:
    """Upsert this router instance's liveness row (last_seen=now()).

    Called every reconcile so the sharding logic can see which instances
    are alive.  Best-effort: a failure just means the caller falls back to
    owning all users (the DB claim still prevents double-dispatch)."""
    from datetime import datetime, timezone
    r = await _aclient().post(
        f"{_url()}/rest/v1/router_instances",
        params={"on_conflict": "instance_id"},
        headers={**_headers(),
                 "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={"instance_id": instance_id,
              "last_seen": datetime.now(timezone.utc).isoformat()},
    )
    if not r.is_success:
        logger.warning("[router-db] heartbeat failed: %s %s",
                       r.status_code, r.text[:200])


async def list_live_router_instances(*, stale_after_sec: int = 150) -> list[str]:
    """Return instance_ids seen within the freshness window, sorted ascending.

    Stale window (150s) is > 2 reconcile intervals so a single missed
    heartbeat doesn't flap an instance out of the ring."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=stale_after_sec)).isoformat()
    r = await _aclient().get(
        f"{_url()}/rest/v1/router_instances",
        params={"last_seen": f"gte.{cutoff}",
                "select": "instance_id", "order": "instance_id.asc"},
        headers=_headers(),
    )
    r.raise_for_status()
    return [row["instance_id"] for row in (r.json() or [])]


async def prune_router_instances(older_than_hours: int = 24) -> int:
    """DELETE router_instances rows not seen for *older_than_hours*."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours))
    r = await _aclient().delete(
        f"{_url()}/rest/v1/router_instances",
        params={"last_seen": f"lt.{cutoff.isoformat()}"},
        headers={**_headers(), "Prefer": "return=representation"},
        timeout=30,
    )
    if not r.is_success:
        logger.warning("[router-db] prune_router_instances failed: %s %s",
                       r.status_code, r.text[:200])
        return 0
    try:
        return len(r.json() or [])
    except Exception:
        return 0


async def prune_scheduled_runs(older_than_hours: int = 24) -> int:
    """DELETE scheduled_runs rows older than *older_than_hours*. Returns
    the number of rows removed (best-effort; PostgREST returns the deleted
    rows when Prefer: return=representation is set)."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours))
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(
            f"{_url()}/rest/v1/scheduled_runs",
            params={"claimed_at": f"lt.{cutoff.isoformat()}"},
            headers={**_headers(),
                     "Prefer": "return=representation"},
        )
    if not r.is_success:
        logger.warning("[router-db] prune_scheduled_runs failed: %s %s",
                       r.status_code, r.text[:200])
        return 0
    try:
        return len(r.json() or [])
    except Exception:
        return 0


async def user_companion_exists(user_id: str) -> bool:
    """True iff the user has completed the web wizard (saved a
    user_companion row).  Used at provision time to retroactively flip
    onboarded=true for users who finished the wizard before their
    user_machines row existed."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{_url()}/rest/v1/user_companion",
            params={"user_id": f"eq.{user_id}", "select": "user_id"},
            headers=_headers(),
        )
    return r.is_success and bool(r.json() or [])


async def get_user_setting_row(user_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{_url()}/rest/v1/user_settings",
            params={"user_id": f"eq.{user_id}", "select": "*"},
            headers=_headers(),
        )
    if not r.is_success:
        return None
    rows = r.json() or []
    return rows[0] if rows else None
