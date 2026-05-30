"""
Cast — recurring people in the companion's world (mom, dad, a friend).

Lazily populated from chat: the first time the companion shares a photo
of someone, the agent passes a one-line ``appearance`` description; we
persist it (Postgres ``companion_cast``, keyed by a normalized slug) so
every later photo is the *same* person:

  - same face: a stable seed derived from the appearance, plus a
    self-bootstrapped reference image (the first good generation is
    promoted to the character's Tigris reference and reused thereafter).
  - same story: the appearance + notes are mirrored into long-term
    memory so the companion talks about them consistently too.

Single-subject only (one cast member per photo).  Group shots that also
include the companion's own face are a separate, harder problem (multi-
reference consistency) and live in a future module.

Postgres-only (SaaS).  Single-tenant dev without Supabase configured
raises a clear error rather than silently generating an inconsistent
stranger.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass

import httpx

from .. import tenancy
from .generator import DEFAULT_SIZE, SeedreamError, SeedreamGenerator
from .selfie import _looks_chinese  # shared CJK heuristic

logger = logging.getLogger(__name__)


_CAST_STYLE_ZH = (
    "写实手机随拍人像，自然光线，生活气息，像家人/朋友被随手拍下的日常照片。"
    "单人入镜。不要文字、不要水印、不要 NSFW、不要血腥。"
)
_CAST_STYLE_EN = (
    "Realistic candid phone portrait of this person, natural lighting, "
    "everyday vibe — like a snapshot a family member or friend took. "
    "Single subject in frame. No text, no watermark, no NSFW, no violence."
)


# ── Pg plumbing (sync; runs in the agent's executor thread) ─────────────


def _pg_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _pg_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _pg_configured() -> bool:
    return bool(_pg_url() and _pg_key())


def _headers(prefer: str = "return=representation") -> dict[str, str]:
    return {
        "apikey": _pg_key(),
        "Authorization": f"Bearer {_pg_key()}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _uid() -> str:
    uid = tenancy.get_current_user()
    if not uid:
        raise SeedreamError("cast photo requires a bound tenant (no user_id)")
    return uid


# ── Model ───────────────────────────────────────────────────────────────


@dataclass
class CastMember:
    slug: str
    name: str
    relation: str
    appearance: str
    seed: int
    reference_key: str | None = None
    notes: str = ""


@dataclass
class CastResult:
    path: str
    prompt: str
    member: CastMember
    model: str

    def caption(self) -> str:
        who = self.member.name or self.member.relation
        return who


# ── Helpers ───────────────────────────────────────────────────────────


def _slugify(s: str) -> str:
    """Normalize a name/relation into a stable key. Keeps CJK + alnum."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w]+", "", s)   # \w is unicode-aware → keeps CJK
    return s[:40] or "person"


