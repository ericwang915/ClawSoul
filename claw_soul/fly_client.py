"""
Thin Fly Machines API client.

Used by the router/scheduler service (and any admin endpoints) to spawn,
wake, suspend, and destroy per-user worker machines.  Stays small and
opinionated — no SDK, no retries beyond a single backoff, no fancy
caching.  Failures bubble back so the caller can put the user on a
maintenance message instead of silently going dark.

Env vars consumed:

    FLY_API_TOKEN          required — Fly Machines API token
    FLY_WORKER_APP_NAME    target app (e.g. "clawsoul-worker")
    FLY_WORKER_IMAGE       image ref (e.g. "registry.fly.io/clawsoul:deployment-…")
    FLY_DEFAULT_REGION     default region (e.g. "sin")
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


FLY_API_BASE = "https://api.machines.dev/v1"


# ── Custom exceptions ──────────────────────────────────────────────────────

class FlyAPIError(RuntimeError):
    """Fly Machines API returned a non-2xx."""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"Fly API {status}: {body[:200]}")


class FlyConfigError(RuntimeError):
    """Operator hasn't set the required env vars."""


# ── Config + transport ─────────────────────────────────────────────────────

def _api_token() -> str:
    tok = os.environ.get("FLY_API_TOKEN", "").strip()
    if not tok:
        raise FlyConfigError("FLY_API_TOKEN not set")
    return tok


def _app_name() -> str:
    app = os.environ.get("FLY_WORKER_APP_NAME", "").strip()
    if not app:
        raise FlyConfigError("FLY_WORKER_APP_NAME not set")
    return app


def _worker_image() -> str:
    img = os.environ.get("FLY_WORKER_IMAGE", "").strip()
    if not img:
        raise FlyConfigError("FLY_WORKER_IMAGE not set")
    return img


def _default_region() -> str:
    return os.environ.get("FLY_DEFAULT_REGION", "sin")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_token()}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, *, json: dict | None = None,
             timeout: float = 30.0) -> dict:
    """Single HTTP request with one retry on 5xx."""
    url = FLY_API_BASE + path
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(method, url, json=json, headers=_headers())
            if resp.is_success:
                if resp.text.strip():
                    return resp.json()
                return {}
            if 500 <= resp.status_code < 600 and attempt == 1:
                time.sleep(1.0)
                continue
            raise FlyAPIError(resp.status_code, resp.text)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt == 1:
                time.sleep(1.0)
                continue
            raise
    # Defensive — shouldn't reach
    raise last_exc or RuntimeError("fly request failed")


# ── Spec dataclass ─────────────────────────────────────────────────────────

@dataclass
class MachineSpec:
    """Minimal launch spec for a per-user worker machine.

    Anything not set here uses sensible defaults: 1 shared cpu, 256 MB,
    region from FLY_DEFAULT_REGION, auto-suspend on.
    """
    user_id: str
    region: str = ""
    cpus: int = 1
    cpu_kind: str = "shared"
    memory_mb: int = 256
    auto_suspend: bool = True
    image: str = ""               # defaults to FLY_WORKER_IMAGE
    extra_env: dict[str, str] = field(default_factory=dict)
    tier: str = "free"


# ── Public API ─────────────────────────────────────────────────────────────

def create_user_machine(spec: MachineSpec) -> dict[str, Any]:
    """Spawn a new per-user worker machine. Returns the Fly machine record."""
    body = {
        "name": f"user-{spec.user_id[:12]}",
        "region": spec.region or _default_region(),
        "config": {
            "image": spec.image or _worker_image(),
            "guest": {
                "cpu_kind": spec.cpu_kind,
                "cpus": spec.cpus,
                "memory_mb": spec.memory_mb,
            },
            "env": {
                "CLAW_USER_ID": spec.user_id,
                "CLAW_TIER": spec.tier,
                **spec.extra_env,
            },
            "services": [{
                "ports": [{"port": 80, "handlers": ["http"]},
                          {"port": 443, "handlers": ["tls", "http"]}],
                "protocol": "tcp",
                "internal_port": 7788,
                # Auto-stop when idle; Fly Proxy wakes us on incoming requests.
                "autostop": "suspend" if spec.auto_suspend else "off",
                "auto_start_machines": True,
            }],
            "metadata": {
                "claw_user_id": spec.user_id,
                "claw_tier": spec.tier,
            },
        },
    }
    return _request("POST", f"/apps/{_app_name()}/machines", json=body)


def start_machine(machine_id: str) -> dict[str, Any]:
    """Wake a suspended/stopped machine. Returns immediately; the machine
    enters 'starting' then 'started'."""
    return _request("POST", f"/apps/{_app_name()}/machines/{machine_id}/start")


def stop_machine(machine_id: str) -> dict[str, Any]:
    return _request("POST", f"/apps/{_app_name()}/machines/{machine_id}/stop")


def suspend_machine(machine_id: str) -> dict[str, Any]:
    return _request("POST", f"/apps/{_app_name()}/machines/{machine_id}/suspend")


def destroy_machine(machine_id: str, *, force: bool = False) -> dict[str, Any]:
    """Tear down a machine permanently (e.g. user cancelled)."""
    path = f"/apps/{_app_name()}/machines/{machine_id}"
    if force:
        path += "?force=true"
    return _request("DELETE", path)


def get_machine(machine_id: str) -> dict[str, Any]:
    return _request("GET", f"/apps/{_app_name()}/machines/{machine_id}")


def list_machines() -> list[dict[str, Any]]:
    res = _request("GET", f"/apps/{_app_name()}/machines")
    return res if isinstance(res, list) else (res.get("machines") or [])


def wait_for_state(
    machine_id: str,
    target_state: str,
    *,
    timeout_sec: float = 30.0,
    poll_interval: float = 0.5,
) -> bool:
    """Block until the machine reaches *target_state* or timeout. Returns
    True on hit. Used right after start_machine to confirm the wake."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            m = get_machine(machine_id)
        except FlyAPIError:
            return False
        if m.get("state") == target_state:
            return True
        time.sleep(poll_interval)
    return False
