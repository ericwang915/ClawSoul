#!/usr/bin/env python3
"""Take an in-character selfie via Seedream and send through the active channel."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hint", default=None, help="Extra scene description")
    parser.add_argument("--model", default=None, help="Override Seedream model id")
    parser.add_argument("--no-send", action="store_true",
                        help="Just generate and print the path, do not send")
    args = parser.parse_args()

    try:
        from claw_soul.core.image_gen import take_selfie
        from claw_soul.core.tools import send_photo
    except ImportError as exc:
        print(f"Error: claw_soul not importable ({exc})", file=sys.stderr)
        return 1

    try:
        result = take_selfie(scene_hint=args.hint, model=args.model)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Selfie saved: {result.path}")
    if result.scene.activity:
        print(f"Scene: {result.scene.activity}")

    if args.no_send:
        return 0

    # Send the photo WITHOUT a caption — the agent's text reply (which
    # follows this tool call) becomes the single voiceover. Baking a caption
    # in here produces two duplicated messages around the photo in chat.
    send_result = send_photo(result.path, caption="")
    print(send_result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
