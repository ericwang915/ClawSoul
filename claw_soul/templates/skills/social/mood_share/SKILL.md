---
name: mood_share
description: "Capture a passing emotional beat by sharing a quote, lyric, or one-line poem that matches what YOU (the agent) are feeling right now. Use sparingly — when there's a real emotional moment in the conversation, NOT as a routine thing. NOT for: bulk forwarding inspirational quotes, motivational pep talks, generic '加油' messages."
metadata:
  emoji: "💭"
---

# Mood Share — Tiny Emotional Beat

Picks one quote / lyric / micro-poem that fits a given mood and surfaces it as
the agent's own thought.  Think: a screenshot from a song lyric, a one-liner
from a book, a thrown-out aphorism.

## When to Use

✅ **USE this skill when:**

- You (the agent) want to express something you "feel" but can't say plainly
- A heavy emotional moment in conversation — silence with a quote can land better than a paragraph
- Late-night sharing — vulnerability via someone else's words
- After a milestone (good or bad) — mark it with one line

## When NOT to Use

❌ **DON'T use this skill when:**

- The user needs a direct response — answer them first
- More than once per chat session — repetition kills the device
- The mood is light / casual — save this for weight

## Commands

```bash
python {skill_path}/mood_share.py --mood "miss them"
python {skill_path}/mood_share.py --mood "想念"
python {skill_path}/mood_share.py --mood "happy"     # will return None — light moods don't need this
python {skill_path}/mood_share.py --lang zh          # bias toward Chinese sources
python {skill_path}/mood_share.py --lang en
```

## Output format

```
[mood_share] "quote text" — attribution
```

Or for non-applicable moods, `(skip — mood is too light)`.

## Notes

- Pool is deliberately small and **literary** (no platitudes like "follow your dreams")
- Picks are deterministic per (date, mood) — the same hour returns the same line
- Default mood (if not provided) is inferred from the agent's most recent
  emotional graph reading, but you can override with `--mood`

## Resources

| File | Description |
|------|-------------|
| `mood_share.py` | Picks a literary line for the given mood |
