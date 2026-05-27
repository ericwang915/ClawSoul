"""
ClawSoul Router/Scheduler service — Phase 2a.

A small always-on FastAPI app that:
  - Receives Telegram webhooks for every user's bot at
    ``/telegram/<bot-token>`` and dispatches to that user's worker machine.
  - Runs the central APScheduler clock for every paid user's cron jobs
    (daily planner, proactive ticks, scheduled selfies) and wakes their
    machine via the Fly API when a tick fires.
  - Exposes a tiny admin API for the dashboard to provision / destroy
    user machines.

Lives in its own Fly app (``clawsoul-router``) so its uptime is independent
from the per-user worker fleet.

See ``SAAS_PHASE2_PLAN.md`` for the full architecture.
"""

from .app import create_router_app

__all__ = ["create_router_app"]
