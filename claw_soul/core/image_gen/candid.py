"""
Candid shot generator — non-self images the companion "stumbled on".

Different from :mod:`selfie`:
  - Not a self-portrait — the subject is animals, scenery, food, fun
    things, etc.  No persona-appearance prompt, no character reference.
  - Picked by the agent based on conversational context; the LLM passes
    a ``category`` (animal / scenery / food / fun / random) and an
    optional ``hint``.
  - Same Seedream backend as selfies, same quota check, same realistic
    "phone snapshot" style so the photos fit the everyday vibe.

The agent decides *when* to call this from the chat flow; this module is
just the image-rendering primitive.
"""

from __future__ import annotations

import logging
import random
import tempfile
from dataclasses import dataclass
from typing import Literal

from .generator import DEFAULT_SIZE, SeedreamError, SeedreamGenerator

logger = logging.getLogger(__name__)


Category = Literal["animal", "scenery", "food", "fun", "place", "random"]


# Style suffix appended to every candid prompt — keeps the look consistent
# with the rest of the conversation (a partner sharing a phone snapshot,
# not a glossy stock photo).  Language-aware: Chinese personas keep the CN
# suffix (Seedream's strongest prompting language); others get EN so the
# look isn't biased by surrounding Chinese context.
_CANDID_STYLE_ZH = (
    "手机随手抓拍的生活快照，现场自然光、光线略不均匀，构图随意像是顺手按下快门，"
    "真实质感、有一点点噪点或轻微动态模糊，景深浅、背景自然虚化，完全没有修图美化感。"
    "像真人用手机拍的普通照片，不是渲染图、不是CG、不是棚拍、不是商业广告图。"
    "不要文字、不要水印、不要 NSFW。"
)
_CANDID_STYLE_EN = (
    "An everyday phone snapshot grabbed in the moment, available ambient light "
    "(slightly uneven), casual un-staged framing like a quick tap of the shutter. "
    "Real texture with a touch of sensor grain or mild motion blur, shallow depth "
    "of field with a naturally blurred background, zero retouching or beautifying. "
    "Looks like an ordinary photo a real person took on a phone — NOT a render, "
    "NOT CGI, NOT a studio shot, NOT a commercial/stock image, not glossy. "
    "No text, no watermark, no NSFW."
)


# Category → seed-prompt picker.  Each picker returns a Chinese subject
# description that gets combined with the agent's hint (if any) and the
# global style suffix.  Lists are intentionally short and themed so a
# random draw still feels coherent.

_ANIMAL_SUBJECTS = [
    "街角晒太阳的橘猫，眯着眼，毛蓬蓬的",
    "公园长椅旁趴着一只柴犬，伸着舌头",
    "电线上排成一排的麻雀，背景是傍晚的天空",
    "便利店门口蹲着一只大狸花猫，眼神警惕",
    "海边沙滩上散步的两只海鸥",
    "窗台外的鸽子，背景是城市楼顶",
]

_SCENERY_SUBJECTS = [
    "傍晚阳台望出去的城市天际线，远处的云被夕阳染成橙粉色",
    "雨后湿漉漉的街道，灯光倒影在地面上",
    "清晨地铁站台，几乎没人，阳光斜照进来",
    "公园里一条小路，两旁是开始变黄的梧桐树",
    "海边码头，停着几艘小渔船，浪很温柔",
    "山顶看下去的云海，远处有几座更高的峰",
]

_FOOD_SUBJECTS = [
    "桌上一碗热腾腾的拉面，叉烧、溏心蛋、葱花清晰可见",
    "下午茶桌上的拿铁拉花，旁边是一块芝士蛋糕，斜光",
    "宵夜大排档的小龙虾盘，蒜蓉味，红油溅在桌布上",
    "便利店买的关东煮盒，鱼蛋、白萝卜、海带卷，雾气升腾",
    "刚出炉的可颂在牛皮纸袋里，焦糖色脆皮反光",
    "夜市里一串烤肉串，油花滴下来，背景是模糊的灯笼",
]

