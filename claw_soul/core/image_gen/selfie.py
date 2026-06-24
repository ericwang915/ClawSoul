"""
High-level selfie facade — assembles persona + scene → prompt → image.

This is the single entry point everything else calls (skill, scheduler,
proactive integration).  It hides the assembly logic so callers don't
need to know about persona_render / scene_builder / generator / album.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime

from ... import config
from .generator import DEFAULT_SIZE, SeedreamError, SeedreamGenerator
from .persona_render import load_appearance
from .photo_album import PhotoAlbum
from .scene_builder import Scene, build_scene

logger = logging.getLogger(__name__)


# ── Prompt template ────────────────────────────────────────────────────────
#
# Style suffix is language-aware: with a Chinese appearance description
# we keep the Chinese suffix (Seedream's strongest prompting language),
# but for non-CJK personas (American, French, Indian, etc.) we use the
# English suffix so Seedream isn't biased toward East-Asian features by
# the surrounding Chinese context.

_BASE_STYLE_ZH = (
    "手机前置摄像头随手自拍，未经修饰的生活快照，像随手发给男朋友的那种。"
    "真实自然的皮肤质感，看得到毛孔和细小瑕疵、几缕碎发，没有磨皮、没有美颜滤镜、没有瘦脸。"
    "现场自然光，光线略不均匀，景深浅、背景轻微虚化，可有一点点噪点或轻微动态模糊，构图随意不刻意。"
    "看起来就是真人用手机拍的普通照片，不是渲染图、不是CG、不是精修棚拍、不是杂志封面。"
    "画面里不要出现任何文字、字母、水印、字幕或logo。"
    "不要 NSFW，不要暴露，不要血腥。"
)

_BASE_STYLE_EN = (
    "Casual front-camera phone selfie, an unedited everyday snapshot like one "
    "you'd fire off to your partner. Real natural skin texture — visible pores, "
    "tiny imperfections, a few flyaway hairs; NO airbrushing, NO beauty filter, "
    "NO skin smoothing, NO face slimming. Available ambient light, slightly "
    "uneven, shallow depth of field with a softly blurred background, a touch of "
    "sensor grain or mild motion blur, casual un-staged framing. It should look "
    "like an ordinary photo a real person took on a phone — NOT a render, NOT "
    "CGI, NOT a retouched studio shot, NOT a magazine cover, not glossy or "
    "plastic. No text, no letters, no words, no watermark, no caption or logo "
    "anywhere in the image. No NSFW, no nudity, no violence."
)


def _looks_chinese(text: str) -> bool:
    """Heuristic: does the appearance description contain CJK characters?"""
    for ch in text or "":
        if "一" <= ch <= "鿿":
            return True
    return False


def _build_prompt(appearance: str, scene: Scene, extra_hint: str | None) -> str:
    """Assemble the Seedream prompt in the same language as the appearance
    description, so non-Asian personas don't get pulled back toward East-
    Asian features by surrounding Chinese context.

    For non-Chinese personas we also drop the auto-built scene block
    (which is hard-coded Chinese in scene_builder) — any extra context
    the LLM wants comes through ``extra_hint`` in whatever language the
    LLM picked.
    """
    is_zh = _looks_chinese(appearance)
    chunks: list[str] = [appearance.strip()]
    if is_zh:
        scene_block = scene.as_prompt_block()
        if scene_block:
            chunks.append(scene_block)
    else:
        # Non-CN persona: the auto scene block is Chinese, so we don't add it,
        # but we DO inject the daily outfit (already in English) — that's what
        # gives day-to-day, weather-appropriate wardrobe variety here.
        if scene.outfit:
            chunks.append(f"Outfit: {scene.outfit}.")
    if extra_hint:
        chunks.append(extra_hint.strip())
    chunks.append(_BASE_STYLE_ZH if is_zh else _BASE_STYLE_EN)
    return "\n\n".join(c for c in chunks if c)


# Face-card style: a clean, front-facing portrait whose ONLY job is to capture
# the face well, so it can anchor every later selfie (no scene, no outfit
# variety — those would pollute the identity anchor).
_REFERENCE_STYLE_ZH = (
    "正脸清晰肖像，中性自然表情，柔和自然光，头肩特写，简洁纯色背景。"
    "真实皮肤质感，看得到毛孔和细小瑕疵，没有磨皮、没有美颜滤镜，"
    "像真人用手机拍的，不是精修棚拍、不是渲染图。"
    "画面里不要任何文字、字母、水印、logo。不要 NSFW，不要暴露。"
)
_REFERENCE_STYLE_EN = (
    "Clear front-facing portrait, neutral natural expression, soft natural "
    "light, head-and-shoulders, plain solid background. Real skin texture with "
    "visible pores and tiny imperfections — NO airbrushing, NO beauty filter, "
    "NO skin smoothing; like a real phone photo, not a retouched studio shot or "
    "a render. No text, letters, watermark or logo anywhere. No NSFW, no nudity."
)


def _build_reference_prompt(appearance: str) -> str:
    is_zh = _looks_chinese(appearance)
    style = _REFERENCE_STYLE_ZH if is_zh else _REFERENCE_STYLE_EN
    return "\n\n".join(c for c in (appearance.strip(), style) if c)


def ensure_reference(
    album: "PhotoAlbum",
    generator: "SeedreamGenerator",
    *,
    model: str | None = None,
) -> str | None:
    """Return a local path to the companion's canonical face reference,
    creating or restoring it if it doesn't exist locally yet.

    Order: local file → restore from Tigris (survives machine destroy) →
    bootstrap a fresh face-card and persist it both places.  Best-effort:
    any failure returns None and the caller just generates without a
    reference (degrades to the old seed-only behaviour).
    """
    local = album.primary_reference()
    if local:
        return local

    from .. import tenancy
    uid = tenancy.get_current_user()

    # Restore from Tigris if a prior machine already built one.
    if uid:
        try:
            from . import tigris
            if tigris.is_configured():
                data = tigris.get_bytes(tigris.object_key(uid, album.REFERENCE_NAME))
                if data:
                    return album.save_reference_bytes(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[selfie] reference restore skipped: %s", exc)

    # Bootstrap: generate a clean face-card once, persist it.
    appearance = load_appearance()
    if not appearance.strip():
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="faceref_") as tmp:
            paths = generator.generate_and_download(
                _build_reference_prompt(appearance),
                output_dir=tmp,
                filename_prefix="faceref",
                size=DEFAULT_SIZE,
                n=1,
                seed=_stable_seed(appearance),
                model=model,
            )
            if not paths:
                return None
            dst = album.set_reference(paths[0])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[selfie] reference bootstrap failed: %s", exc)
        return None

    if uid:
        try:
            from . import tigris
            tigris.upload_photo(uid, dst, filename=album.REFERENCE_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[selfie] reference Tigris upload skipped: %s", exc)
    logger.info("[selfie] bootstrapped canonical face reference")
    return dst


def _stable_seed(appearance: str) -> int:
    """Deterministic seed derived from the appearance description.

    Same character description → same seed → more consistent face across
    photos even when no reference image is provided.
    """
    digest = hashlib.sha256(appearance.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


# ── Result dataclass ───────────────────────────────────────────────────────

@dataclass
class SelfieResult:
    path: str
    prompt: str
    model: str
    seed: int
    used_reference: bool
    scene: Scene

    def caption(self) -> str:
        """Short human-readable caption suitable for Telegram."""
        if self.scene.activity:
            from .. import lang as _lang
            now_word = "现在" if _lang.is_chinese() else "now"
            return f"{self.scene.time or now_word} · {self.scene.activity}"
        return self.scene.activity or ""


# ── Main entry point ───────────────────────────────────────────────────────

def take_selfie(
    *,
    scene_hint: str | None = None,
    use_reference: bool = True,
    seed: int | None = None,
    model: str | None = None,
    size: str = DEFAULT_SIZE,
    generator: SeedreamGenerator | None = None,
    album: PhotoAlbum | None = None,
    now: datetime | None = None,
) -> SelfieResult:
    """Generate one selfie reflecting the character + her current moment.

    ``scene_hint`` overrides or adds to the auto-built scene (e.g. when the
    LLM has a specific situation in mind).  ``use_reference`` toggles
    pulling the primary reference image from the album.
    """
    # Pre-flight disk quota check — refuse BEFORE spending an API call when
    # there's no room to save the result anyway. ~700 KB is a conservative
    # over-estimate of a 2048x2048 JPEG selfie.
    from ..quota import check_disk, check_photos
    over = check_photos()
    if over:
        raise SeedreamError(over)
    refusal = check_disk(extra_bytes=700_000)
    if refusal:
        raise SeedreamError(refusal)

    appearance = load_appearance()
    scene = build_scene(now)

    prompt = _build_prompt(appearance, scene, scene_hint)
    actual_seed = seed if seed is not None else _stable_seed(appearance)

    album = album or PhotoAlbum()
    generator = generator or SeedreamGenerator(model=model)

    # Canonical face reference — built once and reused, so the face stays the
    # same person across changing scenes/outfits (seed + text alone drift when
    # the prompt varies). Restores from Tigris on a fresh machine.
    reference_path = ensure_reference(album, generator, model=model) if use_reference else None

    with tempfile.TemporaryDirectory(prefix="selfie_") as tmp:
        paths = generator.generate_and_download(
            prompt,
            output_dir=tmp,
            filename_prefix="selfie",
            size=size,
            n=1,
            seed=actual_seed,
            reference_image=reference_path,
            model=model,
        )
        if not paths:
            raise SeedreamError("Generator returned no images.")
        src = paths[0]
        saved = album.add(
            src,
            kind="selfie",
            prompt=prompt,
            metadata={
                "model": generator.model,
                "seed": actual_seed,
                "size": size,
                "scene": {
                    "time": scene.time,
                    "activity": scene.activity,
                    "mood": scene.mood,
                    "weather": scene.weather,
                },
                "used_reference": bool(reference_path),
            },
        )

    # Background cleanup of old entries — cheap, no-op if nothing to prune
    try:
        album.cleanup()
    except Exception as exc:
        logger.debug("[selfie] album cleanup skipped: %s", exc)

    return SelfieResult(
        path=saved,
        prompt=prompt,
        model=generator.model,
        seed=actual_seed,
        used_reference=bool(reference_path),
        scene=scene,
    )


# ── Config helpers ─────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Selfie feature is enabled when both Seedream key and feature flag are on."""
    if not config.get_bool("selfie", "enabled", default=True):
        return False
    return bool(config.get_str("skills", "seedream", "apiKey", env="ARK_API_KEY"))
