#!/usr/bin/env python3
"""List recent selfies from the album and optionally send one back to the channel.

Reads ``~/.herandhim/context/photos/`` via :class:`PhotoAlbum`. Output is a
compact, LLM-narratable list of past moments — each line includes timestamp,
scene/activity, and mood.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, help="Look back this many days (default 14)")
    parser.add_argument("--limit", type=int, default=5, help="Max entries to return (default 5)")
    parser.add_argument("--send", action="store_true",
                        help="Send the most recent matching photo via the active channel.")
    args = parser.parse_args()

    try:
        from herandhim.core.image_gen import PhotoAlbum
    except ImportError as exc:
        print(f"Error: herandhim not importable ({exc})", file=sys.stderr)
        return 1

    album = PhotoAlbum()
    entries = album.recent(days=args.days, kind="selfie")
    if not entries:
        print("(no photos in the last %d days)" % args.days)
        return 0

    # Sort newest first, then cap
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    entries = entries[: args.limit]

    print(f"Looking back at the last {len(entries)} moment(s):")
    for e in entries:
        ts = e.get("timestamp", "?")
        try:
            ts_pretty = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            ts_pretty = ts
        scene = e.get("scene", {}) or {}
        activity = scene.get("activity") or "(unrecorded scene)"
        mood = scene.get("mood") or ""
        weather = scene.get("weather") or ""
        bits = [f"  [{ts_pretty}] {activity}"]
        if mood:
            bits.append(f"mood: {mood}")
        if weather:
            bits.append(f"weather: {weather}")
        print(" — ".join(bits))

    if not args.send:
        return 0

    # Send the most recent one
    target = entries[0]
    path = target.get("path", "")
    if not path:
        print("(no path on most recent entry; cannot send)")
        return 0

    try:
        from herandhim.core.tools import send_photo
        scene = target.get("scene", {}) or {}
        when = scene.get("time") or ""
        activity = scene.get("activity") or ""
        caption_bits = [b for b in (when, activity) if b]
        caption = " · ".join(caption_bits) or "Remember this?"
        result = send_photo(path, caption=caption)
        print(result)
    except Exception as exc:
        print(f"(send failed: {exc})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