_FUN_SUBJECTS = [
    "桌上一摞拼到一半的乐高，旁边是一杯凉了的咖啡",
    "二手书店里堆得歪歪扭扭的书架，灯光昏黄",
    "夜市夹娃娃机前的爪子刚好抓住一只小熊",
    "便利店冰柜里花花绿绿一排进口零食，挑了好久",
    "电影院散场后空的座位，地面上还有零落的爆米花桶",
    "窗台上一只手冲咖啡壶+一支温度计，蒸汽袅袅",
]

_BUCKET_BY_CATEGORY: dict[str, list[str]] = {
    "animal":  _ANIMAL_SUBJECTS,
    "scenery": _SCENERY_SUBJECTS,
    "food":    _FOOD_SUBJECTS,
    "fun":     _FUN_SUBJECTS,
}

# English subject pools — used when the persona's chat language isn't Chinese,
# so the snapshot isn't pulled toward an East-Asian setting by CN context.
_ANIMAL_SUBJECTS_EN = [
    "a ginger cat sunbathing on a street corner, eyes half-closed, fur fluffed up",
    "a shiba inu sprawled by a park bench, tongue out",
    "sparrows lined up on a wire against an evening sky",
    "a tabby cat crouched outside a corner shop, watchful eyes",
    "two seagulls strolling on the beach",
    "a pigeon on the windowsill, city rooftops behind it",
]
_SCENERY_SUBJECTS_EN = [
    "the city skyline from a balcony at dusk, clouds tinged orange-pink",
    "a rain-slicked street after a shower, lights reflected on the ground",
    "an almost-empty train platform in the early morning, sun slanting in",
    "a tree-lined path in the park, leaves just turning yellow",
    "a harbor at the seaside, a few small boats, gentle waves",
    "a sea of clouds seen from a mountaintop, taller peaks in the distance",
]
_FOOD_SUBJECTS_EN = [
    "a steaming bowl of ramen — chashu, soft egg, scallions clearly visible",
    "a latte with leaf art beside a slice of cheesecake, side light",
    "a late-night plate of garlic crayfish, red oil splashed on the cloth",
    "a convenience-store oden box — fish balls, daikon, kelp rolls, steam rising",
    "a fresh croissant in a paper bag, glossy caramel crust",
    "a skewer of grilled meat at a night market, fat dripping, blurred lanterns behind",
]
_FUN_SUBJECTS_EN = [
    "a half-built Lego set on a desk beside a cold cup of coffee",
    "crooked, overstuffed shelves in a secondhand bookshop, warm dim light",
    "a claw machine just grabbing a little bear",
    "a convenience-store freezer wall of colorful imported snacks",
    "empty cinema seats after the credits, a stray popcorn tub on the floor",
    "a pour-over kettle and thermometer on the windowsill, steam curling up",
]

_BUCKET_BY_CATEGORY_EN: dict[str, list[str]] = {
    "animal":  _ANIMAL_SUBJECTS_EN,
    "scenery": _SCENERY_SUBJECTS_EN,
    "food":    _FOOD_SUBJECTS_EN,
    "fun":     _FUN_SUBJECTS_EN,
}


def _place_subject(is_zh: bool) -> str:
    """Scene-driven 'where I am right now' subject, derived from today's
    plan so the snapshot matches what she's actually doing.  Falls back to
    a generic scenery pick when there's no current activity."""
    try:
        from .scene_builder import build_scene
        scene = build_scene()
        activity = (scene.activity or "").strip()
    except Exception:
        activity = ""
    if not activity:
        return random.choice(_SCENERY_SUBJECTS if is_zh else _SCENERY_SUBJECTS_EN)
    if is_zh:
        return f"她此刻所在环境的随手一拍：{activity}的场景，没有人物入镜，只拍周围的样子"
    return (f"a quick snapshot of where she is right now: the setting around "
            f"'{activity}', no people in frame, just the surroundings")


