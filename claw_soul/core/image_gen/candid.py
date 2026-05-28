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


Category = Literal["animal", "scenery", "food", "fun", "random"]


# Style suffix appended to every candid prompt — keeps the look consistent
# with the rest of the conversation (a partner sharing a phone snapshot,
# not a glossy stock photo).
_CANDID_STYLE = (
    "写实手机随拍风格，自然光线，不要修图感，"
    "构图随意像是顺手按下快门，画面有生活气息。"
    "不要文字、不要水印、不要 NSFW。"
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


def _pick_subject(category: Category, hint: str | None) -> tuple[str, str]:
    """Return ``(resolved_category, subject_prompt)``.

    "random" picks one of the four real categories; the hint, if given,
    overrides the bucket pick so the agent can be specific
    ("拍一下楼下那只总过来蹭饭的猫" → category=animal, hint kept).
    """
    if category == "random":
        category = random.choice(list(_BUCKET_BY_CATEGORY.keys()))  # type: ignore[assignment]
    bucket = _BUCKET_BY_CATEGORY.get(category, _SCENERY_SUBJECTS)
    base = random.choice(bucket)
    if hint:
        return category, f"{base}\n额外细节: {hint.strip()}"
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
    from ..quota import check_disk
    refusal = check_disk(extra_bytes=700_000)
    if refusal:
        raise SeedreamError(refusal)

    resolved_cat, subject = _pick_subject(category, hint)
    prompt = f"{subject}\n\n{_CANDID_STYLE}"

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
