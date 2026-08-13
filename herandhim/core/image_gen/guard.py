"""
Content guardrail for image generation.

A single chokepoint (called from the Seedream generator) that refuses to build
any image whose prompt sexualizes a minor, or that uses terms with no
legitimate use in an adult-companion product.  Child sexual content is a
strict-liability red line in every jurisdiction we touch — we refuse *before*
any API call, and the caller surfaces a neutral, in-character decline.

Detection is intentionally narrow: we block the (minor-cue AND sexual-cue)
combination, plus a short hard list of terms that are never legitimate.  Bare
ages, the word "teen" alone, or a lone "young" are NOT blocked, so ordinary
persona selfies are unaffected.
"""

from __future__ import annotations

import re


class ImageBlocked(Exception):
    """Raised when an image prompt is refused by the content guard."""


# Youth / minor cues.  Only meaningful for the block when they co-occur with a
# sexual cue (see ``assert_allowed``).  No bare digits here — "11pm", "size 12"
# etc. must not trip the guard.
_MINOR = re.compile(
    r"\b(child|children|kid|kids|minor|underage|under[\s-]?age|"
    r"under[\s-]?(18|eighteen)|pre[\s-]?teen|preteen|teen|teenager|teenaged|"
    r"tween|toddler|infant|newborn|little\s+(girl|boy)|"
    r"school[\s-]?girl|school[\s-]?boy|young\s+(girl|boy)|"
    r"(grade|elementary|middle|junior\s+high)\s+school)\b"
    r"|未成年|幼[女童儿兒]|小学生|小學生|初中生|中学生|中學生|童颜|童顏",
    re.IGNORECASE,
)

# Sexual / nudity cues.
_SEXUAL = re.compile(
    r"\b(nude|naked|nsfw|sex|sexual|sexy|porn(ographic)?|explicit|topless|"
    r"bottomless|lingerie|underwear|panties|thong|cleavage|breasts?|nipples?|"
    r"genital|aroused|seductive|erotic|provocative|undress(ed|ing)?|"
    r"spread\s+legs?|suggestive)\b"
    r"|裸|色情|情色|性感|脱衣|脫衣|内衣|內衣|乳|挑逗|诱惑|誘惑|情趣|露点|露點",
    re.IGNORECASE,
)

# Terms with no legitimate use — hard block on their own.
_HARD = re.compile(
    r"\b(loli|lolita|shota|cp\s+(porn|content)|child\s+porn|childporn|"
    r"jailbait|cunny)\b"
    r"|萝莉|蘿莉|正太|幼女|幼交|童色",
    re.IGNORECASE,
)


def assert_allowed(prompt: str) -> None:
    """Refuse prompts that cross the child-sexual-content red line.

    Raises :class:`ImageBlocked`; callers translate this into a neutral,
    in-character refusal (never exposing the rule itself).
    """
    text = prompt or ""
    if _HARD.search(text):
        raise ImageBlocked("prohibited-term")
    if _MINOR.search(text) and _SEXUAL.search(text):
        raise ImageBlocked("minor-sexual")
