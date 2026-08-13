#!/usr/bin/env python3
"""Search across all HerAndHim session files for a keyword."""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Keyword or phrase to search for")
    parser.add_argument("--sessions-dir", default=None,
                        help="Override sessions directory")
    parser.add_argument("--context", "-C", type=int, default=2,
                        help="Lines of context around each match (default: 2)")
    args = parser.parse_args()

    sessions_dir = args.sessions_dir
    if not sessions_dir:
        home = os.environ.get("HERANDHIM_HOME", os.path.expanduser("~/.herandhim"))
        sessions_dir = os.path.join(home, "context", "sessions")

    if not os.path.isdir(sessions_dir):
        print(f"Sessions directory not found: {sessions_dir}", file=sys.stderr)
        sys.exit(1)

    query = args.query.lower()
    results = []

    for md_file in sorted(Path(sessions_dir).glob("*.md")):
        lines = md_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if query in line.lower():
                start = max(0, i - args.context)
                end = min(len(lines), i + args.context + 1)
                snippet = "\n".join(lines[start:end])
                results.append({
                    "file": md_file.name,
                    "line": i + 1,
                    "snippet": snippet,
                })

    if not results:
        print(f"No matches for '{args.query}' in {sessions_dir}")
        return

    print(f"Found {len(results)} match(es) for '{args.query}':\n")
    for r in results:
        print(f"--- {r['file']}:{r['line']} ---")
        print(r["snippet"])
        print()


if __name__ == "__main__":
    main()
