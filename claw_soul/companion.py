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
import os
from pathlib import Path
from typing import Any

from . import config
from .onboard import (
    _AGE_RANGES,
    _ARCHETYPES,
    _COMPANION_GENDERS,
    _COUNTRIES,
    _DEEP_TALKS,
    _DYNAMICS,
    _GENDERS,
    _LANGUAGES,
    _MAX_TRAITS,
    _MIN_TRAITS,
    _OCCUPATIONS,
    _PROACTIVITIES,
    _STRESSES,
    _TONES,
    _TRAITS_ADDITIONAL,
    _TRAITS_EXPRESSIVE,
    _TRAITS_PRIMARY,
    _TRAITS_VALUES,
    _generate_persona_file,
    _generate_profile_file,
    _generate_soul_file,
    _update_proactive_config,
    companion_to_timezone,
    country_to_culture,
    random_region,
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
#
# Source of truth: Postgres (`public.user_companion`, migration 007), so
# the web dashboard and the per-user worker — which live in different
# Fly containers with different filesystems — share state.  We still
# materialize the choices to local files (claw_soul.json + the three
# identity .md files) because the Agent / persona pipeline reads from
# disk; Postgres is the canonical store, local files are a cache.


def load_choices() -> dict | None:
    """Return the current tenant's saved companion choices, or None.

    Priority: Postgres → local config JSON.  Local cache is kept so
    single-tenant / dev installs without Supabase configured still work.
    """
    pg = _load_choices_pg()
    if pg is not None:
        return pg
    cfg = config.load()
    return cfg.get("companion") if cfg else None


def apply_choices(choices: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist (Pg + local), and regenerate identity files.

    Order:
      1. Validate.
      2. Write to Postgres (the canonical store the worker reads).
      3. Materialize claw_soul.json + SOUL.md / PERSONA.md / PROFILE.md
         on the local filesystem — the Agent's persona loader reads
         from there, and we want web-side preview features to stay
         instant.
      4. Flip user_machines.onboarded=true so the router scheduler
         starts firing proactive/selfie/planner ticks (best-effort).
    """
    cleaned = validate(choices)

    # 1. Postgres (the canonical store)
    _save_choices_pg(cleaned)

    # 2. Local cache (claw_soul.json) — keeps the Agent's existing
    #    config-file reads working on whichever container saved this.
    cfg = config.load()
    cfg["companion"] = cleaned
    _update_proactive_config(cfg, cleaned["proactivity"])
    culture_source = cleaned.get("companionCountry") or cleaned.get("userCountry") or ""
    cfg.setdefault("agent", {})["culture"] = country_to_culture(culture_source)
    cfg["agent"]["language"] = cleaned.get("userLanguage") or "en"

    # Companion's home timezone — drives agent's "current local time" in the
    # system prompt.  Without this the agent inherits the user's tz or UTC,
    # which gives "it's 8pm here in Austin" while it's actually 6am in Austin.
    cfg.setdefault("persona", {})["timezone"] = companion_to_timezone(
        cleaned.get("companionCountry", ""),
        cleaned.get("companionRegion") or "",
    )

    _persist_config(cfg)

    # 3. Identity files.  In SaaS mode the dashboard host (legacy
    # `clawsoul` app) doesn't actually serve Telegram traffic anymore —
    # workers do, and they materialize their own copy from Pg via
    # _hydrate_persona_from_pg on boot.  Writing identity files on the
    # dashboard host just leaves dead state on the legacy volume.
    # Single-tenant / dev installs still need the local copy.
    if not _in_saas_dashboard_mode():
        context_dir = str(config.CLAWSOUL_HOME / "context")
        Path(context_dir).mkdir(parents=True, exist_ok=True)
        _generate_soul_file(cleaned, context_dir)
        _generate_persona_file(cleaned, context_dir)
        _generate_profile_file(cleaned, context_dir)

    # 4. Best-effort: flip onboarded on the user's user_machines row so
    #    the scheduler picks them up at the next reconcile.  Failure
    #    here doesn't roll back — the data is already saved.
    _flip_onboarded_pg()

    return cleaned


# ── Postgres-backed companion store ────────────────────────────────────────

def _pg_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _pg_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _pg_configured() -> bool:
    return bool(_pg_url() and _pg_key())


def _pg_headers(prefer: str = "return=representation") -> dict[str, str]:
    return {
        "apikey": _pg_key(),
        "Authorization": f"Bearer {_pg_key()}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _current_user_id() -> str | None:
    from .core import tenancy
    return tenancy.get_current_user()


def _load_choices_pg() -> dict | None:
    if not _pg_configured():
        return None
    uid = _current_user_id()
    if not uid:
        return None
    try:
        import httpx
        r = httpx.get(
            f"{_pg_url()}/rest/v1/user_companion",
            params={"user_id": f"eq.{uid}", "select": "choices"},
            headers=_pg_headers(), timeout=10,
        )
        if not r.is_success:
            return None
        rows = r.json() or []
        return rows[0]["choices"] if rows else None
    except Exception:
        return None


def _save_choices_pg(cleaned: dict[str, Any]) -> bool:
    if not _pg_configured():
        return False
    uid = _current_user_id()
    if not uid:
        return False
    try:
        import httpx
        r = httpx.post(
            f"{_pg_url()}/rest/v1/user_companion",
            params={"on_conflict": "user_id"},
            headers=_pg_headers("resolution=merge-duplicates,return=minimal"),
            json={"user_id": uid, "choices": cleaned},
            timeout=10,
        )
        return r.is_success
    except Exception:
        return False


def _in_saas_dashboard_mode() -> bool:
    """True when we're running on the legacy dashboard host inside SaaS
    Phase 2 — i.e. Telegram traffic goes through the per-user worker
    machines, not this process.  Detected by ROUTER_PUBLIC_URL being
    set (dashboard's env points at the router for provisioning)."""
    return bool(os.environ.get("ROUTER_PUBLIC_URL", "").strip() and
                not os.environ.get("CLAW_USER_ID", "").strip())


def _flip_onboarded_pg() -> None:
    """Best-effort PATCH user_machines.onboarded=true once the wizard saves."""
    if not _pg_configured():
        return
    uid = _current_user_id()
    if not uid:
        return
    try:
        import httpx
        httpx.patch(
            f"{_pg_url()}/rest/v1/user_machines",
            params={"user_id": f"eq.{uid}"},
            headers=_pg_headers("return=minimal"),
            json={"onboarded": True},
            timeout=5,
        )
    except Exception:
        pass


def _persist_config(cfg: dict) -> None:
    """Write the config dict back to disk under the current tenant."""
    path = config.config_path() or (config.CLAWSOUL_HOME / "claw_soul.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    # Bust the per-tenant cache so subsequent reads see the new value
    key = config._tenant_key()
    config._configs[key] = cfg
    config._config_paths[key] = path
