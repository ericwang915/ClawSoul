"""
Daily, weather-coupled, persona-appropriate outfit picker.

The companion's *face/body* live in appearance.md (fixed), but her **outfit**
should change day to day and match the real weather.  This module produces one
deterministic outfit per calendar day:

  • garment weight  ← real temperature band (hot … freezing)
  • rain/snow/sun    ← weather condition
  • style-of-the-day ← seeded by the date, but drawn ONLY from the styles that
                       fit this companion's persona (traits + occupation), so a
                       sporty girl-next-door never wakes up in avant-garde
                       couture.

Same date + same weather + same persona → same outfit, so the selfie image and
what she says in chat ("I threw on a beige trench today") stay consistent.
"""

from __future__ import annotations

import re
from datetime import datetime

# ── Temperature → garment weight band ───────────────────────────────────────
# (threshold °C, band).  First band whose threshold <= temp wins.
_BANDS = [(28, "hot"), (23, "warm"), (17, "mild"), (9, "cool"), (1, "cold"), (-99, "freezing")]

# Seasonal fallback when no temperature is known (northern hemisphere months).
_SEASON_BAND = {12: "cold", 1: "cold", 2: "cold", 3: "mild", 4: "mild", 5: "warm",
                6: "warm", 7: "hot", 8: "hot", 9: "warm", 10: "mild", 11: "cool"}


def _band(temp: float | None, now: datetime) -> str:
    if temp is None:
        return _SEASON_BAND.get(now.month, "mild")
    for thr, name in _BANDS:
        if temp >= thr:
            return name
    return "freezing"


# ── Concrete garments by gender + band (zh, en).  A few options per cell so
#    the same band still varies across days. ─────────────────────────────────
_GARMENTS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "female": {
        "hot": [("一条清凉的吊带连衣裙", "a breezy slip sundress"),
                ("短袖上衣配飘逸半身裙", "a tee with a flowy skirt"),
                ("无袖背心配短裤", "a sleeveless top and shorts")],
        "warm": [("短袖衬衫配牛仔裤", "a short-sleeve shirt and jeans"),
                 ("一条碎花连衣裙", "a floral midi dress"),
                 ("T恤配阔腿裤", "a tee and wide-leg pants")],
        "mild": [("薄针织衫配长裤", "a light knit and trousers"),
                 ("衬衫外搭开衫", "a shirt layered under a cardigan"),
                 ("长袖连衣裙", "a long-sleeve dress")],
        "cool": [("毛衣外搭夹克", "a sweater under a jacket"),
                 ("针织衫配风衣", "a knit top with a trench coat"),
                 ("卫衣配长裤", "a hoodie and trousers")],
        "cold": [("厚毛衣配大衣", "a chunky sweater under a wool coat"),
                 ("羽绒服配围巾", "a puffer jacket and a scarf")],
        "freezing": [("厚羽绒服、围巾和针织帽", "a heavy down coat, scarf and beanie")],
    },
    "male": {
        "hot": [("一件短袖T恤配短裤", "a short-sleeve tee and shorts"),
                ("亚麻衬衫配休闲短裤", "a linen shirt and chino shorts"),
                ("无袖背心配运动短裤", "a tank top and athletic shorts")],
        "warm": [("短袖衬衫配牛仔裤", "a short-sleeve shirt and jeans"),
                 ("Polo衫配休闲裤", "a polo and chinos"),
                 ("T恤配工装裤", "a tee and cargo pants")],
        "mild": [("长袖T恤配长裤", "a long-sleeve tee and trousers"),
                 ("衬衫外搭薄夹克", "a shirt under a light jacket"),
                 ("针织衫配牛仔裤", "a knit pullover and jeans")],
        "cool": [("卫衣外搭夹克", "a hoodie under a jacket"),
                 ("毛衣配风衣", "a sweater with a trench coat"),
                 ("衬衫外搭机能外套", "a shirt under a utility jacket")],
        "cold": [("厚毛衣配大衣", "a chunky knit under a wool coat"),
                 ("羽绒服配围巾", "a puffer jacket and a scarf")],
        "freezing": [("厚羽绒服、围巾和毛线帽", "a heavy down parka, scarf and beanie")],
    },
}
# Anyone without a binary gender uses the female wardrobe as the softer default.
_GARMENTS["other"] = _GARMENTS["female"]


