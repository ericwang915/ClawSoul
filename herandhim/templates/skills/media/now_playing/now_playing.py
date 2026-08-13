#!/usr/bin/env python3
"""Pick a song that fits *the agent's* current scene and return a one-liner.

Output format (single line, parsable by the LLM):

    [now_playing] artist — title :: vibe :: optional_spotify_url

The pick is deterministic per (date, time-slot, mood/genre) so multiple calls
within the same hour return the same track, avoiding song-whiplash mid-chat.

Spotify enrichment is opt-in: if Bearer token at
``skills.spotify.accessToken`` resolves a track URL, it's appended. Otherwise
just the artist+title pair is returned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

# ── Hand-curated pool, light on hits, heavy on companionable mood music ─────
#
# Each entry is (artist, title, vibe, time_slot, genre, query_hint).
# time_slot: morning | afternoon | evening | night | anytime
# genre is lowercased for matching.

_TRACKS: list[tuple[str, str, str, str, str]] = [
    # Morning / light
    ("Mac DeMarco",   "Chamber of Reflection", "soft melancholy",     "morning",  "indie"),
    ("Tom Misch",     "South of the River",    "easy groove",         "morning",  "jazz"),
    ("Wave to Earth", "seasons",               "quiet, growing",      "morning",  "indie"),
    ("陈奕迅",        "好久不见",              "想念但平静",          "morning",  "cantopop"),

    # Afternoon / focus, café
    ("Yorushika",     "ノーチラス",            "drifting daydream",   "afternoon","japanese"),
    ("kessoku band",  "ギターと孤独と蒼い惑星",  "youthful longing",    "afternoon","japanese"),
    ("HOMESHAKE",     "Khmlwugh",              "lo-fi loop",          "afternoon","indie"),
    ("Snail Mail",    "Pristine",              "messy crush feeling", "afternoon","indie"),
    ("Wave to Earth", "love.",                 "warm dusk",           "afternoon","indie"),
    ("Bibio",         "Lovers' Carvings",      "sun through trees",   "afternoon","ambient"),
    ("林俊杰",        "我会想起你",            "怀念",               "afternoon","mandopop"),

    # Evening / mid-tempo
    ("FKJ",           "Ylang Ylang",           "swaying together",    "evening",  "jazz"),
    ("Phoebe Bridgers","Motion Sickness",      "complicated tender",  "evening",  "indie"),
    ("Daniel Caesar", "Best Part",             "soft duet love",      "evening",  "soul"),
    ("Frank Ocean",   "Self Control",          "almost-broken yearning","evening","r&b"),
    ("陶喆",          "普通朋友",              "犹豫的暧昧",          "evening",  "mandopop"),
    ("陈绮贞",        "旅行的意义",            "出发前的安静",        "evening",  "mandopop"),
    ("LANY",          "Malibu Nights",         "post-breakup quiet",  "evening",  "pop"),

    # Night / intimate
    ("Cigarettes After Sex", "Apocalypse",     "slow gravity",        "night",    "indie"),
    ("Beach House",   "Space Song",            "floating in dark",    "night",    "indie"),
    ("Joji",          "SLOW DANCING IN THE DARK", "lonely 2am",       "night",    "r&b"),
    ("BENEE",         "Soaked",                "rainy windowpane",    "night",    "pop"),
    ("Khruangbin",    "August 10",             "instrumental cocoon", "night",    "instrumental"),
    ("Mitski",        "I Bet on Losing Dogs",  "stubborn devotion",   "night",    "indie"),

    # Anytime / mood-coloured
    ("Carla Bruni",   "Quelqu'un m'a dit",     "soft French shrug",   "anytime",  "french"),
    ("Hikaru Utada",  "First Love",            "first-love nostalgia","anytime",  "japanese"),
    ("丁世光",        "好东西",                "宠溺感",             "anytime",  "mandopop"),
    ("Conan Gray",    "Heather",               "wistful crush",       "anytime",  "pop"),
]


def _time_slot(hour: int) -> str:
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _filter(slot: str, genre: str | None, mood: str | None) -> list[tuple]:
    pool = _TRACKS
    if genre:
        g = genre.lower()
        pool = [t for t in pool if g in t[4].lower() or g == t[4].lower()]
    # First, anything matching the slot exactly
    matched = [t for t in pool if t[3] == slot or t[3] == "anytime"]
    if matched:
        pool = matched
    if mood:
        m = mood.lower()
        # Soft filter: prefer tracks whose vibe shares any noun-ish word with the mood
        mood_tokens = [tok for tok in m.replace("，", " ").split() if tok]
        if mood_tokens:
            boosted = [t for t in pool if any(tok in t[2].lower() for tok in mood_tokens)]
            if boosted:
                pool = boosted
    return pool or list(_TRACKS)


def _pick_deterministic(pool: list[tuple], seed_key: str) -> tuple:
    h = hashlib.sha1(seed_key.encode("utf-8")).digest()
    idx = int.from_bytes(h[:4], "big") % len(pool)
    return pool[idx]


def _spotify_url(query: str) -> str | None:
    """Best-effort Spotify search → first track URL. Silent on failure."""
    token = os.environ.get("SPOTIFY_ACCESS_TOKEN") or ""
    if not token:
        for path in (os.path.expanduser("~/.herandhim/herandhim.json"), "herandhim.json"):
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    token = cfg.get("skills", {}).get("spotify", {}).get("accessToken", "") or ""
                    if token:
                        break
                except (OSError, json.JSONDecodeError):
                    continue
    if not token:
        return None

    try:
        import requests  # noqa: WPS433 — optional dep
        r = requests.get(
            "https://api.spotify.com/v1/search",
            params={"q": query, "type": "track", "limit": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        items = r.json().get("tracks", {}).get("items", [])
        if items:
            return items[0].get("external_urls", {}).get("spotify")
    except Exception:
        return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mood", default=None, help="Optional mood hint (Chinese or English)")
    parser.add_argument("--genre", default=None, help="Optional genre filter")
    args = parser.parse_args()

    now = datetime.now()
    slot = _time_slot(now.hour)
    pool = _filter(slot, args.genre, args.mood)

    # Hourly determinism: same hour = same pick
    seed = f"{now.strftime('%Y-%m-%d-%H')}|{args.mood or ''}|{args.genre or ''}"
    artist, title, vibe, _slot, _genre = _pick_deterministic(pool, seed)

    url = _spotify_url(f"{artist} {title}")
    suffix = f" :: {url}" if url else ""
    print(f"[now_playing] {artist} — {title} :: {vibe}{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
