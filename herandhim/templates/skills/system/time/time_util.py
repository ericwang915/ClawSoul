#!/usr/bin/env python3
"""Get current time, convert between timezones."""

import argparse
import json
from datetime import datetime, timedelta, timezone

COMMON_TIMEZONES = {
    "UTC": 0, "US/Eastern": -5, "US/Central": -6, "US/Mountain": -7,
    "US/Pacific": -8, "Europe/London": 0, "Europe/Paris": 1,
    "Europe/Berlin": 1, "Asia/Tokyo": 9, "Asia/Shanghai": 8,
    "Asia/Singapore": 8, "Asia/Kolkata": 5.5, "Australia/Sydney": 11,
    "America/New_York": -5, "America/Chicago": -6,
    "America/Los_Angeles": -8, "America/Sao_Paulo": -3,
}


def _tz_offset(name: str) -> timezone:
    name_lower = name.lower().replace(" ", "_")
    for k, v in COMMON_TIMEZONES.items():
        if k.lower() == name_lower:
            return timezone(timedelta(hours=v))
    try:
        hours = float(name)
        return timezone(timedelta(hours=hours))
    except ValueError:
        pass
    raise ValueError(f"Unknown timezone: {name}. Use --list-tz to see options.")


def main():
    parser = argparse.ArgumentParser(description="Time utility")
    parser.add_argument("--tz", type=str, help="Show time in this timezone")
    parser.add_argument("--list-tz", action="store_true", help="List common timezones")
    parser.add_argument("--unix", action="store_true", help="Show Unix timestamp")
    parser.add_argument("--convert", type=str, help="Datetime string to convert")
    parser.add_argument("--from-tz", type=str, help="Source timezone for conversion")
    parser.add_argument("--to-tz", type=str, help="Target timezone for conversion")
    args = parser.parse_args()

    if args.list_tz:
        for name, offset in sorted(COMMON_TIMEZONES.items()):
            sign = "+" if offset >= 0 else ""
            print(f"  {name:25s} UTC{sign}{offset}")
        return

    if args.unix:
        print(int(datetime.now(timezone.utc).timestamp()))
        return

    if args.convert:
        if not args.from_tz or not args.to_tz:
            print("Error: --convert requires --from-tz and --to-tz")
            return
        src_tz = _tz_offset(args.from_tz)
        dst_tz = _tz_offset(args.to_tz)
        dt = datetime.strptime(args.convert, "%Y-%m-%d %H:%M").replace(tzinfo=src_tz)
        converted = dt.astimezone(dst_tz)
        print(json.dumps({
            "from": f"{args.convert} ({args.from_tz})",
            "to": converted.strftime("%Y-%m-%d %H:%M:%S %Z") + f" ({args.to_tz})",
        }, indent=2))
        return

    if args.tz:
        tz = _tz_offset(args.tz)
        now = datetime.now(tz)
    else:
        now = datetime.now()

    print(json.dumps({
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "timezone": args.tz or "local",
    }, indent=2))


if __name__ == "__main__":
    main()
