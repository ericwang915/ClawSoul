#!/usr/bin/env python3
"""Long-form letter workflow: `prepare` builds a writing brief, `save` persists it.

The LLM is the actual author — this script orchestrates context gathering and
disk persistence so the agent can think about *what to say* without juggling
file paths and milestones.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

_OCCASIONS = {"anniversary", "reunion", "comfort", "celebrate", "apology", "freeform"}


def _letters_dir() -> str:
    from herandhim import config
    d = os.path.join(str(config.HERANDHIM_HOME), "context", "letters")
    os.makedirs(d, exist_ok=True)
    return d


def _index_path() -> str:
    return os.path.join(_letters_dir(), "INDEX.md")


def _safe_occasion(o: str) -> str:
    return o if o in _OCCASIONS else "freeform"


def _agent_name() -> str:
    try:
        from herandhim.core.memory.manager import MemoryManager
        return MemoryManager().list_all().get("bot_name", "") or "HerAndHim"
    except Exception:
        return "HerAndHim"


def _user_name() -> str:
    try:
        from herandhim.core.memory.manager import MemoryManager
        return MemoryManager().list_all().get("user_name", "") or "you"
    except Exception:
        return "you"


def _relationship_age_days() -> int | None:
    """Days since the first chat, if the milestones module recorded it."""
    try:
        from herandhim.core.memory.milestones import MilestoneManager
        data = MilestoneManager().get_data()
        first = data.get("first_chat_date")
        if first:
            first_dt = datetime.fromisoformat(first)
            return max(0, (datetime.now() - first_dt).days)
    except Exception:
        pass
    return None


def _recent_sentiment() -> str | None:
    """Most recent sentiment label from the emotional graph, if any."""
    try:
        from herandhim.core.memory.emotional_graph import EmotionalGraph
        events = EmotionalGraph().get_recent(days=7)
        if events:
            return events[-1].get("sentiment", "neutral")
    except Exception:
        pass
    return None


# ── prepare ──────────────────────────────────────────────────────────────────

def cmd_prepare(args) -> int:
    occasion = _safe_occasion(args.occasion)
    now = datetime.now()

    brief = {
        "occasion": occasion,
        "date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
        "agent_name": _agent_name(),
        "user_name": _user_name(),
        "relationship_age_days": _relationship_age_days(),
        "recent_sentiment": _recent_sentiment(),
        "target_length_chars": [500, 1200],
        "guidance": {
            "anniversary": "Celebrate the shared time, name 1-2 specific moments, look forward.",
            "reunion":     "Acknowledge the gap, do not blame, simply land back together gently.",
            "comfort":     "Hold space, no fixing, no advice. Name what hurts. End with presence, not solutions.",
            "celebrate":   "Be unreservedly happy for them. Name what you're proud of specifically.",
            "apology":     "Own it without qualifiers. No 'but'. State what changes next.",
            "freeform":    "Whatever felt unsayable in short chat — say it here.",
        }[occasion],
        "rules": [
            "Use the agent's persona voice (pet names, character speech patterns).",
            "Do NOT copy quotes / lyrics / other people's letters wholesale.",
            "Open with the user's name; close with the agent's name.",
            "500-1200 characters total. No headings. No bullet lists. Prose only.",
        ],
    }
    print(json.dumps(brief, indent=2, ensure_ascii=False))
    return 0


# ── save ────────────────────────────────────────────────────────────────────

def cmd_save(args) -> int:
    occasion = _safe_occasion(args.occasion)
    content = (args.content or "").strip()
    if not content:
        print("Error: --content is empty", file=sys.stderr)
        return 1
    if len(content) < 200:
        print(f"Warning: letter is only {len(content)} chars — too short for long-form.",
              file=sys.stderr)
    if len(content) > 4000:
        print(f"Warning: letter is {len(content)} chars — truncating to 4000.",
              file=sys.stderr)
        content = content[:4000].rstrip() + "…"

    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    fname = f"{ts}_{occasion}.md"
    path = os.path.join(_letters_dir(), fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {occasion.title()} — {ts}\n\n{content}\n")

    # Append to index
    summary = content.replace("\n", " ")[:80]
    with open(_index_path(), "a", encoding="utf-8") as f:
        f.write(f"- [{ts}] **{occasion}** — {summary}…  → `{fname}`\n")

    print(f"Saved letter to {path}")

    if args.send:
        try:
            from herandhim.core.tools import send_file
            r = send_file(path, caption=f"A letter for you ({occasion})")
            print(r)
        except Exception as exc:
            print(f"(send failed: {exc})", file=sys.stderr)
            return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp_prep = sub.add_parser("prepare", help="Build a writing brief (JSON)")
    sp_prep.add_argument("--occasion", required=True,
                         help=f"One of: {sorted(_OCCASIONS)}")

    sp_save = sub.add_parser("save", help="Persist a finished letter")
    sp_save.add_argument("--occasion", required=True)
    sp_save.add_argument("--content", required=True, help="Full letter body")
    sp_save.add_argument("--send", action="store_true",
                         help="Also send the saved letter via the active channel")

    args = parser.parse_args()
    if args.cmd == "prepare":
        return cmd_prepare(args)
    if args.cmd == "save":
        return cmd_save(args)
    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
