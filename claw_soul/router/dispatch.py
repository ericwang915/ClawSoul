"""
Wake-and-forward primitive used by both the Telegram webhook and the
scheduler tick paths.

We forward through Fly's flycast (the internal-only address that goes
through Fly Proxy) rather than direct 6PN, because flycast triggers
wake-on-request for suspended machines and re-attaches networking
before delivering the request.  6PN direct (``<id>.vm.<app>.internal``)
doesn't — it can resolve to a machine whose network stack hasn't come
back yet, and we'd see "All connection attempts failed".

We still call the Fly Machines API ``start`` first as a belt-and-
braces hint, then POST to ``http://<app>.flycast/dispatch`` with the
``Fly-Force-Instance-Id`` header pinning the request to the per-user
machine (the worker app is shared but each user's worker is a distinct
machine_id).
"""

from __future__ import annotations

import asyncio
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


def _worker_flycast_base() -> str:
    """Flycast routes through Fly Proxy: wakes suspended machines on
    request and only forwards once networking is ready.  Use
    ``Fly-Force-Instance-Id`` to pin to a specific worker machine
    within the multi-machine worker app."""
    app = fly_client._app_name()  # noqa: SLF001 — internal helper, OK to use
    return f"http://{app}.flycast"


async def wake_if_needed(row: db.UserMachineRow) -> None:
    """Idempotent wake.  Always asks Fly to start — ``row.state`` in our
    DB can lag the real Fly state by minutes (we update it only on wake/
    dispatch), and a stale 'starting' read here used to make us skip
    the wake on a machine that was actually 'suspended', leading to DNS
    resolution failures when we then POSTed to
    ``<id>.vm.<app>.internal``.

    Calling start on an already-running machine returns 409, treated as
    success.  When we did wake a stopped/suspended machine, we poll
    briefly for ``started`` so its internal DNS is live before the
    caller dispatches.
    """
    t0 = time.monotonic()
    woke = False
    try:
        fly_client.start_machine(row.machine_id)
        woke = True
    except fly_client.FlyAPIError as exc:
        if exc.status not in (409,):
            raise DispatchError(f"wake failed: {exc}") from exc
        # 409 = already started; nothing to wait for
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info("[router] wake user=%s machine=%s woke=%s latency=%dms",
                    row.user_id[:8], row.machine_id, woke, latency_ms)

    if woke:
        # Wait for Fly to fully reattach networking before the caller
        # tries to resolve the machine's internal DNS.
        ok = fly_client.wait_for_state(
            row.machine_id, "started", timeout_sec=15.0, poll_interval=0.5,
        )
        if not ok:
            raise DispatchError(
                f"machine {row.machine_id} did not reach 'started' within 15s"
            )
    await db.mark_user_machine_state(row.user_id, "running")


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

    url = _worker_flycast_base() + "/dispatch"
    body = {"kind": kind, "payload": payload}
    headers = {"Fly-Force-Instance-Id": row.machine_id}
    # Flycast handles the wake + network re-attach, but a freshly-
    # resumed-from-suspend machine may still be re-binding 7788 for a
    # second or two.  Retry connection errors a few times.
    r = None
    last_exc: Exception | None = None
    for attempt in range(6):
        try:
            async with httpx.AsyncClient(timeout=WORKER_DISPATCH_TIMEOUT) as c:
                r = await c.post(url, json=body, headers=headers)
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < 5:
                await asyncio.sleep(1.5)
                continue
    if r is None:
        logger.warning("[router] %s dispatch network err after retries: %s",
                       user_id[:8], last_exc)
        return False

    if not r.is_success:
        logger.warning(
            "[router] %s dispatch %s → %d %s",
            user_id[:8], kind, r.status_code, r.text[:200],
        )
        return False

    await db.upsert_user_machine(user_id, state="running")
    return True
