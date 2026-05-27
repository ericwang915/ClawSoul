"""
Wake-and-forward primitive used by both the Telegram webhook and the
scheduler tick paths.

Each per-user worker machine listens at ``http://user-<uid>.internal:7788``
(Fly's internal DNS handles routing).  When we have an event for that user,
we:

  1. Make sure the machine is running — call Fly Machines API `start`
     if its persisted state is `suspended` / `stopped`.
  2. POST the event JSON to ``http://user-<uid>.internal/dispatch``.
  3. Stamp ``last_active`` on user_machines.

Failures (machine doesn't exist, refused, timeout) are logged with the
user id and bubble back so callers can fall back gracefully.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .. import fly_client
from . import db

logger = logging.getLogger(__name__)


WORKER_DISPATCH_TIMEOUT = 30.0


class DispatchError(RuntimeError):
    """Raised when the wake / forward chain fails."""


def _worker_url(machine_id: str) -> str:
    """Fly's internal DNS lets us reach a specific machine by id.
    ``http://<machine_id>.vm.<app>.internal:7788`` resolves only inside the
    Fly network, which is exactly the boundary we want."""
    app = fly_client._app_name()  # noqa: SLF001 — internal helper, OK to use
    return f"http://{machine_id}.vm.{app}.internal:7788"


async def wake_if_needed(row: db.UserMachineRow) -> None:
    """Idempotent wake — only call start_machine when the row says we're
    not already running."""
    if row.state in ("running", "started", "starting"):
        return
    t0 = time.monotonic()
    try:
        fly_client.start_machine(row.machine_id)
    except fly_client.FlyAPIError as exc:
        # 409 typically means "already started" — Fly API is racy here
        if exc.status not in (409,):
            raise DispatchError(f"wake failed: {exc}") from exc
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info("[router] wake user=%s machine=%s latency=%dms",
                    row.user_id[:8], row.machine_id, latency_ms)
    await db.mark_user_machine_state(row.user_id, "starting")


async def dispatch(user_id: str, kind: str, payload: dict[str, Any]) -> bool:
    """Wake the user's worker if asleep, then POST `{kind, payload}` to
    its /dispatch endpoint.  Returns True on 2xx, else False."""
    row = await db.get_user_machine(user_id)
    if row is None:
        logger.warning("[router] dispatch: no user_machines row for %s", user_id[:8])
        return False
    try:
        await wake_if_needed(row)
    except DispatchError as exc:
        logger.warning("[router] %s wake failed: %s", user_id[:8], exc)
        return False

    url = _worker_url(row.machine_id) + "/dispatch"
    body = {"kind": kind, "payload": payload}
    try:
        async with httpx.AsyncClient(timeout=WORKER_DISPATCH_TIMEOUT) as c:
            r = await c.post(url, json=body)
    except httpx.HTTPError as exc:
        logger.warning("[router] %s dispatch network err: %s", user_id[:8], exc)
        return False

    if not r.is_success:
        logger.warning(
            "[router] %s dispatch %s → %d %s",
            user_id[:8], kind, r.status_code, r.text[:200],
        )
        return False

    await db.upsert_user_machine(user_id, state="running")
    return True
