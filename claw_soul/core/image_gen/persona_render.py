"""
Persona renderer — produce the character description block for selfie prompts.

Sole source: ``<CLAWSOUL_HOME>/context/persona/appearance.md``, written by
the wizard (``companion.apply_choices`` →
``_generate_appearance_file``).  If the file is missing or empty we
**raise**, because a silent fallback to a hardcoded default has bitten
us multiple times — missing apply_choices on
boot, etc. — and produced an East-Asian selfie for an American persona
with no visible error.  Failing loud means the caller (selfie / candid
tool) returns an explicit error string to the LLM, which then has to
tell the user honestly instead of shipping a wrong-looking photo.
"""

from __future__ import annotations

import logging
import os
import re

from ... import config

logger = logging.getLogger(__name__)


def _profile_dir() -> str:
    return os.path.join(str(config.CLAWSOUL_HOME), "context", "profile")


def load_profile() -> str:
    """Concatenated text of the persona's profile docs, or '' if absent.

    Unlike appearance, this is best-effort (returns '') — callers only use
    it for soft enrichment (the canonical pet), never for a hard requirement.
    """
    d = _profile_dir()
    try:
        if os.path.isdir(d):
            parts = []
            for n in sorted(os.listdir(d)):
                if n.lower().endswith(".md"):
                    with open(os.path.join(d, n), "r", encoding="utf-8") as f:
                        parts.append(f.read())
            return "\n".join(parts)
    except OSError:
        pass
    return ""


# The profile templates pin a specific pet ("养了一只叫'芝麻'的橘猫" /
# "Has a white ragdoll cat named Mochi").  We extract it so chat, proactive
# AND photos can all reference the SAME animal instead of inventing a random
# cat-or-dog of a random colour each time.
_PET_RE_ZH = re.compile(
    r"养了\s*([^。;；]*?(?:猫|狗|犬|兔|仓鼠|鸟|鹦鹉|龟|金毛|柴|柯基|哈士奇|泰迪|博美)[^。;；]*)"
)
_PET_RE_EN = re.compile(
    r"\bhas an?\s+([^.;\n]*?\b(?:cat|dog|kitten|puppy|kitty|rabbit|bunny|bird|hamster|"
    r"turtle|retriever|corgi|husky|shiba|poodle|labrador|terrier|beagle|samoyed)\b[^.;\n]*)",
    re.IGNORECASE,
)


def canonical_pet(profile_text: str | None = None) -> str:
    """The persona's one fixed pet as a short descriptor (e.g. 'white ragdoll
    cat named Mochi' / '一只叫Mochi的白色布偶猫'), or '' if they have none."""
    text = profile_text if profile_text is not None else load_profile()
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", text)
    m = _PET_RE_ZH.search(flat) or _PET_RE_EN.search(flat)
    if m:
        return m.group(1).strip().strip("\"'“”。，,.")
    return ""


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
