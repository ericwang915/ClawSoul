"""
Long-term memory backup → Tigris.

A user's chat *turns* live in Postgres (durable), but the **synthesized**
long-term memory — MEMORY.md, the daily memory logs, the timeline, the
emotional graph, and compaction summaries — lives only on the worker's local
``/data``.  Worker machines have no persistent volume, so that state survives an
idle-suspend (stop/start) but is **lost if the machine is destroyed** (migration,
crash, manual recreation) — and it can't be rebuilt from Pg.

This module checkpoints that state to Tigris (object ``memory/<uid>/state.tar.gz``)
so a fresh machine restores it on boot.  Memory is plain text (a few hundred KB
per user), so the cost is negligible and Tigris egress is free.

  • restore(uid)  — on boot, if local memory is absent, pull + extract.
  • backup(uid)   — on idle-exit (and any checkpoint), tar + upload.

Best-effort throughout: never raises into the worker's boot / shutdown paths.
"""

from __future__ import annotations

import io
import logging
import os
import tarfile

from .. import config
from .image_gen import tigris

logger = logging.getLogger(__name__)

# Synthesized state worth keeping, relative to CLAWSOUL_HOME. Persona/soul/
# profile docs are regenerated from Pg choices on boot, so they're excluded;
# today_plan is regenerated daily, so it's excluded too.
_DIRS = ["context/groups", "context/compaction"]


def _key(user_id: str) -> str:
    return f"memory/{user_id}/state.tar.gz"


def _home() -> str:
    return str(config.CLAWSOUL_HOME)


def _has_local_memory() -> bool:
    groups = os.path.join(_home(), "context", "groups")
    try:
        return os.path.isdir(groups) and any(os.scandir(groups))
    except OSError:
        return False


def backup(user_id: str) -> bool:
    """Tar the synthesized memory dirs and upload to Tigris. Returns success."""
    if not user_id or not tigris.is_configured():
        return False
    home = _home()
    buf = io.BytesIO()
    added = 0
    try:
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel in _DIRS:
                path = os.path.join(home, rel)
                if os.path.isdir(path):
                    tar.add(path, arcname=rel)
                    added += 1
        if added == 0:
            return False
        ok = tigris.put_bytes(_key(user_id), buf.getvalue(), "application/gzip")
        if ok:
            logger.info("[memory_backup] backed up %s (%d dirs, %d bytes)",
                        user_id[:8], added, buf.tell())
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.warning("[memory_backup] backup failed for %s: %s", user_id[:8], exc)
        return False


def restore(user_id: str) -> bool:
    """If local memory is absent (fresh machine), restore it from Tigris.

    No-op when local memory already exists — local is authoritative and newer
    than any backup, so we never overwrite it.
    """
    if not user_id or not tigris.is_configured():
        return False
    if _has_local_memory():
        return False
    data = tigris.get_bytes(_key(user_id))
    if not data:
        return False
    try:
        home = _home()
        os.makedirs(home, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            try:
                tar.extractall(home, filter="data")  # py3.12+ safe extraction
            except TypeError:
                tar.extractall(home)  # older Pythons
        logger.info("[memory_backup] restored memory for %s from Tigris", user_id[:8])
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[memory_backup] restore failed for %s: %s", user_id[:8], exc)
        return False
