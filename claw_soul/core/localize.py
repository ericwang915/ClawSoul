"""
Identity-document localization for non-CN/EN personas.

The soul/persona/profile generators only have Chinese and English template
branches, so a Japanese/Korean/Spanish/French/German companion used to get an
English-scaffold identity — the Language Lock forced her replies into the
right language, but her inner documents (voice examples, style rules,
backstory) stayed English, which dilutes persona fidelity in every prompt.

This module translates the three generated identity files into the configured
language ONCE per (identity, language) — marker-cached so the per-boot
``apply_choices`` re-run is a no-op — using the same LLM provider the agent
already uses. Runs in a background thread (fire-and-forget): the English
files work immediately (status quo ante), and the localized versions take
over on the next agent rebuild. Any failure leaves the English files in
place — never worse than before.

Appearance.md is deliberately NOT localized: image models prompt best in
EN/zh, and selfie.py branches on that.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

# Fields that define the companion's identity. A change here means the
# generated soul/persona/profile docs change, so the localization marker must
# too — that's the only thing this signature gates.
_IDENTITY_FIELDS = (
    "companionName", "companionGender", "companionAge", "companionCountry",
    "companionRegion", "companionOccupation", "archetype", "dynamic",
    "tone", "backstory", "userLanguage",
)


def identity_signature(choices: dict) -> str:
    """Stable hash of the identity fields, used to cache localized docs."""
    payload = {k: str(choices.get(k) or "").strip().lower() for k in _IDENTITY_FIELDS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

_LANG_NAMES = {
    "ja": "Japanese (日本語)", "ko": "Korean (한국어)",
    "es": "Spanish (Español)", "fr": "French (Français)",
    "de": "German (Deutsch)",
}

# Files to localize, relative to the context dir.
_TARGETS = ("soul/SOUL.md", "persona/persona.md", "profile/PROFILE.md")


def _marker_path(context_dir: str) -> str:
    return os.path.join(context_dir, ".localized_sig")


def needs_localization(lang_code: str) -> bool:
    code = (lang_code or "en").lower()
    return code.split("-")[0] in _LANG_NAMES


def is_current(context_dir: str, lang_code: str, identity_sig: str) -> bool:
    """True when the identity files on disk are ALREADY localized for this
    exact (identity, language) — callers must then skip regenerating the
    English templates, or they'd overwrite the localized files every boot."""
    if not needs_localization(lang_code):
        return False
    want = f"{identity_sig}:{(lang_code or '').lower()}"
    try:
        with open(_marker_path(context_dir), "r", encoding="utf-8") as f:
            return f.read().strip() == want
    except OSError:
        return False


def localize_identity_files_async(context_dir: str, lang_code: str,
                                  identity_sig: str) -> None:
    """Fire-and-forget localization; marker-cached."""
    if not needs_localization(lang_code):
        return
    want = f"{identity_sig}:{(lang_code or '').lower()}"
    try:
        with open(_marker_path(context_dir), "r", encoding="utf-8") as f:
            if f.read().strip() == want:
                return  # this identity already localized into this language
    except OSError:
        pass

    def _run() -> None:
        try:
            _localize(context_dir, lang_code, want)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[localize] identity localization failed: %s", exc)

    threading.Thread(target=_run, name="identity-localize", daemon=True).start()


def _localize(context_dir: str, lang_code: str, marker_value: str) -> None:
    lang_name = _LANG_NAMES[(lang_code or "en").lower().split("-")[0]]
    from ..main import _build_provider
    provider = _build_provider()

    done = 0
    for rel in _TARGETS:
        path = os.path.join(context_dir, rel)
        try:
            with open(path, "r", encoding="utf-8") as f:
                original = f.read()
        except OSError:
            continue
        if not original.strip():
            continue
        prompt = (
            f"Translate this AI-companion identity document from English into "
            f"{lang_name}.\n"
            "- Keep ALL markdown structure and heading levels exactly.\n"
            "- Keep people's names and place names as-is.\n"
            "- Quoted example phrases must sound NATIVE in the target language "
            "(adapt them, don't translate word-for-word).\n"
            "- Preserve emoji. Do not add, remove, or reorder sections.\n"
            "- Output ONLY the translated document, no preamble.\n\n"
            f"--- DOCUMENT ---\n{original}"
        )
        try:
            r = provider.chat(messages=[{"role": "user", "content": prompt}],
                              tools=[], temperature=0.3,
                              max_tokens=4000, timeout=120)
            body = (r.choices[0].message.content or "").strip()
            # Sanity: refuse a suspiciously short result (truncated/refused).
            if len(body) < len(original) * 0.4:
                logger.warning("[localize] %s: output too short, keeping EN", rel)
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(body + "\n")
            done += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[localize] %s failed: %s", rel, exc)

    if done == len(_TARGETS):
        try:
            with open(_marker_path(context_dir), "w", encoding="utf-8") as f:
                f.write(marker_value)
        except OSError:
            pass
        logger.info("[localize] identity localized into %s (%d files)",
                    lang_name, done)
    else:
        # Partial → no marker, so the next apply_choices retries the rest.
        logger.info("[localize] partial localization (%d/%d) — will retry",
                    done, len(_TARGETS))
