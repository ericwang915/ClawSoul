---
name: system_time
description: "Get current date, time, timezone, Unix timestamp, or convert between timezones. Use when: user asks what time it is, what date today, timezone conversion, or Unix timestamp. NOT for: scheduling/calendar logic, cron syntax, or historical date arithmetic."
metadata:
  emoji: "🕐"
---

# System Time Skill

Get current date, time, timezone info, and timezone conversions.

## When to Use

✅ **USE this skill when:**
- "What time is it?"
- "What's the date?"
- "What time is it in Tokyo?"
- "Convert 2pm EST to UTC"
- "Give me a Unix timestamp"
- User needs timezone names or abbreviations

## When NOT to Use

❌ **DON'T use this skill when:**
- Scheduling or calendar logic → use calendar/scheduling tools
- Cron expression help → use cron-specific docs
- Historical date arithmetic (e.g., "days between 1990 and 2000") → compute or use date libraries

## Usage/Commands

```bash
# Current local time
python {skill_path}/time_util.py

# Time in a specific timezone
python {skill_path}/time_util.py --tz "America/New_York"

# List common timezone names
python {skill_path}/time_util.py --list-tz

# Unix timestamp
python {skill_path}/time_util.py --unix

# Convert a time between timezones
python {skill_path}/time_util.py --convert "2026-03-01 14:30" --from-tz "Asia/Shanghai" --to-tz "America/New_York"
```

## Notes

- Uses bundled `time_util.py` CLI for all time queries
- Timezone names follow IANA format (e.g., `America/New_York`, `Asia/Shanghai`)
