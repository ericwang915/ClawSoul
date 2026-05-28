"""
One-shot migration helper: legacy single-process tenant → dedicated worker machine.

Used exactly once per existing user (today: Eric, ``a9c257c8-…``) to cut
them over from the shared ``clawsoul`` Fly app into a per-user worker
machine in ``clawsoul-worker``.

What it does, end to end:

  1. Verify ``user_settings`` exists and has a Telegram bot token.
  2. Provision a new worker machine via Fly Machines API (idempotent —
     reuses existing if already present in ``user_machines``).
  3. Copy data from the legacy app's volume to the new worker's volume.
     Strategy: ``fly sftp`` is not async-friendly from here, so we
     emit a printable plan and trust the operator to run the rsync
     commands.  (Future: do it inside the worker on first boot by
     reading from a Tigris/R2 staging bucket — see Phase 2g.)
  4. Set the Telegram webhook on the user's bot.
  5. Set ``state='running'`` in ``user_machines``.

Run with:

    python -m claw_soul.router.migrate_user --user-id <uuid> [--tier paid]

Requires the standard router env vars (SUPABASE, FLY_*, ROUTER_PUBLIC_URL).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .. import fly_client
from . import db, telegram_api

logger = logging.getLogger(__name__)


async def migrate(user_id: str, *, tier: str = "free", region: str | None = None,
                  dry_run: bool = False, set_webhook: bool = True) -> int:
    # 1. Settings present?
    settings = await db.get_user_setting_row(user_id)
    if not settings:
        print(f"ERROR: no user_settings row for {user_id}", file=sys.stderr)
        return 1
    token = settings.get("telegram_bot_token")
    print(f"  · user_settings ✓  token={'<set>' if token else '<absent>'}")

    # 2. Existing machine?
    existing = await db.get_user_machine(user_id)
    if existing is not None:
        print(f"  · already provisioned: machine={existing.machine_id} "
              f"state={existing.state} tier={existing.tier}")
    else:
        if dry_run:
            print(f"  · DRY-RUN: would provision new worker machine "
                  f"(tier={tier}, region={region or fly_client._default_region()})")  # noqa: SLF001
        else:
            spec = fly_client.MachineSpec(
                user_id=user_id, tier=tier,
                region=region or fly_client._default_region(),  # noqa: SLF001
            )
            machine = fly_client.create_user_machine(spec)
            await db.upsert_user_machine(
                user_id,
                machine_id=machine["id"],
                region=machine.get("region", spec.region),
                state="starting",
                tier=tier,
                image_ref=spec.image or fly_client._worker_image(),  # noqa: SLF001
            )
            existing = await db.get_user_machine(user_id)
            print(f"  · provisioned new machine={machine['id']} "
                  f"region={machine.get('region')}")

    # 3. Data copy plan
    if existing is not None:
        print("  · data copy: see the printed commands below — run them in")
        print("    a shell with `fly` logged in to BOTH the source and target apps.")
        print(_data_copy_plan(user_id, existing.machine_id))

    # 4. Webhook
    if not token:
        print("  · webhook: SKIPPED (no telegram_bot_token configured)")
    elif not set_webhook:
        print("  · webhook: SKIPPED (--no-webhook) — flip it manually once /data is copied")
    elif dry_run:
        print(f"  · DRY-RUN: would call setWebhook for token={token[:8]}…")
    else:
        ok, info = await telegram_api.set_webhook(token)
        if ok:
            print(f"  · webhook ✓  url={info}")
            await db.upsert_user_machine(user_id, webhook_url=info)
        else:
            print(f"  · webhook FAILED: {info}")

    # 5. Mark running once everything is set up (operator confirms data copy).
    if not dry_run and existing is not None:
        await db.upsert_user_machine(user_id, state="running")
        print("  · user_machines.state = 'running'")

    print()
    print("Done.  Reminder: actually copy the /data files before sending a TG message,")
    print("otherwise the worker will boot with an empty home and re-onboard from scratch.")
    return 0


def _data_copy_plan(user_id: str, machine_id: str) -> str:
    src = f"/data/users/{user_id}"
    dst = "/data"  # worker machine has the per-user dir as ITS root /data
    return f"""
    ┌─ Operator: run these from the project root once Fly machines are up ───────
    │
    │ # 1. Snapshot legacy data to local tmp
    │ fly ssh sftp shell -a clawsoul -C 'cd {src} && tar czf /tmp/user.tar.gz .'
    │ fly ssh sftp get  -a clawsoul /tmp/user.tar.gz /tmp/user.tar.gz
    │
    │ # 2. Push into the new worker
    │ fly ssh sftp put -a clawsoul-worker --machine {machine_id} \\
    │     /tmp/user.tar.gz /tmp/user.tar.gz
    │ fly ssh console -a clawsoul-worker --machine {machine_id} \\
    │     -C 'mkdir -p {dst} && tar xzf /tmp/user.tar.gz -C {dst}'
    │
    │ # 3. Verify
    │ fly ssh console -a clawsoul-worker --machine {machine_id} -C 'ls {dst}/context'
    │
    └──────────────────────────────────────────────────────────────────────────────
    """


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--tier", default="free", choices=("free", "paid", "enterprise"))
    parser.add_argument("--region", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-webhook", action="store_true",
                        help="Provision the machine but don't flip the Telegram "
                             "webhook (use this to do the /data copy first, then "
                             "re-run without --no-webhook to cut traffic over).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(migrate(
        args.user_id, tier=args.tier, region=args.region, dry_run=args.dry_run,
        set_webhook=not args.no_webhook,
    ))


if __name__ == "__main__":
    sys.exit(main())
