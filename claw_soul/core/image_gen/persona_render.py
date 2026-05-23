"""
Persona renderer — produce the character description block for selfie prompts.

Source priority:
  1. ``context/persona/appearance.md`` (user-curated visual description)
  2. Fallback default (a generic 20-something East Asian girl description)

We intentionally do *not* call an LLM to extract appearance from the persona
file at every selfie generation — that would be slow and expensive. Users
who want a custom look should edit ``appearance.md``.
"""

from __future__ import annotations

import logging
import os

from ... import config

logger = logging.getLogger(__name__)


DEFAULT_APPEARANCE = """\
一位 20 岁出头的东亚女孩，长发，齐刘海，大眼睛，皮肤白皙，身材娇小。
风格清新自然，气质温柔可爱。\
"""


def _appearance_path() -> str:
    return os.path.join(
        str(config.CLAWSOUL_HOME),
        "context", "persona", "appearance.md",
    )


def load_appearance() -> str:
    """Return the character's visual description (Chinese, suitable for prompt)."""
    path = _appearance_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                # Strip markdown headers to keep the prompt clean
                lines = [
                    line for line in text.splitlines()
                    if not line.lstrip().startswith("#")
                ]
                return "\n".join(lines).strip() or DEFAULT_APPEARANCE
        except OSError as exc:
            logger.warning("[persona_render] failed to read appearance.md: %s", exc)
    return DEFAULT_APPEARANCE