# ── At-home / loungewear, by gender + band.  No outerwear, no umbrella — what
#    she actually wears in the bedroom or on the couch. ────────────────────────
_HOME_GARMENTS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "female": {
        "hot": [("一件宽松大T恤", "an oversized sleep tee"),
                ("吊带睡裙", "a slip nightdress"),
                ("短袖家居服套装", "a short-sleeve loungewear set")],
        "warm": [("宽松T恤配棉质短裤", "a loose tee and cotton shorts"),
                 ("柔软的家居服套装", "a soft loungewear set")],
        "mild": [("宽松卫衣配休闲裤", "a comfy sweatshirt and joggers"),
                 ("针织家居服", "a knit lounge set")],
        "cool": [("宽松毛衣配卫裤", "an oversized sweater and sweatpants"),
                 ("加绒连帽卫衣", "a fleece-lined hoodie and joggers")],
        "cold": [("厚卫衣配毛绒家居裤", "a chunky hoodie and fuzzy lounge pants"),
                 ("法兰绒睡衣", "soft flannel pajamas")],
        "freezing": [("加厚法兰绒睡衣配毛绒袜", "thick flannel pajamas and fuzzy socks")],
    },
    "male": {
        "hot": [("一件宽松大T恤配短裤", "an oversized tee and shorts"),
                ("短袖家居服", "a short-sleeve loungewear set")],
        "warm": [("宽松T恤配棉质短裤", "a loose tee and cotton shorts"),
                 ("舒适的家居服", "a comfy loungewear set")],
        "mild": [("宽松卫衣配运动裤", "a relaxed sweatshirt and joggers"),
                 ("长袖家居服", "a long-sleeve lounge set")],
        "cool": [("连帽卫衣配卫裤", "a hoodie and sweatpants"),
                 ("加绒卫衣套装", "a fleece sweat set")],
        "cold": [("厚卫衣配毛绒家居裤", "a chunky hoodie and warm lounge pants"),
                 ("法兰绒睡衣", "soft flannel pajamas")],
        "freezing": [("加厚法兰绒睡衣配厚袜", "thick flannel pajamas and warm socks")],
    },
}
_HOME_GARMENTS["other"] = _HOME_GARMENTS["female"]

# Activity keywords that place her clearly outside vs clearly at home.
_OUT_HINTS = (
    "出门", "外面", "街", "公园", "咖啡馆", "咖啡店", "逛", "散步", "商场", "地铁",
    "通勤", "上班", "公司", "餐厅", "聚会", "约", "看展", "市集", "健身房", "跑步",
    "outside", "out ", "park", "street", "cafe", "café", "coffee shop", "walk",
    "commut", "office", "mall", "restaurant", "gym", "shopping", "errand", "market",
)
_HOME_HINTS = (
    "卧室", "床", "在家", "家里", "沙发", "房间", "窝", "客厅", "书房", "厨房", "阳台",
    "醒", "起床", "睡", "赖床", "宅",
    "bedroom", "in bed", "at home", "couch", "sofa", "living room", "room", "kitchen",
    "balcony", "woke", "waking", "wake up", "lounging", "nap", "asleep",
)


def _setting(activity: str, hour: int) -> str:
    """'out' or 'home' from the current activity, falling back to the clock."""
    a = (activity or "").lower()
    if any(h in a for h in _OUT_HINTS):
        return "out"
    if any(h in a for h in _HOME_HINTS):
        return "home"
    # Ambiguous: early morning / late night → at home; daytime → likely out.
    if hour < 9 or hour >= 22:
        return "home"
    return "out"


# ── Style-of-the-day vocabulary (zh label, en label, en palette hint) ───────
_STYLES: dict[str, tuple[str, str, str]] = {
    "casual":   ("休闲随性", "relaxed everyday", "neutral tones"),
    "sporty":   ("运动活力", "sporty athleisure", "clean sporty colors, sneakers"),
    "cozy":     ("慵懒舒适", "soft cozy", "warm oversized layers"),
    "elegant":  ("优雅精致", "elegant and refined", "clean lines, muted palette"),
    "street":   ("街头潮酷", "edgy streetwear", "layered, bold accents"),
    "office":   ("通勤干练", "smart-casual", "tailored, understated"),
    "vacation": ("度假轻松", "breezy vacation", "light fabrics, sunny colors"),
    "y2k":      ("Y2K潮流", "playful Y2K", "retro-pop, bright"),
    "sweet":    ("甜美可爱", "sweet and cute", "pastel, soft"),
    "vintage":  ("复古文艺", "vintage retro", "earthy, nostalgic"),
    "minimal":  ("极简高级", "minimalist", "monochrome, simple"),
    "preppy":   ("学院经典", "preppy academia", "collared, classic"),
    "boho":     ("波西米亚", "boho artsy", "flowy, textured, warm"),
    "chic":     ("法式松弛", "effortless French chic", "soft neutrals, polished"),
}

_UNIVERSAL = ["casual", "cozy"]


