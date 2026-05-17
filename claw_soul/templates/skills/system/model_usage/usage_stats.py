#!/usr/bin/env python3
"""Parse and summarise ClawSoul usage from history_detail.jsonl."""
import argparse
import json
import os
import sys
from collections import Counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=None, help="Path to history_detail.jsonl")
    parser.add_argument("--format", default="text", choices=["text", "json"])
    args = parser.parse_args()

    log_path = args.log
    if not log_path:
        home = os.environ.get("CLAWSOUL_HOME", os.path.expanduser("~/.claw_soul"))
        log_path = os.path.join(home, "context", "logs", "history_detail.jsonl")

    if not os.path.isfile(log_path):
        print(f"Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    total = len(entries)
    tool_calls = Counter()
    dates = Counter()

    for entry in entries:
        ts = entry.get("timestamp", "")
        if ts:
            day = ts[:10]
            dates[day] += 1

        for tc in entry.get("tool_calls", []):
            name = tc.get("function", {}).get("name", "unknown")
            tool_calls[name] += 1

    stats = {
        "total_interactions": total,
        "unique_days": len(dates),
        "top_tools": dict(tool_calls.most_common(10)),
        "daily_breakdown": dict(sorted(dates.items(), reverse=True)[:7]),
        "log_file": log_path,
    }

    if args.format == "json":
        print(json.dumps(stats, indent=2))
    else:
        print("ClawSoul Usage Statistics")
        print(f"{'=' * 40}")
        print(f"Total interactions: {stats['total_interactions']}")
        print(f"Active days: {stats['unique_days']}")
        print("\nTop tools:")
        for name, count in tool_calls.most_common(10):
            print(f"  {name}: {count}")
        print("\nRecent daily activity:")
        for day, count in sorted(dates.items(), reverse=True)[:7]:
            print(f"  {day}: {count} interactions")


if __name__ == "__main__":
    main()
