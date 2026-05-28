"""
Persona renderer — produce the character description block for selfie prompts.

Sole source: ``<CLAWSOUL_HOME>/context/persona/appearance.md``, written by
the wizard (``companion.apply_choices`` →
``_generate_appearance_file``).  If the file is missing or empty we
**raise**, because a silent fallback to a hardcoded default has bitten
us multiple times — wrong tenancy binding, missing apply_choices on
boot, etc. — and produced an East-Asian selfie for an American persona
with no visible error.  Failing loud means the caller (selfie / candid
tool) returns an explicit error string to the LLM, which then has to
tell the user honestly instead of shipping a wrong-looking photo.
"""

from __future__ import annotations

import logging
import os

from ... import config

logger = logging.getLogger(__name__)


class AppearanceNotConfigured(RuntimeError):
    """No appearance.md exists for the current tenant.

    Surfaces back through ``take_selfie`` / ``candid_shot`` so the LLM
    sees an actionable error message instead of generating a photo with
    the wrong ethnicity.
    """


def _appearance_path() -> str:
    return os.path.join(
        str(config.CLAWSOUL_HOME),
        "context", "persona", "appearance.md",
    )


def load_appearance() -> str:
    """Return the character's visual description, or raise.

    Never returns a silent default — every selfie / candid generation
    has to either succeed with the user-configured look or fail loudly.
    """
    path = _appearance_path()
    if not os.path.isfile(path):
        raise AppearanceNotConfigured(
            f"appearance.md not found at {path} — make sure the companion "
            "wizard ran and per-tenant CLAWSOUL_HOME is correctly bound."
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except OSError as exc:
        raise AppearanceNotConfigured(
            f"appearance.md exists but is unreadable: {exc}"
        ) from exc
    # Strip markdown headers
    lines = [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    ]
    body = "\n".join(lines).strip()
    if not body:
        raise AppearanceNotConfigured(
            f"appearance.md at {path} is empty after stripping headers."
        )
    return body
