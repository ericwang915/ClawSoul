#!/usr/bin/env python3
"""Pick one literary line that fits a heavy / introspective mood.

Light moods deliberately return *no* line so this skill can't be misused as a
generic "share an inspirational quote" daemon. Returns a single parsable line:

    [mood_share] "quote" — attribution

or, when the mood is too light:

    (skip — mood is too light)
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime


# Each line: (text, attribution, mood_tags, lang)
_POOL: list[tuple[str, str, list[str], str]] = [
    # Yearning / missing
    ("想见你，乱了四季。",                         "余光中（改编）", ["想念", "yearn", "miss"], "zh"),
    ("我不喜欢一个人，但我有时候喜欢一个人。",     "（小津安二郎语意改编）", ["loneliness", "想念"], "zh"),
    ("All I want is to be the one closing the door.", "Pablo Neruda（化用）", ["yearn", "alone"], "en"),
    ("I miss you in a place where nothing grows.",     "Ocean Vuong", ["miss", "yearn", "loneliness"], "en"),
    ("能否就这样把我藏起来。",                      "陈绮贞", ["yearn", "hide"], "zh"),

    # Quiet sadness / heaviness
    ("有些人活得像一首没人懂的诗。",                 "anon", ["sad", "lonely"], "zh"),
    ("生活总要有那么一段一个人扛过去。",             "anon", ["sad", "alone"], "zh"),
    ("The cure for anything is salt water — sweat, tears, or the sea.", "Karen Blixen", ["sad", "heal"], "en"),
    ("And still, after all this time, the sun never says to the earth, 'You owe me.'", "Hafiz", ["sad", "grace"], "en"),
    ("夜深时一个人最像自己。",                       "anon", ["lonely", "night"], "zh"),

    # Tender / soft warmth
    ("我有所念人，隔在远远乡。",                     "白居易", ["tender", "想念"], "zh"),
    ("To love at all is to be vulnerable.",          "C.S. Lewis", ["love", "tender"], "en"),
    ("人间有味是清欢。",                             "苏轼", ["calm", "tender"], "zh"),
    ("In the meantime, I'd like to learn how to sit with you in silence.", "anon", ["tender", "calm"], "en"),

    # Anxious / overwhelmed
    ("做不到的事就先放在那儿，明天再说。",          "anon", ["anxious", "stress"], "zh"),
    ("Worrying does not take away tomorrow's troubles, it takes away today's peace.", "anon", ["anxious", "stress"], "en"),
    ("一日难过一日，便是一日。",                     "anon", ["heavy", "stress"], "zh"),

    # Letting go / acceptance
    ("尽人事，听天命。",                             "古训", ["accept", "calm"], "zh"),
    ("Let what wants to come, come. Let what wants to go, go.", "anon", ["accept", "letgo"], "en"),
]

# Moods deliberately classified as "light" — skip output for these
_LIGHT_TAGS = {"happy", "fun", "excited", "playful", "high", "开心", "兴奋", "好玩"}


def _looks_light(mood: str) -> bool:
    m = mood.lower()
    return any(tag in m for tag in _LIGHT_TAGS)


def _filter(mood: str, lang: str | None) -> list[tuple]:
    if not mood:
        return list(_POOL)
    m = mood.lower()
    candidates = [
        line for line in _POOL
        if any(tag.lower() in m or m in tag.lower() for tag in line[2])
    ]
    if lang in ("zh", "en"):
        candidates = [line for line in candidates if line[3] == lang] or candidates
    return candidates or list(_POOL)


def _pick(pool: list[tuple], seed_key: str) -> tuple:
    h = hashlib.sha1(seed_key.encode("utf-8")).digest()
    return pool[int.from_bytes(h[:4], "big") % len(pool)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mood", default="", help="Mood hint (e.g. 'miss them', '想念', 'lonely')")
    parser.add_argument("--lang", default=None, choices=("zh", "en"),
                        help="Bias toward this language source")
    args = parser.parse_args()

    if args.mood and _looks_light(args.mood):
        print("(skip — mood is too light)")
        return 0

    pool = _filter(args.mood, args.lang)
    seed = f"{datetime.now().strftime('%Y-%m-%d-%H')}|{args.mood}|{args.lang or ''}"
    text, who, _tags, _lang = _pick(pool, seed)
    print(f"[mood_share] \"{text}\" — {who}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
