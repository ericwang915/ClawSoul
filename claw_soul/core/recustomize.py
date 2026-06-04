"""
Re-customization reset.

When a user changes WHO their companion is — name, gender, age, location,
personality or chat language — she effectively becomes a different person in a
different place speaking a different language.  Keeping the old synthesized
memory and chat transcript then produces a companion who *behaves* like the new
identity but *remembers* the old one (lives in Austin, speaks Chinese, ate tacos
on I-35), which reads as broken.

So on a material identity change we wipe everything she has *accumulated*
(long-term memory, chat transcript, today's plan, relationship/milestone state)
but KEEP the photos the user has collected — those are keepsakes, not memory.

Detection is signature-based: we hash the material identity fields and stash the
hash in ``context/.identity_sig``.  On each persona hydrate we compare the stored
signature to the freshly-loaded choices; a mismatch (and only when a prior
signature existed — so first-ever onboarding never wipes) triggers the reset.

The reset runs on the WORKER, where the user's ``CLAWSOUL_HOME`` filesystem
actually lives, just before ``apply_choices`` regenerates the identity files.

Everything is best-effort: a failure here must never block persona hydration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil

from .. import config

logger = logging.getLogger(__name__)

# Fields that define identity / place / language.  Changing any of these makes
# her a materially different companion → reset.  Deliberately EXCLUDED:
# proactivity, quiet-hours, quota, telegram token, the user's own name, the
# user's timezone — tweaking those shouldn't make her forget the relationship.
_MATERIAL_FIELDS = (
    "companionName",
    "companionGender",
    "companionAge",
    "companionCountry",
    "companionRegion",
    "companionOccupation",
    "archetype",
    "dynamic",
    "tone",
    "backstory",
    "userLanguage",
)


def _home() -> str:
    return str(config.CLAWSOUL_HOME)


def _sig_path() -> str:
    return os.path.join(_home(), "context", ".identity_sig")


def _norm(v) -> str:
    if isinstance(v, (list, tuple)):
        return ",".join(sorted(_norm(x) for x in v))
    return str(v if v is not None else "").strip().lower()


def identity_signature(choices: dict) -> str:
    """Stable hash over the material identity fields of a choices blob."""
    payload = {k: _norm(choices.get(k)) for k in _MATERIAL_FIELDS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_local_sig() -> str | None:
    try:
        with open(_sig_path(), "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _read_stored_sig(user_id: str) -> str | None:
    """Last-applied signature. Local file is primary (fast, no network); the Pg
    copy is a durable fallback for when the worker machine was destroyed and
    /data (the local file) was wiped — without it, a fresh machine couldn't
    tell that the identity changed and would skip the reset."""
    local = _read_local_sig()
    if local is not None:
        return local
    try:
        from .. import companion
        return companion.load_applied_sig(user_id)
    except Exception:
        return None


def _write_sig(user_id: str, sig: str) -> None:
    # Local file (primary) + Pg (durable fallback).
    try:
        path = _sig_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(sig)
    except OSError as exc:
        logger.warning("[recustomize] could not persist identity sig locally: %s", exc)
    try:
        from .. import companion
        companion.save_applied_sig(user_id, sig)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[recustomize] could not persist identity sig to Pg: %s", exc)


def _wipe_filesystem_memory() -> None:
    """Delete synthesized memory + stale plan under CLAWSOUL_HOME; keep photos
    and the (about-to-be-regenerated) identity docs."""
    home = _home()
    # Whole subtrees that hold ONLY accumulated memory / chat state.
    for rel in ("context/groups", "context/compaction"):
        path = os.path.join(home, rel)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    # Today's plan is location-bound; drop it so it regenerates for the new city.
    plan = os.path.join(home, "context", "calendar", "today_plan.md")
    try:
        os.remove(plan)
    except OSError:
        pass


def reset_memory(user_id: str) -> None:
    """Wipe accumulated memory across all three stores; KEEP photos.

    Filesystem (worker /data): context/groups + context/compaction + today_plan.
    Postgres: turns, sessions, memory_entries, memory_daily, milestone events.
    Tigris: the memory backup tarball (NOT the users/<uid>/ photo objects).
    """
    _wipe_filesystem_memory()

    try:
        from . import storage_pg
        res = storage_pg.purge_user_memory(user_id)
        logger.info("[recustomize] Pg purge for %s: %s", user_id[:8], res)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[recustomize] Pg purge failed for %s: %s", user_id[:8], exc)

    try:
        from . import memory_backup
        memory_backup.purge(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[recustomize] Tigris memory purge failed for %s: %s",
                       user_id[:8], exc)

    logger.info("[recustomize] memory reset complete for %s (photos kept)", user_id[:8])


def maybe_reset_on_identity_change(user_id: str, choices: dict) -> bool:
    """Compare the new choices' identity signature to the stored one; reset
    memory on a mismatch.  Returns True iff a reset was performed.

    First-ever hydrate (no stored signature) NEVER resets — it just records the
    signature, so shipping this feature to an existing user is a no-op until they
    actually change something material.
    """
    new_sig = identity_signature(choices or {})
    old_sig = _read_stored_sig(user_id)
    changed = old_sig is not None and old_sig != new_sig
    if changed:
        logger.info("[recustomize] identity changed for %s — resetting memory", user_id[:8])
        reset_memory(user_id)
    # Record regardless, so the signature tracks the latest applied identity.
    _write_sig(user_id, new_sig)
    return changed