def _stable_seed(appearance: str) -> int:
    digest = hashlib.sha256(appearance.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _fetch(uid: str, slug: str) -> CastMember | None:
    try:
        r = httpx.get(
            f"{_pg_url()}/rest/v1/companion_cast",
            params={"user_id": f"eq.{uid}", "slug": f"eq.{slug}",
                    "select": "*"},
            headers=_headers(), timeout=8,
        )
        if not r.is_success:
            logger.warning("[cast] fetch failed: %s", r.status_code)
            return None
        rows = r.json() or []
        if not rows:
            return None
        d = rows[0]
        return CastMember(
            slug=d["slug"], name=d.get("name") or "", relation=d["relation"],
            appearance=d["appearance"], seed=int(d["seed"]),
            reference_key=d.get("reference_key"), notes=d.get("notes") or "",
        )
    except Exception as exc:
        logger.warning("[cast] fetch errored: %s", exc)
        return None


def _insert(uid: str, m: CastMember) -> None:
    httpx.post(
        f"{_pg_url()}/rest/v1/companion_cast",
        params={"on_conflict": "user_id,slug"},
        json={
            "user_id": uid, "slug": m.slug, "name": m.name,
            "relation": m.relation, "appearance": m.appearance,
            "seed": m.seed, "notes": m.notes,
        },
        headers=_headers("resolution=ignore-duplicates,return=minimal"),
        timeout=8,
    )


def _set_reference_key(uid: str, slug: str, key: str) -> None:
    try:
        httpx.patch(
            f"{_pg_url()}/rest/v1/companion_cast",
            params={"user_id": f"eq.{uid}", "slug": f"eq.{slug}"},
            json={"reference_key": key},
            headers=_headers("return=minimal"), timeout=8,
        )
    except Exception as exc:
        logger.warning("[cast] set reference_key failed: %s", exc)


def _remember(member: CastMember) -> None:
    """Mirror the cast member into long-term memory so the companion stays
    consistent about them in conversation, not just in photos."""
    try:
        from ..storage_pg import MemoryManagerPg, use_postgres
        if not (use_postgres() and _pg_configured()):
            return
        who = member.name or member.relation
        MemoryManagerPg().remember(
            f"{who} ({member.relation}) — {member.appearance}"
            + (f" Notes: {member.notes}" if member.notes else ""),
            key=f"cast:{member.slug}",
        )
    except Exception as exc:
        logger.debug("[cast] memory mirror failed: %s", exc)


# ── Public API ──────────────────────────────────────────────────────────


def get_or_create(*, who: str, relation: str, appearance: str = "",
                  notes: str = "") -> CastMember:
    """Return the cast member for ``who``, creating it on first mention.

    On creation an ``appearance`` is REQUIRED — the agent must describe
    the person once (it knows them from the persona backstory).  We don't
    invent one, so the face stays coherent with how she talks about them.
    """
    if not _pg_configured():
        raise SeedreamError(
            "cast photos need Postgres configured (SaaS mode) so the person "
            "stays consistent across photos."
        )
    uid = _uid()
    slug = _slugify(who or relation)
    existing = _fetch(uid, slug)
    if existing is not None:
        return existing
    appearance = (appearance or "").strip()
    if not appearance:
        raise SeedreamError(
            f"first photo of '{who}': describe their appearance in the "
            "`appearance` arg so future photos match."
        )
    member = CastMember(
        slug=slug, name=(who or relation).strip(), relation=relation,
        appearance=appearance, seed=_stable_seed(appearance),
        notes=(notes or "").strip(),
    )
    _insert(uid, member)
    _remember(member)
    logger.info("[cast] created %s (relation=%s) for %s",
                slug, relation, uid[:8])
    return member


def take_cast_photo(
    *,
    who: str,
    relation: str = "friend",
    appearance: str = "",
    scene_hint: str | None = None,
    notes: str = "",
    model: str | None = None,
    size: str = DEFAULT_SIZE,
    generator: SeedreamGenerator | None = None,
) -> CastResult:
    """Generate one single-subject photo of a cast member (family/friend).

    Reuses the member's stable seed + bootstrapped reference image so the
    face stays consistent; the first generation becomes the reference.
    """
    from ..quota import check_disk, check_photos
    over = check_photos()
    if over:
        raise SeedreamError(over)
    refusal = check_disk(extra_bytes=700_000)
    if refusal:
        raise SeedreamError(refusal)

    member = get_or_create(who=who, relation=relation,
                           appearance=appearance, notes=notes)

    is_zh = _looks_chinese(member.appearance)
    chunks = [member.appearance.strip()]
    if scene_hint:
        chunks.append(scene_hint.strip())
    chunks.append(_CAST_STYLE_ZH if is_zh else _CAST_STYLE_EN)
    prompt = "\n\n".join(c for c in chunks if c)

    from .tigris import presign_get, upload_photo
    ref_url = presign_get(member.reference_key) if member.reference_key else None

    generator = generator or SeedreamGenerator(model=model)
    from .photo_album import PhotoAlbum
    album = PhotoAlbum()

    with tempfile.TemporaryDirectory(prefix="cast_") as tmp:
        paths = generator.generate_and_download(
            prompt, output_dir=tmp,
            filename_prefix=f"cast_{member.slug}",
            size=size, n=1, seed=member.seed,
            reference_image=ref_url, model=model,
        )
        if not paths:
            raise SeedreamError("Seedream returned no image")
        saved = album.add(
            paths[0],
            kind=f"cast_{member.relation}",
            prompt=prompt,
            metadata={"cast_slug": member.slug, "cast_name": member.name,
                      "relation": member.relation, "seed": member.seed},
        )

    # Self-bootstrap: promote the first generation to this member's stable
    # reference so subsequent photos anchor on it and the face converges.
    if not member.reference_key:
        try:
            uid = _uid()
            key = upload_photo(uid, saved, filename=f"castref_{member.slug}.jpg")
            if key:
                _set_reference_key(uid, member.slug, key)
                member.reference_key = key
        except Exception as exc:
            logger.debug("[cast] reference bootstrap failed: %s", exc)

    logger.info("[cast] photo for %s (relation=%s)", member.slug, member.relation)
    return CastResult(path=saved, prompt=prompt, member=member,
                      model=generator.model)