def _pick_subject(category: Category, hint: str | None) -> tuple[str, str]:
    """Return ``(resolved_category, subject_prompt)`` in the persona's
    chat language.

    "random" picks one of the real categories; the hint, if given,
    overrides the bucket pick so the agent can be specific. "place" is
    scene-driven from today's plan.
    """
    from .. import lang as _lang
    is_zh = _lang.is_chinese()
    pools = _BUCKET_BY_CATEGORY if is_zh else _BUCKET_BY_CATEGORY_EN

    if category == "random":
        category = random.choice([*pools.keys(), "place"])  # type: ignore[assignment]

    # An animal photo reads as "my pet" — so use the persona's ONE canonical
    # pet (same species/colour every time) instead of a random street cat/dog.
    pet = ""
    if category == "animal":
        try:
            from .persona_render import canonical_pet
            pet = canonical_pet()
        except Exception:
            pet = ""

    if category == "place":
        base = _place_subject(is_zh)
    elif category == "animal" and pet:
        base = (
            f"我家养的{pet}，随手拍的日常一张，自然放松的姿态，在家里的环境中"
            if is_zh else
            f"my own pet — {pet} — a casual everyday snapshot, natural relaxed "
            "pose, at home"
        )
    else:
        bucket = pools.get(category, pools["scenery"])
        base = random.choice(bucket)

    if hint:
        extra = "额外细节" if is_zh else "extra detail"
        return category, f"{base}\n{extra}: {hint.strip()}"
    return category, base


# ── Result ────────────────────────────────────────────────────────────


@dataclass
class CandidResult:
    path: str
    prompt: str
    category: Category
    model: str

    def caption(self) -> str:
        emoji = {
            "animal":  "🐾",
            "scenery": "🌆",
            "food":    "🍜",
            "fun":     "✨",
            "place":   "📍",
        }.get(self.category, "📷")
        return emoji


# ── Public API ────────────────────────────────────────────────────────


def take_candid(
    *,
    category: Category = "random",
    hint: str | None = None,
    model: str | None = None,
    size: str = DEFAULT_SIZE,
    generator: SeedreamGenerator | None = None,
) -> CandidResult:
    """Generate one non-self snapshot.

    Cheap relative to selfies: no reference image lookup, no persona
    rendering, smaller prompt assembly path.
    """
    # Pre-flight disk check — same budget as selfies (~700 KB).
    from ..quota import check_disk, check_photos
    over = check_photos()
    if over:
        raise SeedreamError(over)
    refusal = check_disk(extra_bytes=700_000)
    if refusal:
        raise SeedreamError(refusal)

    from .. import lang as _lang
    resolved_cat, subject = _pick_subject(category, hint)
    style = _CANDID_STYLE_ZH if _lang.is_chinese() else _CANDID_STYLE_EN
    prompt = f"{subject}\n\n{style}"

    generator = generator or SeedreamGenerator(model=model)

    with tempfile.TemporaryDirectory(prefix="candid_") as tmp:
        paths = generator.generate_and_download(
            prompt,
            output_dir=tmp,
            filename_prefix=f"candid_{resolved_cat}",
            size=size,
            n=1,
        )
        if not paths:
            raise SeedreamError("Seedream returned no image")

        # Move out of the temp dir into the photo album so the file
        # survives this call's cleanup and shows up in look_back queries.
        from .photo_album import PhotoAlbum
        album = PhotoAlbum()
        kept_path = album.add(
            paths[0],
            kind=f"candid_{resolved_cat}",
            prompt=prompt,
            metadata={"category": resolved_cat, "hint": hint or ""},
        )

    logger.info("[candid] generated category=%s prompt=%s",
                resolved_cat, subject[:50])
    return CandidResult(
        path=kept_path,
        prompt=prompt,
        category=resolved_cat,
        model=generator.model,
    )
