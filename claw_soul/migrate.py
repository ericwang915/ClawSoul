"""
One-shot migration from single-tenant to multi-tenant layout.

Before::
    /data/
        claw_soul.json
        context/
        daemon.log

After::
    /data/
        users/
            <user_id>/
                claw_soul.json
                context/
                daemon.log

Usage (from inside the container, e.g. ``fly ssh console``)::

    python -m claw_soul.migrate <user_id>

Safe to run multiple times — re-running with the same user_id is a no-op
once migration has completed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import config


# Files/dirs at the data root that belong to a single user's old layout.
# (anything not in this list is left untouched, including /data/users/.)
LEGACY_ENTRIES = [
    "claw_soul.json",
    "claw_soul.pid",
    "daemon.log",
    "daemon.meta.json",
    "context",
]


def migrate(user_id: str, *, base: Path | None = None, dry_run: bool = False) -> dict:
    """Move legacy single-tenant data under ``users/<user_id>/``.

    Returns a summary dict of what was moved / skipped.
    """
    base = base or config._CLAWSOUL_BASE
    base = Path(base)
    target = base / "users" / user_id

    summary = {
        "base": str(base),
        "target": str(target),
        "moved": [],
        "skipped_already_exists": [],
        "skipped_missing": [],
        "dry_run": dry_run,
    }

    if not base.exists():
        raise SystemExit(f"Base directory does not exist: {base}")

    target.mkdir(parents=True, exist_ok=True)

    for name in LEGACY_ENTRIES:
        src = base / name
        dst = target / name

        if not src.exists():
            summary["skipped_missing"].append(name)
            continue

        if dst.exists():
            # Already migrated. Leave both alone to avoid clobbering.
            summary["skipped_already_exists"].append(name)
            continue

        if dry_run:
            summary["moved"].append(name + " (dry run)")
            continue

        shutil.move(str(src), str(dst))
        summary["moved"].append(name)

    return summary


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    dry_run = "--dry-run" in argv
    positional = [a for a in argv if not a.startswith("--")]

    if not positional:
        print("Error: provide a user_id (Supabase auth.users.id, the JWT 'sub' claim)")
        return 2

    user_id = positional[0]
    result = migrate(user_id, dry_run=dry_run)

    print(f"Base:   {result['base']}")
    print(f"Target: {result['target']}")
    print(f"Moved ({len(result['moved'])}):")
    for n in result["moved"]:
        print(f"  ✔ {n}")
    if result["skipped_already_exists"]:
        print(f"Already in place ({len(result['skipped_already_exists'])}):")
        for n in result["skipped_already_exists"]:
            print(f"  · {n}")
    if result["skipped_missing"]:
        print(f"Not present at source ({len(result['skipped_missing'])}):")
        for n in result["skipped_missing"]:
            print(f"  - {n}")

    if dry_run:
        print("\n(dry run — re-run without --dry-run to apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
