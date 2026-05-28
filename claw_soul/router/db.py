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
    )


# ── user_machines CRUD ───────────────────────────────────────────────────

async def get_user_machine(user_id: str) -> UserMachineRow | None:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
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


async def list_user_machines(
    *, state: str | None = None, tier: str | None = None,
) -> list[UserMachineRow]:
    params: dict[str, str] = {"select": "*"}
    if state:
        params["state"] = f"eq.{state}"
    if tier:
        params["tier"] = f"eq.{tier}"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{_url()}/rest/v1/user_machines",
                        params=params, headers=_headers())
    r.raise_for_status()
    return [_row(d) for d in (r.json() or [])]


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
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
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
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(
            f"{_url()}/rest/v1/scheduled_runs",
            headers={**_headers(),
                     "Prefer": "return=minimal"},
            json=body,
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
