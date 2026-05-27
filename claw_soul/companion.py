"""
Companion-personality setup data + helpers.

This is the web-facing version of the CLI wizard that lives in
``claw_soul/onboard.py``. Same option set, same file generators — just
without the ``input()`` / coloured-print plumbing.

The dashboard's persona wizard (POST /api/setup/companion) calls
``apply_choices`` to write the user's selections to the config JSON and
regenerate SOUL.md / PERSONA.md / PROFILE.md under the active tenant's
``/data/users/<uid>/context/`` directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .onboard import (
    _ARCHETYPES,
    _DEEP_TALKS,
    _DYNAMICS,
    _GENDERS,
    _COMPANION_GENDERS,
    _AGE_RANGES,
    _PROACTIVITIES,
    _STRESSES,
    _TONES,
    _generate_persona_file,
    _generate_profile_file,
    _generate_soul_file,
    _update_proactive_config,
)


# ── Option metadata (serializable) ─────────────────────────────────────────

def _opt(items: list[tuple[str, str, str]]) -> list[dict]:
    """Convert ``[(key, label, description), …]`` to JSON-friendly dicts."""
    return [{"key": k, "label": label, "description": desc} for k, label, desc in items]


OPTIONS: dict[str, list[dict]] = {
    "userGender":       _opt(_GENDERS),
    "userAge":          _opt(_AGE_RANGES),
    "companionGender":  _opt(_COMPANION_GENDERS),
    "companionAge":     _opt(_AGE_RANGES),
    "archetype":        _opt(_ARCHETYPES),
    "dynamic":          _opt(_DYNAMICS),
    "tone":             _opt(_TONES),
    "proactivity":      _opt(_PROACTIVITIES),
    "stress":           _opt(_STRESSES),
    "deepTalk":         _opt(_DEEP_TALKS),
}

# Fields that are validated against OPTIONS, plus the free-text ones.
_CHOICE_FIELDS = set(OPTIONS.keys())
_TEXT_FIELDS = {"userName", "companionName"}
ALL_FIELDS = _CHOICE_FIELDS | _TEXT_FIELDS


# ── Validation ──────────────────────────────────────────────────────────────

class ChoiceError(ValueError):
    """Raised when the submitted wizard choices fail validation."""


def validate(choices: dict[str, Any]) -> dict[str, Any]:
    """Return a cleaned + validated dict, or raise ``ChoiceError``."""
    cleaned: dict[str, Any] = {}

    # Free-text fields with sane defaults
    for name in _TEXT_FIELDS:
        val = (choices.get(name) or "").strip()
        cleaned[name] = val[:60] if val else ("主人" if name == "userName" else "小爪")

    # Choice fields must match one of the OPTIONS keys
    for name in _CHOICE_FIELDS:
        val = choices.get(name)
        valid_keys = {o["key"] for o in OPTIONS[name]}
        if val not in valid_keys:
            raise ChoiceError(
                f"{name!r} must be one of {sorted(valid_keys)}, got {val!r}"
            )
        cleaned[name] = val

    return cleaned


# ── Read / write current user's companion config ────────────────────────────

def load_choices() -> dict | None:
    """Return the current tenant's saved companion choices, or None."""
    cfg = config.load()
    return cfg.get("companion") if cfg else None


def apply_choices(choices: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist, and regenerate SOUL/PERSONA/PROFILE files.

    Writes to the active tenant's storage (resolved via the tenancy
    contextvar — see ``claw_soul/core/tenancy.py``).
    """
    cleaned = validate(choices)

    # Update + save config JSON
    cfg = config.load()
    cfg["companion"] = cleaned
    _update_proactive_config(cfg, cleaned["proactivity"])
    _persist_config(cfg)

    # Regenerate the three identity files
    context_dir = str(config.CLAWSOUL_HOME / "context")
    Path(context_dir).mkdir(parents=True, exist_ok=True)
    _generate_soul_file(cleaned, context_dir)
    _generate_persona_file(cleaned, context_dir)
    _generate_profile_file(cleaned, context_dir)

    return cleaned


def _persist_config(cfg: dict) -> None:
    """Write the config dict back to disk under the current tenant."""
    path = config.config_path() or (config.CLAWSOUL_HOME / "claw_soul.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    # Bust the per-tenant cache so subsequent reads see the new value
    key = config._tenant_key()
    config._configs[key] = cfg
    config._config_paths[key] = path
