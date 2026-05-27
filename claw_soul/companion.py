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
    _COUNTRIES,
    _LANGUAGES,
    _OCCUPATIONS,
    _PROACTIVITIES,
    random_region,
    _STRESSES,
    _TONES,
    _TRAITS_PRIMARY,
    _TRAITS_EXPRESSIVE,
    _TRAITS_VALUES,
    _TRAITS_ADDITIONAL,
    _MIN_TRAITS,
    _MAX_TRAITS,
    country_to_culture,
    _generate_persona_file,
    _generate_profile_file,
    _generate_soul_file,
    _update_proactive_config,
)


# ── Option metadata (serializable) ─────────────────────────────────────────

def _opt(items: list[tuple[str, str, str]]) -> list[dict]:
    """Convert ``[(key, label, description), …]`` to JSON-friendly dicts."""
    return [{"key": k, "label": label, "description": desc} for k, label, desc in items]


def _opt2(items: list[tuple[str, str]]) -> list[dict]:
    """For (key, label) pairs without a description (e.g. traits)."""
    return [{"key": k, "label": label} for k, label in items]


OPTIONS: dict[str, list[dict]] = {
    "userGender":       _opt(_GENDERS),
    "userAge":          _opt(_AGE_RANGES),
    "userCountry":      _opt(_COUNTRIES),
    "userLanguage":     _opt(_LANGUAGES),
    "companionGender":     _opt(_COMPANION_GENDERS),
    "companionAge":        _opt(_AGE_RANGES),
    "companionOccupation": _opt(_OCCUPATIONS),
    "companionCountry":    _opt(_COUNTRIES),
    "archetype":        _opt(_ARCHETYPES),
    "dynamic":          _opt(_DYNAMICS),
    "tone":             _opt(_TONES),
    "proactivity":      _opt(_PROACTIVITIES),
    "stress":           _opt(_STRESSES),
    "deepTalk":         _opt(_DEEP_TALKS),
}

# Multi-select traits: rendered as four expandable groups in the UI.
TRAIT_GROUPS: dict[str, list[dict]] = {
    "Primary":               _opt2(_TRAITS_PRIMARY),
    "Expressive":            _opt2(_TRAITS_EXPRESSIVE),
    "Temperament & Values":  _opt2(_TRAITS_VALUES),
    "Additional":            _opt2(_TRAITS_ADDITIONAL),
}
TRAITS_MIN = _MIN_TRAITS
TRAITS_MAX = _MAX_TRAITS

# Fields that are validated against OPTIONS, plus the free-text ones.
_CHOICE_FIELDS = set(OPTIONS.keys())
_TEXT_FIELDS = {"userName", "companionName", "companionRegion"}
ALL_FIELDS = _CHOICE_FIELDS | _TEXT_FIELDS | {"traits", "backstory"}

# Fields that may be missing on legacy configs — fall back to a sensible
# default rather than 400-ing existing users when they re-open the wizard.
_OPTIONAL_DEFAULTS: dict[str, str] = {
    "userCountry": "OTHER",
    "userLanguage": "en",
    "companionOccupation": "freelancer",
    "companionCountry": "OTHER",
}


# ── Validation ──────────────────────────────────────────────────────────────

class ChoiceError(ValueError):
    """Raised when the submitted wizard choices fail validation."""


_TEXT_DEFAULTS_BY_LANG: dict[str, dict[str, str]] = {
    "zh-CN": {"userName": "主人",  "companionName": "小爪"},
    "zh-TW": {"userName": "主人",  "companionName": "小爪"},
    "ja":    {"userName": "あなた", "companionName": "Claw"},
    "ko":    {"userName": "당신",   "companionName": "Claw"},
    "en":    {"userName": "You",   "companionName": "Claw"},
    "es":    {"userName": "Tú",    "companionName": "Claw"},
    "fr":    {"userName": "Toi",   "companionName": "Claw"},
    "de":    {"userName": "Du",    "companionName": "Claw"},
}


def validate(choices: dict[str, Any]) -> dict[str, Any]:
    """Return a cleaned + validated dict, or raise ``ChoiceError``."""
    cleaned: dict[str, Any] = {}

    # Resolve the language first so text-field defaults pick the right script.
    lang = choices.get("userLanguage") or "en"
    defaults = _TEXT_DEFAULTS_BY_LANG.get(lang) or _TEXT_DEFAULTS_BY_LANG["en"]

    # Free-text fields with locale-aware defaults (name fields).  Region is
    # a regular text field but defaults are computed later from companionCountry.
    for name in ("userName", "companionName"):
        val = (choices.get(name) or "").strip()
        cleaned[name] = val[:60] if val else defaults.get(name, "")

    # Choice fields must match one of the OPTIONS keys
    for name in _CHOICE_FIELDS:
        val = choices.get(name)
        valid_keys = {o["key"] for o in OPTIONS[name]}
        if val not in valid_keys:
            # Optional fields fall back to a default instead of 400-ing
            # legacy configs that predate them.
            if name in _OPTIONAL_DEFAULTS:
                cleaned[name] = _OPTIONAL_DEFAULTS[name]
                continue
            raise ChoiceError(
                f"{name!r} must be one of {sorted(valid_keys)}, got {val!r}"
            )
        cleaned[name] = val

    # Key traits — multi-select (3-7 picks). Tolerates custom strings that
    # didn't come from the canonical groups (the CLI supports "+custom"); we
    # just lower-case + clip them for display safety.
    raw_traits = choices.get("traits") or []
    if not isinstance(raw_traits, list):
        raw_traits = []
    seen: set[str] = set()
    traits: list[str] = []
    for t in raw_traits:
        if not isinstance(t, str):
            continue
        key = t.strip().lower().replace(" ", "_")[:48]
        if key and key not in seen:
            seen.add(key)
            traits.append(key)
        if len(traits) >= TRAITS_MAX:
            break
    if traits and len(traits) < TRAITS_MIN:
        # Caller passed some but too few — treat as user error to keep
        # the wizard's "pick 3-7" rule honest.
        raise ChoiceError(
            f"'traits' must include at least {TRAITS_MIN} entries, got {len(traits)}."
        )
    cleaned["traits"] = traits

    # Backstory — free text, optional, capped at 1000 chars.
    backstory = (choices.get("backstory") or "").strip()
    cleaned["backstory"] = backstory[:1000]

    # Companion region — free text, optional.  If blank, pick a default city
    # from the chosen country.  Stored on the cleaned dict so the persona/
    # profile generators don't have to re-randomize each run.
    region = (choices.get("companionRegion") or "").strip()[:80]
    if not region:
        region = random_region(cleaned.get("companionCountry", "OTHER"))
    cleaned["companionRegion"] = region

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

    # `agent.culture` powers the horoscope skill (cn 黄历 / en zodiac / jp 占い
    # / in rashifal). It belongs to the *agent's* worldview, so prefer the
    # companion's country and fall back to the user's only when the companion
    # hasn't been placed.
    culture_source = cleaned.get("companionCountry") or cleaned.get("userCountry") or ""
    cfg.setdefault("agent", {})["culture"] = country_to_culture(culture_source)
    cfg["agent"]["language"] = cleaned.get("userLanguage") or "en"

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