def persona_styles(choices: dict | None) -> list[str]:
    """The style lanes that suit THIS companion, from traits + occupation.

    Returns an ordered, de-duped subset of _STYLES keys (the day-seed then
    rotates within this subset)."""
    out = list(_UNIVERSAL)
    ch = choices or {}
    traits = {str(t).lower() for t in (ch.get("traits") or [])}
    occ = (ch.get("companionOccupation") or "").lower()
    arche = (ch.get("archetype") or "").lower()
    blob = " ".join([occ, arche, " ".join(traits)])

    def has(*words: str) -> bool:
        return any(w in blob for w in words)

    if has("flirty", "expressive", "dramatic", "seductive", "bold"):
        out += ["elegant", "street", "chic"]
    if has("empath", "gentle", "warm", "caring", "sweet", "shy", "soft"):
        out += ["sweet", "cozy", "chic"]
    if has("playful", "cheerful", "bubbly", "energetic", "fun", "sporty"):
        out += ["sporty", "y2k", "street"]
    if has("calm", "mature", "elegant", "reserved", "intellectual", "stoic"):
        out += ["elegant", "minimal", "office"]
    if has("design", "artist", "photograph", "music", "writer", "creative", "art"):
        out += ["street", "vintage", "y2k", "boho"]
    if has("engineer", "developer", "research", "doctor", "scientist", "tech"):
        out += ["minimal", "sporty", "preppy", "casual"]
    if has("teacher", "psycholog", "marketing", "entrepreneur", "consultant", "manager"):
        out += ["office", "elegant", "preppy", "chic"]
    if has("student", "college"):
        out += ["y2k", "sweet", "preppy", "sporty"]

    seen: list[str] = []
    for s in out:
        if s in _STYLES and s not in seen:
            seen.append(s)
    return seen[:6] if len(seen) >= 3 else ["casual", "cozy", "elegant", "sporty"]


# ── Weather parsing + condition extras ──────────────────────────────────────
_COND_RE = re.compile(
    r"(暴雨|大雨|中雨|小雨|雷阵雨|阵雨|雨|雪|雾|霾|晴|多云|阴|"
    r"thunder|storm|rain|drizzle|snow|sleet|fog|mist|haze|sunny|clear|cloud|overcast)",
    re.IGNORECASE,
)
_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*[Cc]")


def _parse_weather(weather: str | None) -> tuple[float | None, str]:
    if not weather:
        return None, ""
    m = _TEMP_RE.search(weather)
    temp = float(m.group(1)) if m else None
    c = _COND_RE.search(weather)
    return temp, (c.group(1).lower() if c else "")


def _condition_extra(condition: str, band: str) -> tuple[str, str]:
    c = condition
    if any(w in c for w in ("雨", "rain", "drizzle", "storm", "thunder")):
        return "，手里拿着伞", ", holding an umbrella"
    if any(w in c for w in ("雪", "snow", "sleet")):
        return "，戴着手套", ", with gloves"
    if any(w in c for w in ("晴", "sunny", "clear")) and band in ("hot", "warm"):
        return "，戴着墨镜", ", wearing sunglasses"
    return "", ""


def _is_zh(lang: str | None) -> bool:
    return bool(lang) and lang.lower().startswith("zh")


def outfit_today(
    now: datetime,
    weather: str | None,
    lang: str | None = "en",
    choices: dict | None = None,
    activity: str = "",
) -> str:
    """A deterministic, weather- AND setting-coupled, persona-appropriate outfit.

    Indoors (bedroom, couch, just-woke, late night) she's in loungewear — no
    coat, no umbrella, no sunglasses.  Outdoors she's in the weather-appropriate
    style-of-the-day.  Same date + weather + activity → same outfit, so the
    selfie and what she says in chat agree.
    """
    if choices is None:
        try:
            from .. import config  # noqa: F401  (kept lazy; choices usually passed in)
            from ... import companion as _comp
            choices = _comp.load_choices() or {}
        except Exception:
            choices = {}

    gender = (choices.get("companionGender") or "female").lower()
    if gender not in _GARMENTS:
        gender = "female"

    temp, condition = _parse_weather(weather)
    band = _band(temp, now)
    # Day ordinal increments by exactly 1 each day, so indices rotate daily;
    # the *7 on the garment decorrelates it from the style pick.
    ordinal = now.toordinal()
    setting = _setting(activity, now.hour)

    if setting == "home":
        # Loungewear: warmth still tracks the weather, but no outerwear / extras
        # and no loud style label — it's what she lounges in at home.
        homes = _HOME_GARMENTS[gender][band]
        home_zh, home_en = homes[(ordinal * 7) % len(homes)]
        return f"{home_zh}（在家）" if _is_zh(lang) else f"{home_en}, lounging at home"

    styles = persona_styles(choices)
    style_key = styles[ordinal % len(styles)]
    garms = _GARMENTS[gender][band]
    garm_zh, garm_en = garms[(ordinal * 7) % len(garms)]
    style_zh, style_en, palette = _STYLES[style_key]
    extra_zh, extra_en = _condition_extra(condition, band)

    if _is_zh(lang):
        return f"{garm_zh}，{style_zh}风格{extra_zh}"
    return f"{garm_en}, {style_en} style ({palette}){extra_en}"
